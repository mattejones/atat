"""
tailorer.py — Assembles cv-library context, calls the LLM, and renders to PDF.

Pipeline:
  1. Read JD file
  2. Assemble full cv-library context (library FIRST, JD LAST)
  3. Call LLM with extended thinking — produces tailored CV Markdown
  4. Write output folder (jd.txt, cv.md, run_meta.json)
  5. Render cv.md → cv.pdf via Typst (if RENDER_PDF is enabled)
"""

import json
import logging
from datetime import date
from pathlib import Path

from pipeline.config import (
    EXPERIENCE_PATH,
    PERSONAS_PATH,
    SKILLS_PATH,
    META_PATH,
    OUTPUT_PATH,
    PROMPTS_PATH,
    LLM_PROVIDER,
    LLM_MODEL,
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    THINKING_BUDGET,
    ENABLE_CACHING,
    RENDER_PDF,
)

log = logging.getLogger(__name__)


# ── Library Loaders ───────────────────────────────────────────────────────────

def load_text(path: Path) -> str:
    """Read a file and return its contents, or empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    log.warning(f"Expected file not found: {path}")
    return ""


def load_experience_files() -> str:
    """Load all experience Markdown files, newest first."""
    files = sorted(EXPERIENCE_PATH.glob("*.md"), reverse=True)
    if not files:
        log.warning(f"No experience files found in {EXPERIENCE_PATH}")
    return "\n\n---\n\n".join(load_text(f) for f in files)


def load_persona_files() -> str:
    """Load all persona Markdown files."""
    files = sorted(PERSONAS_PATH.glob("*.md"))
    return "\n\n---\n\n".join(
        f"## PERSONA: {f.stem}\n\n{load_text(f)}" for f in files
    )


def load_personal_additions() -> str:
    """
    Load optional personal prompt additions from a gitignored local file.
    Returns empty string if file does not exist — not an error.
    """
    path = PROMPTS_PATH / "personal_additions.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        log.info("Personal additions loaded.")
        return content
    log.info("No personal additions file found — continuing without.")
    return ""


def build_system_prompt() -> str:
    """
    Assemble the full system prompt from cv_prompt.md and optional
    personal additions.
    """
    base      = load_text(PROMPTS_PATH / "cv_prompt.md")
    additions = load_personal_additions()
    if additions:
        return f"{base}\n\n---\n\n## PERSONAL ADDITIONS\n\n{additions}"
    return base


def assemble_user_message(jd_text: str) -> str:
    """
    Build the user message: library context FIRST, JD LAST.

    Placing the source library before the JD reduces over-indexing on JD
    language and grounds the model in the candidate's actual experience
    before it encounters role requirements.
    """
    return f"""## META
<!-- Contact info, constraints, DO NOT INCLUDE rules — apply all without exception -->

{load_text(META_PATH)}

---

## PERSONAS
<!-- Review all personas. You will select the best fit in your reasoning steps. -->

{load_persona_files()}

---

## EXPERIENCE LIBRARY
<!-- Full role history. Every claim in the CV must be sourced from this section. -->

{load_experience_files()}

---

## SKILLS INVENTORY
<!-- Use this to select skills to include. Do not add skills not present here. -->

{load_text(SKILLS_PATH)}

---

## JOB DESCRIPTION
<!-- Read this LAST. Use it to understand what to foreground — not as a template to mirror.
     Do not introduce any term, technology, or concept from this section that is not
     already present in the library above. -->

{jd_text}
"""


# ── LLM Client ────────────────────────────────────────────────────────────────

def call_llm(system: str, user: str) -> str:
    """Route to configured LLM provider."""
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

    extra_kwargs = {}
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

    output_text = "".join(
        block.text for block in message.content if block.type == "text"
    )

    log.info(
        f"Usage — input: {message.usage.input_tokens} tokens, "
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
    """
    Derive company and role slugs from the JD filename.
    Convention: company_role-title.txt
    Falls back to filename stem if convention not followed.
    """
    stem  = jd_path.stem.lower().replace(" ", "-")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return stem, "unknown-role"


def write_output(jd_path: Path, jd_text: str, cv_markdown: str) -> Path:
    """Write JD, CV Markdown, and run metadata to a dated output folder."""
    company, role = derive_output_name(jd_path)
    today         = date.today().isoformat()
    out_dir       = OUTPUT_PATH / f"{today}_{company}_{role}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "jd.txt").write_text(jd_text,      encoding="utf-8")
    (out_dir / "cv.md").write_text(cv_markdown,    encoding="utf-8")
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
        }, indent=2),
        encoding="utf-8",
    )

    log.info(f"Output written to: {out_dir}")
    return out_dir


# ── Entry Point ───────────────────────────────────────────────────────────────

def process_jd(jd_path: Path) -> Path:
    """
    Full pipeline:
      1. Read JD
      2. Assemble context + call LLM
      3. Write Markdown output
      4. Render to PDF (if RENDER_PDF is enabled)
    """
    log.info(f"Processing: {jd_path.name}")

    jd_text    = jd_path.read_text(encoding="utf-8")
    system     = build_system_prompt()
    user       = assemble_user_message(jd_text)

    log.info(f"Calling LLM ({LLM_PROVIDER} / {LLM_MODEL})...")
    cv_markdown = call_llm(system, user)

    output_dir = write_output(jd_path, jd_text, cv_markdown)
    log.info(f"CV Markdown: {output_dir / 'cv.md'}")

    if RENDER_PDF:
        try:
            from pipeline.render import render_cv
            pdf_path = render_cv(output_dir / "cv.md", output_dir)
            log.info(f"PDF rendered: {pdf_path.name}")
        except Exception as e:
            log.error(f"PDF rendering failed: {e} — Markdown CV is still available.")
    else:
        log.info("PDF rendering disabled (RENDER_PDF=false).")

    return output_dir
