"""
tailorer.py — Assembles cv-library context, calls the LLM, and renders to PDF.

Pipeline:
  1. Read JD (file or text)
  2. Assemble full cv-library context (library FIRST, JD LAST)
  3. Optionally inject per-application generation notes before the JD
  4. Call LLM — produces tailored CV Markdown
  5. Extract reasoning if present in output (model misfired) — save to reasoning.md
  6. Write output folder (jd.txt, cv.md, reasoning.md, run_meta.json)
  7. Render cv.md → cv.pdf via Typst (if RENDER_PDF is enabled)

Returns:
  (output_dir, cv_markdown, reasoning) — reasoning is empty string if none captured
"""

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from pipeline.config import (
    EXPERIENCE_PATH, PERSONAS_PATH, SKILLS_PATH, META_PATH,
    OUTPUT_PATH, PROMPTS_PATH, LLM_PROVIDER, LLM_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY, TEMPERATURE,
    MAX_OUTPUT_TOKENS, THINKING_BUDGET, ENABLE_CACHING, RENDER_PDF,
)

log = logging.getLogger(__name__)


# ── Library Loaders ───────────────────────────────────────────────────────────

def load_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    log.warning(f"Expected file not found: {path}")
    return ""


def load_experience_files() -> str:
    files = sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True)
    if not files:
        log.warning(f"No experience files found in {EXPERIENCE_PATH}")
    return "\n\n---\n\n".join(load_text(f) for f in files)


def load_persona_files() -> str:
    files = sorted(PERSONAS_PATH.glob("*.md"))
    return "\n\n---\n\n".join(
        f"## PERSONA: {f.stem}\n\n{load_text(f)}" for f in files
    )


def load_personal_additions() -> str:
    path = PROMPTS_PATH / "personal_additions.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        log.info("Personal additions loaded.")
        return content
    return ""


def build_system_prompt() -> str:
    base      = load_text(PROMPTS_PATH / "cv_prompt.md")
    additions = load_personal_additions()
    if additions:
        return f"{base}\n\n---\n\n## PERSONAL ADDITIONS\n\n{additions}"
    return base


def assemble_user_message(jd_text: str, generation_notes: Optional[str] = None) -> str:
    notes_block = ""
    if generation_notes and generation_notes.strip():
        notes_block = f"""
---

## APPLICANT NOTES FOR THIS APPLICATION
<!-- Personal guidance from the applicant about this specific role.
     Apply these instructions with the same weight as PERSONAL ADDITIONS.
     These notes are not for inclusion in the CV — they are guidance only. -->

{generation_notes.strip()}
"""

    return f"""## META
{load_text(META_PATH)}

---

## PERSONAS
{load_persona_files()}

---

## EXPERIENCE LIBRARY
{load_experience_files()}

---

## SKILLS INVENTORY
{load_text(SKILLS_PATH)}
{notes_block}
---

## JOB DESCRIPTION
{jd_text}
"""


# ── Reasoning extraction ──────────────────────────────────────────────────────

def extract_reasoning_and_cv(raw_output: str) -> tuple[str, str]:
    """
    Separate reasoning from CV in the LLM output.

    When extended thinking is working correctly, the model outputs only the CV
    and reasoning is empty. When it misfires (outputs reasoning as text), this
    function detects the pattern and splits the output cleanly.

    Detection: if the output contains "## STEP" headers before the first H1,
    the content before the CV heading is reasoning.

    Returns:
        (reasoning, cv_markdown)
    """
    # Find the first top-level heading that looks like a name (# Firstname ...)
    # This marks the start of the actual CV
    cv_start_match = re.search(r'^# [A-Z][a-zA-Z]', raw_output, re.MULTILINE)

    if cv_start_match and cv_start_match.start() > 0:
        reasoning_candidate = raw_output[:cv_start_match.start()].strip()
        cv_candidate        = raw_output[cv_start_match.start():].strip()

        # Only treat as reasoning if it actually contains step content
        if re.search(r'## STEP\s+\d', reasoning_candidate, re.IGNORECASE):
            log.warning(
                "Model output included reasoning as text — extracting and saving separately. "
                "This indicates extended thinking may not be active."
            )
            return reasoning_candidate, cv_candidate

    # Normal case: no reasoning in output
    return "", raw_output.strip()


