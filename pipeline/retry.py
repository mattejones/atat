"""
retry.py — Section-level regeneration with constraint injection for ATAT.

Called by the review route when a retry is triggered. Takes the active flags
and user comment from a failed report and regenerates only the target section,
injecting the failure context as explicit constraints into the prompt.

Key differences from initial generation:
  - Uses RETRY_MODEL (Haiku by default) not LLM_MODEL — retries are targeted
    edits with explicit constraints, not open-ended generation.
  - No thinking budget — extended reasoning is unnecessary for constrained edits.
  - Uses a lightweight, section-specific system prompt — NOT the cv_prompt.md
    which instructs the model to return a full JSON object. The retry model must
    return raw section text only, so it must receive a different system prompt.
  - The user message provides the full source context alongside the previous
    output and constraint block, so the model has everything it needs.
"""

import logging
from typing import Optional

from pipeline.config import (
    LLM_PROVIDER, RETRY_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    TEMPERATURE, MAX_OUTPUT_TOKENS,
    META_PATH, SKILLS_PATH,
)
from pipeline.tailorer import (
    load_text, load_experience_files, load_persona_files,
)

log = logging.getLogger(__name__)


# ── Section-specific return instructions ──────────────────────────────────────

_SECTION_INSTRUCTIONS: dict[str, str] = {
    "profile": (
        "Return ONLY the profile paragraph as plain prose. "
        "A single concise paragraph. No heading, no bullet points, no markdown formatting."
    ),
    "experience": (
        "Return ONLY the experience section content using this exact markdown format: "
        "### Company -- Role | Dates, an optional italic context line, "
        "then bullet points starting with -. "
        "No top-level heading. No JSON. No preamble."
    ),
    "skills": (
        "Return ONLY the skills section content. "
        "One line per category: **Category:** items. "
        "No top-level heading. No JSON. No preamble."
    ),
    "education": (
        "Return ONLY the education section content using the same format as the original. "
        "No top-level heading. No JSON. No preamble."
    ),
    "certifications": (
        "Return ONLY the certifications as a bullet list starting with -. "
        "No top-level heading. No JSON. No preamble."
    ),
}

_DEFAULT_SECTION_INSTRUCTION = (
    "Return ONLY the raw section content. No JSON, no headings, no preamble."
)

# ── System prompt ─────────────────────────────────────────────────────────────
# Deliberately minimal — no JSON schema, no CV generation rules.
# The model must return plain text for the target section only.

_SYSTEM_PROMPT = """You are an expert CV writer performing a targeted section edit.

You will be given:
- Source profile material (factual reference)
- A job description
- The previous version of a CV section
- A list of specific issues to fix
- Optional reviewer feedback

Your task is to rewrite the section addressing every listed issue precisely.

Rules:
- Return ONLY the section content — no JSON, no wrapper, no explanation, no preamble
- Do not use em dashes (— or –); use hyphens (-) or rewrite the sentence
- Do not use AI filler words (leverage, spearhead, seamlessly, robust, etc.)
- Keep sentences under 35 words; bullet points under 35 words
- All claims must be grounded in the source profile material
- Do not begin with "I"
- Write in the third person implied (no subject pronoun)
"""


# ── Constraint block builder ──────────────────────────────────────────────────

def build_constraint_block(
    active_flags:   list[dict],
    global_comment: Optional[str] = None,
) -> str:
    """
    Build the constraint block injected into the retry prompt.

    Takes a list of active flag dicts (type, excerpt, message).
    Dismissed flags must be excluded by the caller before passing.
    """
    lines: list[str] = [
        "## ISSUES WITH THE PREVIOUS VERSION",
        "",
        "Regenerate the section addressing every issue listed below:",
        "",
    ]

    for i, flag in enumerate(active_flags, 1):
        flag_type = flag.get("type", "unknown").replace("_", " ").upper()
        message   = flag.get("message", "")
        excerpt   = flag.get("excerpt", "")

        lines.append(f"{i}. [{flag_type}] {message}")
        if excerpt and flag.get("type") not in ("readability",):
            lines.append(f'   Problematic text: "{excerpt}"')
        lines.append("")

    if global_comment and global_comment.strip():
        lines += [
            "## ADDITIONAL FEEDBACK FROM REVIEWER",
            "",
            global_comment.strip(),
            "",
        ]

    return "\n".join(lines)


# ── LLM callers ───────────────────────────────────────────────────────────────
# No thinking budget — retries are constrained edits, not generative reasoning.

def _call_anthropic(user: str) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=RETRY_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in message.content if b.type == "text")
    return text.strip(), message.usage.input_tokens, message.usage.output_tokens


def _call_openai(user: str) -> tuple[str, int, int]:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=RETRY_MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user},
        ],
    )
    content = response.choices[0].message.content.strip()
    usage   = response.usage
    return content, usage.prompt_tokens, usage.completion_tokens


# ── Public entry point ────────────────────────────────────────────────────────

def regenerate_section(
    section_name:     str,
    previous_text:    str,
    jd_text:          str,
    active_flags:     list[dict],
    global_comment:   Optional[str] = None,
    generation_notes: Optional[str] = None,
) -> tuple[str, int, int]:
    """
    Regenerate a single CV section with constraint injection.

    Uses RETRY_MODEL with no thinking budget. The system prompt is a
    lightweight, section-specific prompt — not the CV generation prompt —
    so the model returns raw section text, not JSON.

    Args:
        section_name:     Canonical section name (profile | experience | ...).
        previous_text:    The text from the previous (failed) report.
        jd_text:          The original job description text.
        active_flags:     Active flag dicts to inject as constraints.
                          Dismissed flags must be excluded before calling.
        global_comment:   Optional user-level feedback.
        generation_notes: Original generation notes from the application.

    Returns:
        Tuple of (regenerated_text, prompt_tokens, completion_tokens).

    Raises:
        RuntimeError on LLM call failure or empty response.
    """
    section_instruction = _SECTION_INSTRUCTIONS.get(
        section_name, _DEFAULT_SECTION_INSTRUCTION
    )

    constraint_block = build_constraint_block(active_flags, global_comment)

    notes_block = ""
    if generation_notes and generation_notes.strip():
        notes_block = (
            f"\n## APPLICANT NOTES FOR THIS APPLICATION\n"
            f"{generation_notes.strip()}\n"
        )

    user = f"""## SOURCE PROFILE MATERIAL

### Meta
{load_text(META_PATH)}

### Experience library
{load_experience_files()}

### Skills inventory
{load_text(SKILLS_PATH)}

### Personas
{load_persona_files()}
{notes_block}
---

## JOB DESCRIPTION
{jd_text}

---

## PREVIOUS VERSION OF {section_name.upper()} SECTION
{previous_text}

---

{constraint_block}

---

## YOUR TASK

{section_instruction}"""

    log.info(
        f"Retrying '{section_name}' section — "
        f"model={RETRY_MODEL}, flags={len(active_flags)}"
    )

    try:
        if LLM_PROVIDER == "anthropic":
            text, prompt_tokens, completion_tokens = _call_anthropic(user)
        elif LLM_PROVIDER == "openai":
            text, prompt_tokens, completion_tokens = _call_openai(user)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")
    except Exception as e:
        raise RuntimeError(f"Section regeneration failed: {e}") from e

    if not text.strip():
        raise RuntimeError("Retry model returned empty text.")

    log.info(
        f"Section retry complete — "
        f"prompt: {prompt_tokens}, completion: {completion_tokens} tokens"
    )

    return text, prompt_tokens, completion_tokens
