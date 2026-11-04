"""
Reading a model's JSON reply: tolerate the escapes models get wrong, and try again once when a
reply is unusable, so one bad reply doesn't throw away a multi-minute run.
"""

import json

import pytest

from pipeline import llm_json
from pipeline import tailorer


@pytest.mark.parametrize("raw, expected", [
    (r'{"a": "it\'s fine"}',            {"a": "it's fine"}),            # \'
    (r'{"a": "costs \£40"}',            {"a": "costs £40"}),            # \£
    (r'{"a": "full\-time"}',            {"a": "full-time"}),            # \-
    (r'{"a": "a\&b"}',                  {"a": "a&b"}),
])
def test_invalid_escapes_are_accepted(raw, expected):
    assert llm_json.loads_lenient(raw) == expected


def test_valid_escapes_are_left_alone():
    bs = chr(92)
    raw = '{"a": "line' + bs + 'nbreak ' + bs + '"quoted' + bs + '" back' + bs + bs + 'slash ' + bs + 'u00e9 tab' + bs + 't"}'
    assert llm_json.loads_lenient(raw) == json.loads(raw)
    assert llm_json.repair_escapes(raw) == raw


def test_a_valid_backslash_pair_followed_by_a_bad_escape_is_read_correctly():
    # \ is a real backslash; the \' after it is the bad one
    assert llm_json.loads_lenient('{"a": "x\\\\\\\'y"}') == {"a": "x\\'y"}


def test_code_fences_are_stripped():
    assert llm_json.loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_unrecoverable_json_still_raises():
    with pytest.raises(json.JSONDecodeError):
        llm_json.loads_lenient('{"a": ')


def test_the_cv_parser_accepts_a_reply_with_a_bad_escape(monkeypatch):
    monkeypatch.setattr(tailorer, "_REQUIRED_FIELDS", frozenset({"reasoning"}))
    assert tailorer.parse_llm_response(r'{"reasoning": "it\'s a fit"}') == {"reasoning": "it's a fit"}


def test_a_bad_reply_is_retried_once_then_succeeds():
    replies = iter(["not json at all", '{"ok": true}'])
    calls = []

    def call():
        calls.append(1)
        return next(replies)

    assert llm_json.call_with_retry(call, lambda raw: json.loads(raw)) == {"ok": True}
    assert len(calls) == 2


def test_an_empty_reply_is_retried_once():
    replies = iter(["", '{"ok": true}'])
    assert llm_json.call_with_retry(lambda: next(replies), lambda raw: llm_json.loads_lenient(raw)) == {"ok": True}


def test_two_bad_replies_fail_clearly_without_a_third_call():
    calls = []

    def call():
        calls.append(1)
        return ""

    with pytest.raises(ValueError):
        llm_json.call_with_retry(call, lambda raw: llm_json.loads_lenient(raw))
    assert len(calls) == 2


def test_an_error_that_is_not_about_the_reply_is_not_retried():
    calls = []

    def call():
        calls.append(1)
        raise RuntimeError("no api key")

    with pytest.raises(RuntimeError):
        llm_json.call_with_retry(call, lambda raw: raw)
    assert len(calls) == 1
