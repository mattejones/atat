"""The jd_spec seniority field: validated when present, optional, and written into the brief."""

import pytest

from pipeline import jd_spec as js

JD = "We need a regional manager to lead our operations team across the north."
SPEC = {
    "requirements": [{"id": "R1", "quote": "lead our operations team", "must_have": True, "evidence": []}],
    "anti_patterns": [],
    "signals": [],
}


def _errors(**extra):
    return js.validate_spec({**SPEC, **extra}, JD, index={})


def test_seniority_is_optional():
    assert _errors() == []


@pytest.mark.parametrize("level", ["ic", "manager", "head", "director", "exec", "Director", None])
def test_valid_levels_pass(level):
    assert _errors(seniority=level) == []


@pytest.mark.parametrize("level", ["senior", "vp", "junior", ""])
def test_unknown_levels_are_rejected_when_set_by_hand(level):
    errs = _errors(seniority=level)
    # "" is treated like a missing value only when null; an explicit empty string is not a level
    assert errs and "seniority must be one of" in errs[0]


def test_the_brief_states_the_level_and_the_register():
    block = js.spec_to_prompt_block({**SPEC, "seniority": "head"})
    assert "### Role level" in block and "**head**" in block
    assert js.SENIORITY_REGISTER["head"] in block and "Do not open on years of tenure" in block


def test_no_level_means_no_role_level_section():
    assert "Role level" not in js.spec_to_prompt_block({**SPEC, "seniority": None})
    assert "Role level" not in js.spec_to_prompt_block(SPEC)


def test_the_review_view_shows_the_level():
    spec = {**SPEC, "seniority": "manager", "computed_tier": "T2", "tier_stats": {}}
    assert "Role level: **manager**" in js.render_spec_for_review(spec)


def test_the_extraction_prompt_has_neutral_examples_and_the_level_scale():
    from pipeline.config import PROMPTS_PATH
    text = (PROMPTS_PATH / "jd_spec_extraction.md").read_text(encoding="utf-8")
    assert "aircall" not in text.lower()
    for level in ("ic", "manager", "head", "director", "exec"):
        assert f"`{level}`" in text
