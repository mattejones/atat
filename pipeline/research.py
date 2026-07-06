"""
research.py — Company and role research via Anthropic web search.

Standalone module. Has no imports from cover_letter_generator or any other
feature module — it only depends on pipeline.config and the Anthropic SDK.

Designed to be called from cover_letter_generator.py today and elevated to a
top-level pipeline feature (with its own API endpoint and application-level DB
storage) in future without requiring any changes to this module.

Two-phase research:
  _call_with_search() — Anthropic call with web_search_20250305 tool
  _parse_brief()      — extracts Company / Role sections from the response text

Returns ResearchBrief. The .combined field is the full markdown response; the
.company_context and .role_context fields are the parsed sections for finer
downstream use.

Provider guard: web search is only supported for Anthropic. If LLM_PROVIDER is
not 'anthropic', the function returns an empty brief and logs a warning.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pipeline.config import (
    ANTHROPIC_API_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    PROMPTS_PATH,
)

log = logging.getLogger(__name__)

_FALLBACK_SYSTEM = """You are a research assistant preparing context for a job application cover letter.

Research the company and/or role using web search and return a structured markdown brief with
## Company Research and ## Role Research sections. Be factual and concise — 150-250 words per
section. This brief feeds a downstream generation step."""


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ResearchBrief:
    company_context: str = ""
    role_context:    str = ""
    combined:        str = ""          # full markdown as returned by the model
    generated_at:    str = field(default_factory=lambda: datetime.now().isoformat())


# ── Public API ─────────────────────────────────────────────────────────────────

def research(
    company:          str,
    role:             str,
    jd_text:          str,
    research_company: bool = True,
    research_role:    bool = True,
) -> ResearchBrief:
    """
    Run the research phase for a cover letter.

    Returns a ResearchBrief. If both flags are False, or if the configured
    provider does not support web search, returns an empty brief immediately.

    Raises RuntimeError on API failure — callers should decide whether to treat
    this as fatal or degrade gracefully.
    """
    if not research_company and not research_role:
        log.info("Both research flags disabled — skipping research phase")
        return ResearchBrief()

    if LLM_PROVIDER != "anthropic":
        log.warning(
            "Web search research requires Anthropic provider (current: %r) — skipping",
            LLM_PROVIDER,
        )
        return ResearchBrief()

    system = _load_system_prompt()
    user   = _build_user_message(company, role, jd_text, research_company, research_role)

    log.info(
        "Research phase starting (company=%s, role=%s, research_company=%s, research_role=%s)",
        company, role, research_company, research_role,
    )

    brief_text = _call_with_search(system, user)
    return _parse_brief(brief_text)


# ── Prompt loading ─────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    path = PROMPTS_PATH / "cover_letter_research.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    log.warning("cover_letter_research.md not found or empty — using fallback system prompt")
    return _FALLBACK_SYSTEM


# ── Message assembly ───────────────────────────────────────────────────────────

def _build_user_message(
    company:          str,
    role:             str,
    jd_text:          str,
    research_company: bool,
    research_role:    bool,
) -> str:
    scope_parts: list[str] = []
    if research_company:
        scope_parts.append(f"the company **{company}**")
    if research_role:
        scope_parts.append(f"the role **{role}**")
    scope = " and ".join(scope_parts)

    # Truncate JD to keep the prompt lean — research doesn't need the full text
    jd_excerpt = jd_text[:3000].strip()
    if len(jd_text) > 3000:
        jd_excerpt += "\n\n[... truncated]"

    return f"""Please research {scope} and return a structured markdown brief.

## Target company: {company}
## Target role: {role}

## Job description (for role context)
{jd_excerpt}

Return ## Company Research and ## Role Research sections. Keep each section to
150-250 words. Focus on actionable, specific insights — not generic descriptions."""


# ── Anthropic call ─────────────────────────────────────────────────────────────

def _call_with_search(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=LLM_MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )

    log.info(
        "Research usage — input: %d, output: %d tokens",
        message.usage.input_tokens,
        message.usage.output_tokens,
    )

    # Collect text blocks only — tool_use / web_search_result blocks are informational
    text = "".join(
        block.text
        for block in message.content
        if hasattr(block, "text") and block.type == "text"
    )

    if not text.strip():
        raise RuntimeError("Research call returned no text content — model may have only searched")

    return text.strip()


# ── Brief parsing ──────────────────────────────────────────────────────────────

def _parse_brief(text: str) -> ResearchBrief:
    """
    Extract Company Research and Role Research sections from the markdown brief.
    Falls back gracefully: if sections can't be identified, the full text is
    stored in combined and the individual fields are left empty.
    """
    company_ctx = ""
    role_ctx    = ""
    current:    Optional[str] = None
    buf:        list[str]     = []

    for line in text.splitlines():
        lower = line.lower().strip()
        if lower.startswith("## company"):
            if current == "role":
                role_ctx = "\n".join(buf).strip()
            current = "company"
            buf = []
        elif lower.startswith("## role"):
            if current == "company":
                company_ctx = "\n".join(buf).strip()
            current = "role"
            buf = []
        else:
            buf.append(line)

    # Flush final section
    if current == "company" and not company_ctx:
        company_ctx = "\n".join(buf).strip()
    elif current == "role" and not role_ctx:
        role_ctx = "\n".join(buf).strip()

    return ResearchBrief(
        company_context=company_ctx,
        role_context=role_ctx,
        combined=text,
    )
