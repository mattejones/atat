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
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from pipeline.config import (
    REPO_ROOT,
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

# Characters with no ASCII decomposition under NFKD get silently deleted by the
# encode("ascii", "ignore") step below — not replaced, deleted. That's fine when the
# source has spaces on both sides of the character (e.g. " – "), but ad copy commonly
# sets em/en dashes with no surrounding spaces ("levels—from"), and a silent delete
# there merges two words into one garbled token ("levelsfrom"). That garbled token then
# goes into both the LLM's prompt and, in jd_spec.py, the verbatim-quote validation
# haystack — but an extraction model asked to "quote verbatim" will reflexively repair
# the nonsense word when it writes its answer, so its quote no longer matches the
# haystack it was genuinely copied from. Mapping these explicitly, before the NFKD/ignore
# pass, avoids deleting anything without leaving a sane ASCII stand-in behind.
_UNICODE_REPLACEMENTS = {
    "\u2014": " - ",  # em dash
    "\u2013": "-",    # en dash
    "\u2018": "'", "\u2019": "'",   # smart single quotes
    "\u201c": '"', "\u201d": '"',   # smart double quotes
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",    # non-breaking space
}


def sanitise_text(text: str) -> str:
    """Normalise unicode to ASCII-safe characters."""
    if not text:
        return text
    for orig, repl in _UNICODE_REPLACEMENTS.items():
        text = text.replace(orig, repl)
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


def assemble_user_message(
    jd_text: str,
    generation_notes: Optional[str] = None,
    jd_spec: Optional[dict] = None,
) -> str:
    """
    Assemble the tailoring prompt.

    jd_spec, when present, is the load-bearing brief: a structured, human-reviewed
    extraction of the ad's requirements and anti-patterns, each mapped to real library
    evidence (or explicitly marked as a gap). It goes FIRST, before the free-text ad,
    because it is the thing the CV will actually be judged against by tier 3.

    generation_notes remain, and still matter. They carry what a spec structurally cannot:
    context about the reader, standing sensitivities, the fact that a former colleague
    will see this and knows exactly what you did. Notes are guidance; the spec is the brief.
    When they conflict, the notes win — they come from the applicant, the spec came from a
    model.
    """
    jd_text = sanitise_text(jd_text)
    if generation_notes:
        generation_notes = sanitise_text(generation_notes)

    spec_block = ""
    if jd_spec and jd_spec.get("requirements"):
        from pipeline.jd_spec import spec_to_prompt_block
        spec_block = f"""
---

## THE BRIEF FOR THIS APPLICATION
<!-- Structured extraction of the job ad below, reviewed and approved by the applicant.
     Every quote is verbatim from the ad. Every evidence reference points at a real
     achievement in the experience library.

     The CV you write will be judged against this, requirement by requirement.

     Where a requirement is marked as having NO EVIDENCE: leave it uncovered. Do not
     reach for something adjacent. An honest gap costs less than a stretch that gets
     found out in an interview. -->

{spec_to_prompt_block(jd_spec)}
"""

    notes_block = ""
    if generation_notes and generation_notes.strip():
        notes_block = f"""
---

## APPLICANT NOTES FOR THIS APPLICATION
<!-- Personal guidance from the applicant about this specific role.
     Apply with equal weight to PERSONAL ADDITIONS.
     These override the brief above where they conflict.
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
{spec_block}{notes_block}
---

## JOB DESCRIPTION
{jd_text}
"""


# ── JSON parsing and validation ───────────────────────────────────────────────

LOG_DIR = REPO_ROOT / "logs" / "cv_generation_failures"


def _log_generation_failure(raw: str, error: str) -> Path:
    """
    Persist the full, untruncated raw model response on a parse failure.

    Same rationale as jd_spec.py's _log_extraction_failure: generate_cv responses are
    large (reasoning field plus the full CV), so a fixed-length preview in the exception
    message is close to useless for diagnosing where a JSON-formatting failure actually
    occurred. Write the whole thing to disk instead.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = LOG_DIR / f"{ts}.txt"
    path.write_text(f"ERROR: {error}\n\n{'-' * 20} RAW RESPONSE {'-' * 20}\n\n{raw}", encoding="utf-8")
    return path


def parse_llm_response(raw: str) -> dict:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log_path = _log_generation_failure(raw, str(e))
        raise ValueError(
            f"Model returned invalid JSON: {e}\n"
            f"Full response ({len(raw)} chars) logged to: {log_path}"
        )

    if not isinstance(data, dict):
        log_path = _log_generation_failure(raw, f"Expected JSON object, got {type(data).__name__}")
        raise ValueError(
            f"Expected JSON object, got {type(data).__name__}\n"
            f"Full response ({len(raw)} chars) logged to: {log_path}"
        )

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"JSON response missing required fields: {sorted(missing)}")

    # Contact sub-schema. Checked here rather than trusted, because a missing contact key
    # is invisible downstream: compose_cv_markdown builds the header from a fixed list and
    # silently drops anything it does not know about. The personal website went missing from
    # CVs for months exactly this way, despite a standing "no exceptions" rule requiring it.
    # An empty header field produces no error, no warning, and no output. So: fail loudly.
    contact = data.get("contact")
    if not isinstance(contact, dict):
        raise ValueError(f"contact must be an object, got {type(contact).__name__}")

    missing_contact = [
        k for k in ("email", "location", "website")
        if not str(contact.get(k, "")).strip()
    ]
    if missing_contact:
        raise ValueError(
            f"contact is missing required field(s): {missing_contact}. "
            "These are populated from META's Contact Information and must always be present."
        )

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


def _stream_message(client, **kwargs):
    """
    Wraps client.messages.stream() and returns the final Message — same shape as
    client.messages.create()'s return value (usage, content blocks, etc.), so callers
    don't need to change.

    Required because Anthropic's SDK refuses non-streaming requests whose max_tokens
    makes a generation longer than 10 minutes possible, and a full CV generation with
    a large thinking budget is exactly that territory.
    """
    with client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


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

    base_kwargs = dict(
        model=LLM_MODEL,
        max_tokens=total_max_tokens,
        system=system_content,
        messages=[{"role": "user", "content": user}],
        **extra_kwargs,
    )

    def _is_temperature_deprecated(err: Exception) -> bool:
        s = str(err).lower()
        return "temperature" in s and "deprecated" in s

    def _is_thinking_enabled_unsupported(err: Exception) -> bool:
        s = str(err).lower()
        return "thinking.type.enabled" in s or ("thinking" in s and "adaptive" in s)

    try:
        message = _stream_message(client, temperature=effective_temperature, **base_kwargs)
    except anthropic.BadRequestError as e:
        if _is_temperature_deprecated(e):
            # Some newer models reject an explicit `temperature` value outright
            # ("temperature is deprecated for this model") instead of just
            # ignoring it — fall back to the model's default sampling rather
            # than hard-failing generation against that model.
            log.warning(
                f"Model {LLM_MODEL} does not accept `temperature` — retrying without it."
            )
            message = _stream_message(client, **base_kwargs)
        elif _is_thinking_enabled_unsupported(e):
            # Some newer models (e.g. claude-sonnet-5) have retired the
            # thinking.type="enabled" + budget_tokens shape in favour of
            # thinking.type="adaptive" + output_config.effort. Retry using
            # that shape instead of hard-failing generation.
            log.warning(
                f"Model {LLM_MODEL} does not accept thinking.type='enabled' — "
                "retrying with adaptive thinking (output_config.effort='high')."
            )
            adaptive_kwargs = dict(base_kwargs)
            if THINKING_BUDGET > 0:
                adaptive_kwargs["thinking"] = {"type": "adaptive"}
                adaptive_kwargs["output_config"] = {"effort": "high"}
            try:
                message = _stream_message(client, temperature=effective_temperature, **adaptive_kwargs)
            except anthropic.BadRequestError as e2:
                if _is_temperature_deprecated(e2):
                    message = _stream_message(client, **adaptive_kwargs)
                else:
                    raise
        else:
            raise

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
        contact.get('website', ''),
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
        contact.get('website', ''),
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
