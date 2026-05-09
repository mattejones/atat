"""
tailorer.py — Assembles cv-library context, calls the LLM, returns structured CV data.

Pipeline:
  1. Assemble library context (META, PERSONAS, EXPERIENCE, SKILLS) + JD
  2. Call LLM — model outputs a JSON object as text (with optional extended thinking)
  3. Parse and validate JSON — raise immediately on failure, no silent fallback
  4. Convert dict -> canonical Markdown (cv_to_markdown) and ParsedCV (cv_data_to_parsed)
  5. Write output folder: jd.txt, cv.md, reasoning.md (if present), run_meta.json
  6. Render cv.md -> cv.pdf via Typst (if RENDER_PDF is enabled)

call_llm() returns a validated dict.
Callers extract reasoning and pass remaining data to cv_to_markdown() / cv_data_to_parsed().

System prompt loaded from prompts/cv_generation.md at call time.
Personal additions loaded from prompts/personal_additions.md (gitignored, optional).
"""

import json
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

from pipeline.config import (
    EXPERIENCE_PATH, PERSONAS_PATH, SKILLS_PATH, META_PATH,
    OUTPUT_PATH, PROMPTS_PATH, LLM_PROVIDER, LLM_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY, TEMPERATURE,
    MAX_OUTPUT_TOKENS, THINKING_BUDGET, ENABLE_CACHING, RENDER_PDF,
)
from pipeline.parse_cv import ParsedCV, ExperienceEntry, EducationEntry

log = logging.getLogger(__name__)

_REQUIRED_FIELDS = {
    "reasoning", "name", "contact", "profile",
    "experience", "skills", "education", "certifications",
}


# ── Text sanitisation ─────────────────────────────────────────────────────────

def sanitise_text(text: str) -> str:
    """Normalise unicode to ASCII-safe characters."""
    if not text:
        return text
    normalised = unicodedata.normalize("NFKD", text)
    return normalised.encode("ascii", "ignore").decode("ascii")


# ── Library loaders ───────────────────────────────────────────────────────────

def load_text(path: Path) -> str:
    """Read a file and sanitise to ASCII. Covers library files and prompts."""
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        return sanitise_text(raw)
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
        content = load_text(path)
        log.info("Personal additions loaded.")
        return content
    return ""


def build_system_prompt() -> str:
    base      = load_text(PROMPTS_PATH / "cv_generation.md")
    additions = load_personal_additions()
    if additions:
        return f"{base}\n\n---\n\n## PERSONAL ADDITIONS\n\n{additions}"
    return base