# ── LLM Client ────────────────────────────────────────────────────────────────

def call_llm(system: str, user: str) -> str:
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(system, user)
    elif LLM_PROVIDER == "openai":
        return _call_openai(system, user)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")


def _call_anthropic(system: str, user: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_content = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if ENABLE_CACHING else system
    )

    extra_kwargs: dict = {}
    if THINKING_BUDGET > 0:
        extra_kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}
        effective_temperature    = 1
        log.info(f"Extended thinking enabled — budget: {THINKING_BUDGET} tokens")
    else:
        effective_temperature = TEMPERATURE

    total_max_tokens = MAX_OUTPUT_TOKENS + (THINKING_BUDGET if THINKING_BUDGET > 0 else 0)

    message = client.messages.create(
        model=LLM_MODEL,
        max_tokens=total_max_tokens,
        temperature=effective_temperature,
        system=system_content,
        messages=[{"role": "user", "content": user}],
        **extra_kwargs,
    )

    # Capture extended thinking content if present (for future logging)
    thinking_text = "".join(
        block.thinking for block in message.content
        if hasattr(block, 'thinking') and block.thinking
    )
    if thinking_text:
        log.info(f"Extended thinking captured: {len(thinking_text)} chars")

    output_text = "".join(
        block.text for block in message.content if block.type == "text"
    )
    log.info(
        f"Usage — input: {message.usage.input_tokens}, "
        f"output: {message.usage.output_tokens} tokens"
    )
    return output_text


def _call_openai(system: str, user: str) -> str:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=TEMPERATURE,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return response.choices[0].message.content


# ── Output Writer ─────────────────────────────────────────────────────────────

def derive_output_name(jd_path: Path) -> tuple[str, str]:
    stem  = jd_path.stem.lower().replace(" ", "-")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return stem, "unknown-role"


def write_output(
    jd_path:     Path,
    jd_text:     str,
    cv_markdown: str,
    reasoning:   str = "",
) -> Path:
    company, role = derive_output_name(jd_path)
    today         = date.today().isoformat()
    out_dir       = OUTPUT_PATH / f"{today}_{company}_{role}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "jd.txt").write_text(jd_text,   encoding="utf-8")
    (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")

    if reasoning:
        (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")
        log.info("Reasoning saved to reasoning.md")

    (out_dir / "run_meta.json").write_text(
        json.dumps({
            "jd_file":         jd_path.name,
            "model":           LLM_MODEL,
            "provider":        LLM_PROVIDER,
            "temperature":     TEMPERATURE,
            "thinking_budget": THINKING_BUDGET,
            "caching":         ENABLE_CACHING,
            "render_pdf":      RENDER_PDF,
            "generated_at":    today,
            "has_reasoning":   bool(reasoning),
        }, indent=2),
        encoding="utf-8",
    )
    log.info(f"Output written to: {out_dir}")
    return out_dir


# ── Entry Point ───────────────────────────────────────────────────────────────

def process_jd(
    jd_path:          Path,
    generation_notes: Optional[str] = None,
) -> tuple[Path, str, str]:
    """
    Full pipeline. Returns (output_dir, cv_markdown, reasoning).
    reasoning is empty string when extended thinking worked correctly.
    """
    log.info(f"Processing: {jd_path.name}")
    jd_text    = jd_path.read_text(encoding="utf-8")
    system     = build_system_prompt()
    user       = assemble_user_message(jd_text, generation_notes)
    raw_output = call_llm(system, user)

    reasoning, cv_markdown = extract_reasoning_and_cv(raw_output)

    output_dir = write_output(jd_path, jd_text, cv_markdown, reasoning)
    log.info(f"CV Markdown: {output_dir / 'cv.md'}")

    if RENDER_PDF:
        try:
            from pipeline.render import render_cv
            render_cv(output_dir / "cv.md", output_dir)
        except Exception as e:
            log.error(f"PDF rendering failed: {e}")

    return output_dir, cv_markdown, reasoning
