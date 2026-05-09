"""
retry.py — Section-level regeneration with constraint injection for ATAT.

System prompt loaded from prompts/retry_system.md at call time.
Per-section return instructions loaded from prompts/retry_sections/{name}.md.
Both can be edited without touching source code.
"""

import logging
from typing import Optional

from pipeline.config import (
    LLM_PROVIDER, RETRY_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    TEMPERATURE, MAX_OUTPUT_TOKENS,
    META_PATH, SKILLS_PATH, PROMPTS_PATH,
)
from pipeline.tailorer import (
    load_text, load_experience_files, load_persona_files,
)

log = logging.getLogger(__name__)

_RETRY_SECTIONS_PATH = PROMPTS_PATH / "retry_sections"

_DEFAULT_SECTION_INSTRUCTION = (
    "Return ONLY the raw section content. No JSON, no headings, no preamble."
)


def _load_section_instruction(section_name: str) -> str:
    """Load return instruction for a section from prompts/retry_sections/{name}.md."""
    path = _RETRY_SECTIONS_PATH / f"{section_name}.md"
    content = load_text(path)
    return content.strip() if content.strip() else _DEFAULT_SECTION_INSTRUCTION


# ── Constraint block builder ──────────────────────────────────────────────────

def build_constraint_block(
    active_flags:   list[dict],
    global_comment: Optional[str] = None,
) -> str:
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

def _call_anthropic(system: str, user: str) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=RETRY_MODEL,
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
        model=RETRY_MODEL,
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
    section_name:     str,
    previous_text:    str,
    jd_text:          str,
    active_flags:     list[dict],
    global_comment:   Optional[str] = None,
    generation_notes: Optional[str] = None,
) -> tuple[str, int, int]:
    """
    Regenerate a single CV section with constraint injection.
    System prompt and section return instructions are loaded from disk.
    """
    system              = load_text(PROMPTS_PATH / "retry_system.md")
    section_instruction = _load_section_instruction(section_name)
    constraint_block    = build_constraint_block(active_flags, global_comment)

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
            text, prompt_tokens, completion_tokens = _call_anthropic(system, user)
        elif LLM_PROVIDER == "openai":
            text, prompt_tokens, completion_tokens = _call_openai(system, user)
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
