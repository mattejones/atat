"""
retry.py — Section-level regeneration with constraint injection for ATAT.

Called by the review route when a retry is triggered. Takes the active flags
and user comment from a failed report and regenerates only the target section,
injecting the failure context as explicit constraints into the prompt.

Unlike the initial generation (which produces a full structured CV via JSON),
section retries ask the model to return raw section text only — no JSON wrapper,
no headers, no surrounding structure. This is simpler, faster, and cheaper.

The constraint block is built from active (non-dismissed) flags only.
Dismissed flags are excluded — the user has explicitly decided they are not issues.
"""

import logging
from typing import Optional

from pipeline.config import (
    LLM_PROVIDER, LLM_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    TEMPERATURE, MAX_OUTPUT_TOKENS,
)
from pipeline.tailorer import (
    build_system_prompt,
    load_text, load_experience_files, load_persona_files,
    META_PATH, SKILLS_PATH,
)

log = logging.getLogger(__name__)


# ── Section-specific guidance ─────────────────────────────────────────────────
# Tells the model what to return for each section type.

_SECTION_INSTRUCTIONS: dict[str, str] = {
    "profile": (
        "Return ONLY the profile paragraph — a single concise paragraph of plain prose. "
        "No heading, no bullet points, no markdown formatting."
    ),
    "experience": (
        "Return ONLY the experience section content in the same markdown format as the original: "
        "### Company -- Role | Dates, followed by bullet points. "
        "No top-level heading."
    ),
    "skills": (
        "Return ONLY the skills section content: "
        "one line per category in the format **Category:** items. "
        "No top-level heading."
    ),
    "education": (
        "Return ONLY the education section content in the same format as the original. "
        "No top-level heading."
    ),
    "certifications": (
        "Return ONLY the certifications section content as a bullet list. "
        "No top-level heading."
    ),
}

_DEFAULT_SECTION_INSTRUCTION = (
    "Return ONLY the raw section content — no headings, no JSON, no preamble."
)


# ── Constraint block builder ──────────────────────────────────────────────────

def build_constraint_block(
    active_flags: list[dict],
    global_comment: Optional[str] = None,
) -> str:
    """
    Build the constraint block injected into the retry prompt.

    Takes a list of active flag dicts (with keys: type, excerpt, message).
    Dismissed flags must be excluded by the caller before passing.

    Returns a formatted string ready for injection into the user message.
    """
    lines: list[str] = [
        "## ISSUES WITH THE PREVIOUS VERSION",
        "",
        "The previous version of this section had the following problems.",
        "Regenerate addressing all of them explicitly:",
        "",
    ]

    for i, flag in enumerate(active_flags, 1):
        flag_type = flag.get("type", "unknown").replace("_", " ").upper()
        message   = flag.get("message", "")
        excerpt   = flag.get("excerpt", "")

        lines.append(f"{i}. [{flag_type}] {message}")
        if excerpt:
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

def _call_anthropic(system: str, user: str) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=LLM_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in message.content if b.type == "text")
    return text.strip(), message.usage.input_tokens, message.usage.output_tokens


def _call_openai(system: str, user: str) -> tuple[str, int, int]:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    content = response.choices[0].message.content.strip()
    usage   = response.usage
    return content, usage.prompt_tokens, usage.completion_tokens


# ── Public entry point ────────────────────────────────────────────────────────

def regenerate_section(
    section_name:    str,
    previous_text:   str,
    jd_text:         str,
    active_flags:    list[dict],
    global_comment:  Optional[str] = None,
    generation_notes: Optional[str] = None,
) -> tuple[str, int, int]:
    """
    Regenerate a single CV section with constraint injection.

    Args:
        section_name:     Canonical section name (profile | experience | ...).
        previous_text:    The text from the previous (failed) report.
        jd_text:          The original job description text.
        active_flags:     List of active flag dicts to inject as constraints.
                          Dismissed flags must be excluded before calling.
        global_comment:   Optional user-level feedback.
        generation_notes: Original generation notes from the application.

    Returns:
        Tuple of (regenerated_text, prompt_tokens, completion_tokens).

    Raises:
        RuntimeError on LLM call failure.
    """
    section_instruction = _SECTION_INSTRUCTIONS.get(
        section_name, _DEFAULT_SECTION_INSTRUCTION
    )

    constraint_block = build_constraint_block(active_flags, global_comment)

    system = build_system_prompt()

    notes_block = ""
    if generation_notes and generation_notes.strip():
        notes_block = f"""
---

## APPLICANT NOTES FOR THIS APPLICATION
{generation_notes.strip()}
"""

    user = f"""## META
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

---

## PREVIOUS VERSION OF {section_name.upper()} SECTION
{previous_text}

---

{constraint_block}

---

## YOUR TASK

Regenerate the {section_name} section only, addressing every issue listed above.

{section_instruction}

Do not explain your changes. Return only the section content."""

    log.info(
        f"Regenerating '{section_name}' section with "
        f"{len(active_flags)} active flag(s)"
    )

    try:
        if LLM_PROVIDER == "anthropic":
            text, prompt_tokens, completion_tokens = _call_anthropic(system, user)
        elif LLM_PROVIDER == "openai":
            text, prompt_tokens, completion_tokens = _call_openai(system, user)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")
    except Exception as e:
        raise RuntimeError(f"Section regeneration failed: {e}") from e

    if not text.strip():
        raise RuntimeError("Model returned empty text on section retry.")

    log.info(
        f"Section retry complete — "
        f"prompt: {prompt_tokens}, completion: {completion_tokens} tokens"
    )

    return text, prompt_tokens, completion_tokens
