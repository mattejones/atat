"""
question_answerer.py — LLM pipeline for answering job application questions.

Assembles context from the application (JD, CV, notes) and calls Sonnet
to answer all questions in a single batch call. Returns a mapping of
question_id -> answer text.

Questions marked needs_research=True cause the web_search tool to be
included in the API call. The model uses it selectively for those questions;
non-research questions are answered from context alone.

Tone is injected into the system prompt at call time from the application's
qa_tone field. The base system prompt lives at prompts/question_answering.md.

Output format: the model returns a JSON array:
  [{"question_id": "...", "answer": "..."}, ...]

This is prompted via the system prompt. We extract text blocks from the
response (which may also contain tool_use/tool_result blocks when web
search is used) and parse the JSON from them.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from pipeline.config import ANTHROPIC_API_KEY, PROMPTS_PATH

log = logging.getLogger(__name__)

# ── Tone instructions ─────────────────────────────────────────────────────────
# Injected into the system prompt at call time based on application.qa_tone.

_TONE_INSTRUCTIONS: dict[str, str] = {
    "professional": (
        "Write in a measured, clear, and formal tone. "
        "Suitable for established enterprise environments. "
        "Avoid slang or overly casual language."
    ),
    "direct": (
        "Write in a concise, assertive tone. "
        "Lead with the main point immediately. "
        "Avoid hedging language, filler phrases, and unnecessary preamble."
    ),
    "conversational": (
        "Write in a warm, approachable tone. "
        "Answers should read naturally — like a thoughtful spoken response — "
        "not like a cover letter or formal document."
    ),
    "technical": (
        "Write with precision and specificity. "
        "Use domain vocabulary where appropriate. "
        "Prioritise accuracy and concrete detail over stylistic polish."
    ),
}

_DEFAULT_TONE = "professional"

# ── Response length descriptions ──────────────────────────────────────────────
# Embedded in the question list to give the model per-question guidance.

_LENGTH_LABELS: dict[str, str] = {
    "short":     "1–3 concise sentences",
    "paragraph": "a well-structured paragraph of 4–6 sentences",
}

# ── Model ─────────────────────────────────────────────────────────────────────
# Sonnet — fast enough for interactive use, no extended thinking needed here.
_QA_MODEL = "claude-sonnet-4-6"


# ── Prompt loading ────────────────────────────────────────────────────────────

def _load_base_prompt() -> str:
    path = PROMPTS_PATH / "question_answering.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Question answering prompt not found at {path}. "
            "Create prompts/question_answering.md to enable this feature."
        )
    return path.read_text(encoding="utf-8")


def _build_system_prompt(qa_tone: str) -> str:
    base = _load_base_prompt()
    tone_key = qa_tone if qa_tone in _TONE_INSTRUCTIONS else _DEFAULT_TONE
    tone_instruction = _TONE_INSTRUCTIONS[tone_key]
    return f"{base}\n\n---\n\n## TONE\n\n{tone_instruction}"


# ── User message assembly ─────────────────────────────────────────────────────

def _build_user_message(
    jd_text:     str,
    cv_markdown: str,
    notes:       Optional[str],
    questions:   list[dict],
) -> str:
    """
    Assemble the user message containing all application context and the
    numbered question list.

    Each question in `questions` is a dict with keys:
      id, question_text, response_length, needs_research
    """
    notes_block = ""
    if notes and notes.strip():
        notes_block = f"\n\n### Application notes\n\n{notes.strip()}"

    question_lines: list[str] = []
    for i, q in enumerate(questions, start=1):
        length_label = _LENGTH_LABELS.get(q.get("response_length", "short"), _LENGTH_LABELS["short"])
        research_tag = " [RESEARCH REQUIRED]" if q.get("needs_research") else ""
        question_lines.append(
            f'{i}. {q["question_text"]}{research_tag}\n'
            f'   question_id: {q["id"]}\n'
            f'   length: {length_label}'
        )

    questions_block = "\n\n".join(question_lines)

    return f"""## APPLICATION CONTEXT

### Job description
{jd_text.strip()}

### CV
{cv_markdown.strip()}{notes_block}

---

## QUESTIONS

{questions_block}
"""


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_response(content_blocks: list) -> dict[str, str]:
    """
    Extract text from content blocks (which may include tool_use/tool_result
    blocks when web search was used) and parse the JSON answer array.

    Returns dict mapping question_id -> answer text.
    Raises ValueError on parse failure — callers must not silently fall back.
    """
    raw_text = "".join(
        block.text
        for block in content_blocks
        if getattr(block, "type", None) == "text"
    )

    if not raw_text.strip():
        raise ValueError("Model returned no text content in response.")

    # Strip markdown code fences if the model wrapped the JSON
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model returned invalid JSON: {e}\n"
            f"First 400 chars: {cleaned[:400]}"
        )

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    result: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"Expected array of objects, got item: {item!r}")
        q_id   = item.get("question_id", "").strip()
        answer = item.get("answer", "").strip()
        if not q_id:
            raise ValueError(f"Answer item missing question_id: {item!r}")
        result[q_id] = answer

    return result


# ── LLM call ─────────────────────────────────────────────────────────────────

def _call_anthropic(
    system:           str,
    user:             str,
    include_search:   bool,
) -> list:
    """
    Call Anthropic API and return content blocks.
    Includes the web_search tool when include_search is True.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    kwargs: dict = {}
    if include_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    message = client.messages.create(
        model=_QA_MODEL,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        **kwargs,
    )

    log.info(
        "QA generation — input: %d, output: %d tokens",
        message.usage.input_tokens,
        message.usage.output_tokens,
    )

    return message.content


# ── Public entry point ────────────────────────────────────────────────────────

def generate_answers(
    jd_text:     str,
    cv_markdown: str,
    notes:       Optional[str],
    qa_tone:     str,
    questions:   list[dict],
) -> dict[str, str]:
    """
    Generate answers for all provided questions in a single LLM call.

    Parameters
    ----------
    jd_text      : Job description text from the application.
    cv_markdown  : CV markdown from the application.
    notes        : Free-text notes from the application (may be None).
    qa_tone      : Tone key — one of professional / direct / conversational / technical.
    questions    : List of question dicts, each with:
                     id, question_text, response_length, needs_research

    Returns
    -------
    dict mapping question_id -> generated answer text.
    Raises RuntimeError on LLM or parse failure.
    """
    if not questions:
        return {}

    include_search = any(q.get("needs_research") for q in questions)

    try:
        system   = _build_system_prompt(qa_tone)
        user     = _build_user_message(jd_text, cv_markdown, notes, questions)
        blocks   = _call_anthropic(system, user, include_search)
        answers  = _parse_response(blocks)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    except ValueError as e:
        raise RuntimeError(f"Answer generation failed: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error during answer generation: {e}")

    # Warn on any questions that didn't get an answer
    answered_ids  = set(answers.keys())
    requested_ids = {q["id"] for q in questions}
    missing       = requested_ids - answered_ids
    if missing:
        log.warning("Model did not return answers for question_ids: %s", missing)

    return answers
