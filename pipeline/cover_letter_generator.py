"""
cover_letter_generator.py — Two-phase cover letter generation pipeline.

Phase 1 — Research (optional):
    Calls pipeline.research.research() to produce a ResearchBrief via web search.
    Research is non-fatal: if it fails, generation proceeds without the brief.

Phase 2 — Generation:
    Assembles full context (CV library, application CV + reasoning, JD, research
    brief, user draft and key points) and calls the configured LLM provider.

Context mirrors the CV generation pipeline in tailorer.py — same library loaders,
same sanitisation, so the model works from the same source of truth.

System prompt loaded from prompts/cover_letter_system.md.
Personal additions loaded from prompts/cover_letter_personal.md (gitignored, optional).

Returns (cover_letter_markdown: str, brief: Optional[ResearchBrief]).
"""

import logging
from typing import Optional

from pipeline.config import (
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    MAX_OUTPUT_TOKENS,
    META_PATH,
    OPENAI_API_KEY,
    PROMPTS_PATH,
    SKILLS_PATH,
    TEMPERATURE,
)
from pipeline.research import ResearchBrief, research as run_research
from pipeline.tailorer import (
    load_experience_files,
    load_persona_files,
    load_text,
    sanitise_text,
)

log = logging.getLogger(__name__)

_FALLBACK_SYSTEM = (
    "You are a professional cover letter writer for technology and GTM roles. "
    "Write a tailored, compelling cover letter using the provided context. "
    "Return ONLY the cover letter body as plain markdown: date, salutation, "
    "body paragraphs, sign-off. Do not include the applicant contact header."
)


# ── Prompt loading ─────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    base_path     = PROMPTS_PATH / "cover_letter_system.md"
    personal_path = PROMPTS_PATH / "cover_letter_personal.md"

    base = _FALLBACK_SYSTEM
    if base_path.exists():
        content = base_path.read_text(encoding="utf-8").strip()
        if content:
            base = content

    if personal_path.exists():
        personal = personal_path.read_text(encoding="utf-8").strip()
        if personal:
            return f"{base}\n\n---\n\n## PERSONAL ADDITIONS\n\n{personal}"

    return base


# ── Message assembly ───────────────────────────────────────────────────────────

def _assemble_user_message(
    company:     str,
    role:        str,
    jd_text:     str,
    cv_markdown: str,
    reasoning:   Optional[str],
    brief:       Optional[ResearchBrief],
    draft_input: Optional[str],
    key_points:  Optional[str],
) -> str:
    parts: list[str] = []

    # CV library — same source of truth as tailorer.py
    parts.append(f"## APPLICANT META\n{load_text(META_PATH)}")
    parts.append(f"## PERSONAS\n{load_persona_files()}")
    parts.append(f"## EXPERIENCE LIBRARY\n{load_experience_files()}")
    parts.append(f"## SKILLS INVENTORY\n{load_text(SKILLS_PATH)}")

    # Application context
    parts.append(f"## GENERATED CV\n{sanitise_text(cv_markdown)}")

    if reasoning and reasoning.strip():
        parts.append(
            f"## CV GENERATION REASONING\n"
            f"<!-- The model's reasoning when generating the CV — "
            f"use this to understand persona and emphasis choices -->\n"
            f"{sanitise_text(reasoning)}"
        )

    parts.append(
        f"## JOB DESCRIPTION\n"
        f"**Company:** {company}\n"
        f"**Role:** {role}\n\n"
        f"{sanitise_text(jd_text)}"
    )

    if brief and brief.combined.strip():
        parts.append(f"## RESEARCH BRIEF\n{sanitise_text(brief.combined)}")

    if draft_input and draft_input.strip():
        parts.append(
            f"## APPLICANT ROUGH DRAFT\n"
            f"<!-- Treat this as a strong signal for content, tone, and intent -->\n"
            f"{sanitise_text(draft_input)}"
        )

    if key_points and key_points.strip():
        parts.append(
            f"## KEY POINTS TO INCLUDE\n"
            f"<!-- The applicant has flagged these as important to address -->\n"
            f"{sanitise_text(key_points)}"
        )

    return "\n\n---\n\n".join(parts)


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_cover_letter(
    company:          str,
    role:             str,
    jd_text:          str,
    cv_markdown:      str,
    reasoning:        Optional[str] = None,
    research_company: bool          = True,
    research_role:    bool          = True,
    draft_input:      Optional[str] = None,
    key_points:       Optional[str] = None,
) -> tuple[str, Optional[ResearchBrief]]:
    """
    Run the two-phase cover letter pipeline.

    Returns (cover_letter_markdown, research_brief).
    research_brief is None if both research flags are False, if the provider
    does not support web search, or if the research phase fails.

    Raises RuntimeError if the generation phase fails.
    """
    # ── Phase 1: Research ──────────────────────────────────────────────────────
    brief: Optional[ResearchBrief] = None

    if research_company or research_role:
        try:
            brief = run_research(
                company=company,
                role=role,
                jd_text=jd_text,
                research_company=research_company,
                research_role=research_role,
            )
            log.info("Research phase complete — brief length: %d chars", len(brief.combined))
        except RuntimeError as e:
            # Research failure is non-fatal: log and continue without the brief
            log.warning("Research phase failed, proceeding without brief: %s", e)

    # ── Phase 2: Generation ────────────────────────────────────────────────────
    system = _load_system_prompt()
    user   = _assemble_user_message(
        company=company,
        role=role,
        jd_text=jd_text,
        cv_markdown=cv_markdown,
        reasoning=reasoning,
        brief=brief,
        draft_input=draft_input,
        key_points=key_points,
    )

    log.info("Cover letter generation starting (provider=%s, model=%s)", LLM_PROVIDER, LLM_MODEL)

    if LLM_PROVIDER == "anthropic":
        markdown = _call_anthropic(system, user)
    elif LLM_PROVIDER == "openai":
        markdown = _call_openai(system, user)
    else:
        raise RuntimeError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")

    return markdown.strip(), brief


# ── LLM clients ───────────────────────────────────────────────────────────────

def _call_anthropic(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    base_kwargs = dict(
        model=LLM_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    try:
        message = client.messages.create(temperature=TEMPERATURE, **base_kwargs)
    except anthropic.BadRequestError as e:
        # Some newer models reject an explicit `temperature` value outright
        # ("temperature is deprecated for this model") instead of just
        # ignoring it — fall back to the model's default sampling rather
        # than hard-failing cover letter generation against that model.
        if "temperature" in str(e).lower() and "deprecated" in str(e).lower():
            log.warning(
                f"Model {LLM_MODEL} does not accept `temperature` — retrying without it."
            )
            message = client.messages.create(**base_kwargs)
        else:
            raise

    log.info(
        "Generation usage — input: %d, output: %d tokens",
        message.usage.input_tokens,
        message.usage.output_tokens,
    )

    text = "".join(
        block.text
        for block in message.content
        if hasattr(block, "text") and block.type == "text"
    )

    if not text.strip():
        raise RuntimeError("Model returned no text content")

    return text


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
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError("Model returned no text content")
    return content
