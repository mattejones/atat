"""The pipeline must keep working when the Anthropic SDK no longer accepts `temperature`
(anthropic 2.x dropped it from messages.create / messages.stream)."""

import types

import anthropic
import pytest

from pipeline.llm_compat import with_temperature


def _old_style(*, model, max_tokens, temperature=None, **kw): ...      # still takes it
def _new_style(*, model, max_tokens, system=None, messages=None): ...  # 2.x: it is gone
def _any_kwargs(**kw): ...                                             # can't tell → keep it


def test_keeps_temperature_when_the_sdk_accepts_it():
    assert with_temperature(_old_style, 0.3) == {"temperature": 0.3}


def test_drops_temperature_when_the_sdk_does_not_accept_it():
    assert with_temperature(_new_style, 0.3) == {}


def test_keeps_temperature_for_a_catch_all_signature():
    assert with_temperature(_any_kwargs, 0.3) == {"temperature": 0.3}


def test_uninspectable_callables_keep_the_old_behaviour():
    assert with_temperature(print, 1) in ({"temperature": 1}, {})   # must not raise


def _fake_client(stream_fn):
    return types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream_fn))


class _Stream:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        block = types.SimpleNamespace(type="text", text='{"ok": true}')
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        return types.SimpleNamespace(content=[block], usage=usage)


def test_cv_generation_runs_against_an_sdk_without_temperature(monkeypatch):
    """The reported failure: Messages.stream() got an unexpected keyword argument 'temperature'."""
    from pipeline import tailorer

    seen = {}

    def stream(*, model, max_tokens, system, messages, thinking=None):   # no temperature, no **kw
        seen.update(model=model, thinking=thinking)
        return _Stream()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(stream))
    text = tailorer._call_anthropic("system", "user")
    assert text == '{"ok": true}'
    assert seen["model"] == tailorer.LLM_MODEL


def test_cv_generation_still_passes_temperature_to_an_sdk_that_takes_it(monkeypatch):
    from pipeline import tailorer

    monkeypatch.setattr(tailorer, "THINKING_BUDGET", 0)    # temperature only applies without thinking
    seen = {}

    def stream(*, model, max_tokens, system, messages, temperature=None, thinking=None):
        seen["temperature"] = temperature
        return _Stream()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _fake_client(stream))
    tailorer._call_anthropic("system", "user")
    assert seen["temperature"] == tailorer.TEMPERATURE