def assemble_user_message(jd_text: str, generation_notes: Optional[str] = None) -> str:
    jd_text = sanitise_text(jd_text)
    if generation_notes:
        generation_notes = sanitise_text(generation_notes)

    notes_block = ""
    if generation_notes and generation_notes.strip():
        notes_block = f"""
---

## APPLICANT NOTES FOR THIS APPLICATION
<!-- Personal guidance from the applicant about this specific role.
     Apply with equal weight to PERSONAL ADDITIONS.
     Do not include in the CV itself. -->

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


# ── JSON parsing and validation ───────────────────────────────────────────────

def parse_llm_response(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model returned invalid JSON: {e}\n"
            f"First 300 chars of response: {cleaned[:300]}"
        )

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"JSON response missing required fields: {sorted(missing)}")

    return data


# ── LLM clients ───────────────────────────────────────────────────────────────

def call_llm(system: str, user: str) -> dict:
    if LLM_PROVIDER == "anthropic":
        raw = _call_anthropic(system, user)
    elif LLM_PROVIDER == "openai":
        raw = _call_openai(system, user)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")
    return parse_llm_response(raw)


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

    log.info(
        f"Usage — input: {message.usage.input_tokens}, "
        f"output: {message.usage.output_tokens} tokens"
    )

    text = "".join(
        block.text for block in message.content if block.type == "text"
    )

    if not text.strip():
        raise ValueError("Model returned no text content.")

    return text


def _call_openai(system: str, user: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=TEMPERATURE,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=MAX_OUTPUT_TOKENS,
        response_format={"type": "json_object"},
    )

    return response.choices[0].message.content


# ── Structured -> Markdown ────────────────────────────────────────────────────

def cv_to_markdown(cv_data: dict) -> str:
    lines = []
    lines.append(f"# {cv_data.get('name', '')}")
    contact = cv_data.get('contact', {})
    contact_parts = [
        contact.get('email', ''),
        contact.get('phone', ''),
        contact.get('location', ''),
        contact.get('linkedin', ''),
    ]
    lines.append(' · '.join(p for p in contact_parts if p))
    lines += ['', '---', '']

    lines += ['## Profile', '', cv_data.get('profile', ''), '', '---', '']

    lines += ['## Experience', '']
    for exp in cv_data.get('experience', []):
        lines.append(
            f"### {exp.get('company', '')} -- {exp.get('role', '')} | {exp.get('dates', '')}"
        )
        lines.append('')
        if exp.get('context'):
            lines.append(f"*{exp['context']}*")
            lines.append('')
        for bullet in exp.get('bullets', []):
            lines.append(f"- {bullet}")
        lines += ['', '---', '']

    earlier = cv_data.get('earlier_experience', '')
    if earlier:
        lines += ['### Earlier technical experience', '', earlier, '', '---', '']

    lines += ['## Skills', '']
    for skill in cv_data.get('skills', []):
        lines.append(f"**{skill.get('category', '')}:** {skill.get('items', '')}")
    lines += ['', '---', '']

    lines += ['## Education', '']
    for edu in cv_data.get('education', []):
        lines.append(
            f"**{edu.get('degree', '')}** -- {edu.get('institution', '')}, {edu.get('years', '')}"
        )
        if edu.get('subjects'):
            lines.append(f"*{edu['subjects']}*")
        lines.append('')
    lines += ['---', '']

    lines += ['## Certifications', '']
    for cert in cv_data.get('certifications', []):
        lines.append(f"- {cert}")

    return '\n'.join(lines)


# ── Structured -> ParsedCV ────────────────────────────────────────────────────

def cv_data_to_parsed(cv_data: dict) -> ParsedCV:
    contact = cv_data.get('contact', {})
    contact_str = ' · '.join(filter(None, [
        contact.get('email', ''),
        contact.get('phone', ''),
        contact.get('location', ''),
        contact.get('linkedin', ''),
    ]))

    experience: list[ExperienceEntry] = []
    for e in cv_data.get('experience', []):
        experience.append(ExperienceEntry(
            company=e.get('company', ''),
            role=e.get('role', ''),
            dates=e.get('dates', ''),
            context=e.get('context'),
            bullets=e.get('bullets', []),
        ))

    earlier = cv_data.get('earlier_experience', '')
    if earlier:
        experience.append(ExperienceEntry(
            company='Earlier technical experience',
            role='', dates='', context=None,
            bullets=[earlier],
        ))

    return ParsedCV(
        name=cv_data.get('name', ''),
        contact=contact_str,
        profile=cv_data.get('profile', ''),
        experience=experience,
        skills=[
            (s.get('category', ''), s.get('items', ''))
            for s in cv_data.get('skills', [])
        ],
        education=[
            EducationEntry(
                degree=e.get('degree', ''),
                institution=e.get('institution', ''),
                years=e.get('years', ''),
                subjects=e.get('subjects'),
            )
            for e in cv_data.get('education', [])
        ],
        certifications=cv_data.get('certifications', []),
    )


# ── Output writer ─────────────────────────────────────────────────────────────

def derive_output_name(jd_path: Path) -> tuple[str, str]:
    stem  = jd_path.stem.lower().replace(" ", "-")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return stem, "unknown-role"


def write_output(jd_path: Path, jd_text: str, cv_markdown: str, reasoning: str = "") -> Path:
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


# ── Entry point ───────────────────────────────────────────────────────────────

def process_jd(jd_path: Path, generation_notes: Optional[str] = None) -> tuple[Path, str, str]:
    """Full pipeline. Returns (output_dir, cv_markdown, reasoning)."""
    log.info(f"Processing: {jd_path.name}")
    jd_text = jd_path.read_text(encoding="utf-8")
    system  = build_system_prompt()
    user    = assemble_user_message(jd_text, generation_notes)

    cv_data   = call_llm(system, user)
    reasoning = cv_data.pop("reasoning", "")

    cv_markdown = cv_to_markdown(cv_data)
    output_dir  = write_output(jd_path, jd_text, cv_markdown, reasoning)
    log.info(f"CV Markdown: {output_dir / 'cv.md'}")

    if RENDER_PDF:
        try:
            from pipeline.render import render_cv
            render_cv(output_dir / "cv.md", output_dir)
        except Exception as e:
            log.error(f"PDF rendering failed: {e}")

    return output_dir, cv_markdown, reasoning
