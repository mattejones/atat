"""
cheap_llm.py — Tier 2 LLM accuracy judge for ATAT section review.

Checks a generated CV section for claims not supported by or contradicting
the source profile material (cv-library). This is a faithfulness check —
it evaluates grounded accuracy, not writing quality.

The model is asked to return verbatim excerpts from the generated text for
any unsupported claims. Character positions are then resolved by searching
for those excerpts in the raw text. This avoids asking the model to reason
about character indices directly, which is unreliable.

Deliberately uses JUDGE_MODEL (Haiku / gpt-4o-mini) — this is a structured,
mechanical task that does not require a frontier model.

System prompt is loaded from prompts/judge_accuracy.md at call time,
so it can be edited without touching source code.
"""

import json
import logging
import re
from dataclasses import dataclass, field

from pipeline.config import (
    LLM_PROVIDER, JUDGE_MODEL,
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    META_PATH, EXPERIENCE_PATH, SKILLS_PATH, PROMPTS_PATH,
)
from pipeline.tailorer import load_text, load_experience_files

log = logging.getLogger(__name__)


# ── Flag and result dataclasses ───────────────────────────────────────────────

@dataclass
class CheapLLMFlag:
    type:      str = "accuracy"
    start_pos: int = 0
    end_pos:   int = 0
    excerpt:   str = ""
    message:   str = ""


@dataclass
class CheapLLMResult:
    passed:            bool
    model:             str
    prompt_tokens:     int
    completion_tokens: int
    flags:             list[CheapLLMFlag] = field(default_factory=list)


# ── Source material loader ────────────────────────────────────────────────────

def _load_source_material() -> str:
    meta       = load_text(META_PATH)
    experience = load_experience_files()
    skills     = load_text(SKILLS_PATH)

    return f"""## META (factual baseline)
{meta}

---

## EXPERIENCE LIBRARY (roles, companies, dates, achievements)
{experience}

---

## SKILLS INVENTORY
{skills}"""


# ── Position resolver ─────────────────────────────────────────────────────────

def _resolve_positions(text: str, excerpt: str) -> tuple[int, int]:
    idx = text.find(excerpt)
    if idx == -1:
        lower_idx = text.lower().find(excerpt.lower())
        if lower_idx == -1:
            log.warning(f"Could not locate excerpt in text: '{excerpt[:60]}...'")
            return 0, len(text)
        return lower_idx, lower_idx + len(excerpt)
    return idx, idx + len(excerpt)


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_anthropic(system: str, user_message: str) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    text = "".join(
        block.text for block in message.content if block.type == "text"
    )
    return text, message.usage.input_tokens, message.usage.output_tokens


def _call_openai(system: str, user_message: str) -> tuple[str, int, int]:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        max_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
    )
    content = response.choices[0].message.content
    usage   = response.usage
    return content, usage.prompt_tokens, usage.completion_tokens


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: str) -> list[dict]:
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Judge returned invalid JSON: {e}\nRaw: {cleaned[:300]}")

    if not isinstance(data, dict) or "flags" not in data:
        raise ValueError(f"Unexpected judge response shape: {cleaned[:300]}")

    return data["flags"]


# ── Public entry point ────────────────────────────────────────────────────────

def run(section_text: str) -> CheapLLMResult:
    """
    Run the Tier 2 accuracy judge against a raw section text.
    System prompt is loaded from prompts/judge_accuracy.md at call time.
    """
    if not section_text or not section_text.strip():
        log.warning("Cheap LLM judge received empty text — skipping.")
        return CheapLLMResult(
            passed=True, model=JUDGE_MODEL,
            prompt_tokens=0, completion_tokens=0,
        )

    system          = load_text(PROMPTS_PATH / "judge_accuracy.md")
    source_material = _load_source_material()

    user_message = f"""## SOURCE PROFILE MATERIAL
{source_material}

---

## GENERATED CV SECTION (check this for accuracy)
{section_text}"""

    try:
        if LLM_PROVIDER == "anthropic":
            raw, prompt_tokens, completion_tokens = _call_anthropic(system, user_message)
        elif LLM_PROVIDER == "openai":
            raw, prompt_tokens, completion_tokens = _call_openai(system, user_message)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")
    except Exception as e:
        raise RuntimeError(f"Cheap LLM judge call failed: {e}") from e

    log.debug(
        f"Cheap LLM judge usage — "
        f"prompt: {prompt_tokens}, completion: {completion_tokens} tokens"
    )

    raw_flags = _parse_response(raw)

    flags: list[CheapLLMFlag] = []
    for f in raw_flags:
        excerpt = f.get("excerpt", "").strip()
        reason  = f.get("reason", "").strip()
        if not excerpt or not reason:
            continue

        start, end = _resolve_positions(section_text, excerpt)
        flags.append(CheapLLMFlag(
            type="accuracy",
            start_pos=start,
            end_pos=end,
            excerpt=excerpt,
            message=f"Unsupported claim: {reason}",
        ))

    log.debug(
        f"Cheap LLM judge: {len(flags)} flag(s), "
        f"model={JUDGE_MODEL}, passed={len(flags) == 0}"
    )

    return CheapLLMResult(
        passed=len(flags) == 0,
        model=JUDGE_MODEL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        flags=flags,
    )
