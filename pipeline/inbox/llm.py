"""
llm.py — one JSON-returning model call, shared by triage and classification.

Same shape as the judges (pipeline/judges/cheap_llm.py): ask for JSON in the prompt,
strip any code fence, json.loads, and leave shape checks to the caller. Uses
INBOX_MODEL (JUDGE_MODEL by default) — sorting email is a cheap, mechanical task.
Raw responses that fail to parse are written to logs/inbox_failures/ for inspection.

Token usage is tallied per thread (a sync runs on one job-runner thread): wrap a run in
usage_scope() and read the totals from the dict it yields.
"""

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from pipeline.config import (
    ANTHROPIC_API_KEY, INBOX_MODEL, LLM_PROVIDER, OPENAI_API_KEY, LOGS_PATH,
)

log = logging.getLogger(__name__)

_FAILURE_DIR = LOGS_PATH / "inbox_failures"

_local = threading.local()


def _new_usage() -> dict:
    return {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}


@contextmanager
def usage_scope() -> Iterator[dict]:
    """Collect token usage of every call_json on this thread until the block exits."""
    previous = getattr(_local, "usage", None)
    _local.usage = usage = _new_usage()
    try:
        yield usage
    finally:
        _local.usage = previous


def _record(input_tokens: int, output_tokens: int) -> None:
    usage = getattr(_local, "usage", None)
    if usage is not None:
        usage["llm_calls"]     += 1
        usage["input_tokens"]  += input_tokens or 0
        usage["output_tokens"] += output_tokens or 0


def _call_anthropic(system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
    import anthropic
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=INBOX_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    return text, message.usage.input_tokens, message.usage.output_tokens


def _call_openai(system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=INBOX_MODEL,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    usage = response.usage
    return response.choices[0].message.content, usage.prompt_tokens, usage.completion_tokens


def parse_json(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _log_failure(label: str, raw: str, err: Exception) -> None:
    try:
        _FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        path = _FAILURE_DIR / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{label}.txt"
        path.write_text(f"{err}\n\n{raw}", encoding="utf-8")
    except OSError:
        pass


def call_json(system: str, user: str, *, max_tokens: int = 1024, label: str = "call") -> dict:
    """Call INBOX_MODEL and return its JSON object. Raises RuntimeError on any failure."""
    try:
        if LLM_PROVIDER == "anthropic":
            raw, tokens_in, tokens_out = _call_anthropic(system, user, max_tokens)
        elif LLM_PROVIDER == "openai":
            raw, tokens_in, tokens_out = _call_openai(system, user, max_tokens)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")
    except Exception as e:
        raise RuntimeError(f"Inbox {label} call failed: {e}") from e
    _record(tokens_in, tokens_out)
    try:
        return parse_json(raw)
    except ValueError as e:  # json.JSONDecodeError is a ValueError
        _log_failure(label, raw, e)
        raise RuntimeError(f"Inbox {label} returned invalid JSON: {e}") from e
