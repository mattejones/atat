"""
llm_json.py — reading a model's JSON reply, and trying again when it is unusable.

Models occasionally write a backslash before a character JSON does not allow to be escaped
(\\', \\£, \\-), or return nothing at all. Either used to fail a whole multi-minute generation.
loads_lenient repairs the first; call_with_retry makes the second call once more before giving up.
"""

import json
import logging
import re
from typing import Any, Callable

log = logging.getLogger(__name__)

# A valid JSON escape (kept as one unit, so a doubled backslash is read as a pair) or a lone backslash.
_ESCAPE = re.compile(r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})|\\')


def strip_fences(raw: str) -> str:
    """Remove a ```json code fence the model wrapped its reply in."""
    cleaned = re.sub(r'^```(?:json)?\s*', '', (raw or "").strip(), flags=re.IGNORECASE)
    return re.sub(r'\s*```$', '', cleaned).strip()


def repair_escapes(text: str) -> str:
    """Drop every backslash that does not start a valid JSON escape: a stray \' or \£ means the
    character after it, so it is kept and the backslash is not."""
    return _ESCAPE.sub(lambda m: m.group(0) if m.group(1) else "", text)


def loads_lenient(raw: str) -> Any:
    """json.loads for a model reply: strips a code fence and, only if the text will not parse as
    it is, repairs invalid escapes. Valid JSON is never altered. Raises json.JSONDecodeError."""
    cleaned = strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = repair_escapes(cleaned)
        if repaired == cleaned:
            raise
        return json.loads(repaired)


def call_with_retry(call: Callable[[], Any], parse: Callable[[Any], Any], attempts: int = 2) -> Any:
    """
    result = parse(call()), calling the model again if the reply cannot be parsed (a ValueError,
    which json.JSONDecodeError is). After `attempts` unusable replies the last error is raised.
    Errors from the call itself (no API key, network) are not about the reply and are not retried.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        raw = call()
        try:
            return parse(raw)
        except ValueError as e:
            last = e
            log.warning("Model reply unusable (attempt %d of %d): %s", attempt, attempts, str(e)[:200])
    assert last is not None
    raise last
