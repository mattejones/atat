"""
The deterministic judge's context-aware checks, the readability fixes, and the judge plumbing.
Pure functions and prompt assembly: no database, no model calls.
"""

import pytest

import pipeline.config as config
from pipeline.judges import cheap_llm, deterministic as det, orchestrator
from pipeline.judges.context import JudgeContext, stem, tokens

JD = (
    "We are looking for a regional lead. You will coach market leads making calls themselves "
    "without escalating to head office. Experience of clinical governance and regulatory "
    "inspection is essential. You will produce weekly reporting for the clinical board."
)
LIBRARY = (
    "Led a team of twelve across three branches. Ran rotas and handled escalations for the "
    "branch managers. Produced monthly summaries for the board."
)


def _ctx(section="experience", cv_text="", library=LIBRARY, jd=JD, additions="") -> JudgeContext:
    return JudgeContext(section=section, jd_text=jd, library_text=library,
                        personal_additions=additions, cv_text=cv_text)


def _types(result) -> list:
    return [f.type for f in result.flags]


# ── Ad echo ───────────────────────────────────────────────────────────────────

def test_phrase_copied_from_the_ad_and_not_in_the_library_is_flagged():
    text = "- Coached market leads making calls themselves across the region for three years running.\n"
    result = det.run(text, _ctx())
    echo = [f for f in result.flags if f.type == "ad_echo"]
    assert echo, "a 4+ word phrase from the ad with no source in the library must be flagged"
    assert "market leads making calls" in echo[0].excerpt.lower()
    assert text[echo[0].start_pos:echo[0].end_pos] == echo[0].excerpt           # span is exact


def test_the_same_phrase_is_fine_when_the_library_says_it_too():
    library = LIBRARY + " I coached market leads making calls themselves in the Leeds region."
    text = "- Coached market leads making calls themselves across the region for three years.\n"
    assert "ad_echo" not in _types(det.run(text, _ctx(library=library)))


def test_ordinary_wording_is_not_an_echo():
    text = "- Led a team of twelve across three branches and handled escalations for the managers.\n"
    assert "ad_echo" not in _types(det.run(text, _ctx()))


def test_an_ad_only_word_used_more_than_once_in_the_cv_is_flagged():
    cv = "Profile about clinical work. - Ran clinical rotas. - Wrote clinical notes."
    text = "- Ran clinical rotas for the branch.\n"
    flags = [f for f in det.run(text, _ctx(cv_text=cv)).flags if f.type == "ad_echo"]
    assert any("clinical" in f.message and "3 times" in f.message for f in flags)


def test_an_ad_only_word_used_once_is_left_alone():
    cv = "Profile. - Ran clinical rotas."
    text = "- Ran clinical rotas for the branch.\n"
    assert not [f for f in det.run(text, _ctx(cv_text=cv)).flags if "clinical" in f.message]


def test_a_word_the_library_uses_is_not_ad_only():
    library = LIBRARY + " Clinical audits were part of the role."
    cv = "- Ran clinical rotas. - Wrote clinical notes."
    text = "- Ran clinical rotas for the branch.\n"
    assert not [f for f in det.run(text, _ctx(cv_text=cv, library=library)).flags if "clinical" in f.message]


def test_standing_rules_license_terms_too():
    cv = "- Ran clinical rotas. - Wrote clinical notes."
    text = "- Ran clinical rotas for the branch.\n"
    ctx = _ctx(cv_text=cv, additions="Use the word clinical only for the current employer.")
    assert not [f for f in det.run(text, ctx).flags if "clinical" in f.message]


def test_stems_group_related_words():
    assert stem("regulatory") == stem("regulated") == stem("regulation")
    assert [t[0] for t in tokens("Led 3.5% growth")][:2] == ["led", "3"]


def test_no_job_ad_means_no_echo_check():
    assert "ad_echo" not in _types(det.run("- Coached market leads making calls themselves.\n", _ctx(jd="")))


# ── Repetition ────────────────────────────────────────────────────────────────

def test_a_word_used_three_times_in_one_role_is_flagged_at_the_third_use():
    text = (
        "### Acme -- Head | 2020 - 2023\n\n"
        "- Shipped the MVP to the first pilot customers in two regions.\n"
        "- Rebuilt the MVP after feedback from the pilot team and support.\n"
        "- Retired the MVP once the full platform launched for everyone.\n"
    )
    flags = [f for f in det.run(text, _ctx(section="experience", jd="")).flags if f.type == "repetition"]
    assert len(flags) == 1 and flags[0].excerpt == "MVP"
    third = text.rfind("MVP")
    assert flags[0].start_pos == third


def test_repetition_count_resets_for_each_role():
    text = (
        "### A -- Head | 2020 - 2023\n\n- Ran the programme for the whole region and its branches.\n"
        "- Ran the programme review with the senior board and trustees.\n\n"
        "### B -- Lead | 2018 - 2020\n\n- Ran the programme pilot for two branches in the north.\n"
    )
    assert "repetition" not in _types(det.run(text, _ctx(section="experience", jd="")))


def test_repetition_only_applies_to_the_experience_section():
    text = "- aaaa bbbb cccc dddd eeee ffff gggg hhhh\n" * 3
    assert "repetition" not in _types(det.run(text, _ctx(section="certifications", jd="")))


# ── Fragments ─────────────────────────────────────────────────────────────────

def test_short_experience_bullets_are_fragments():
    text = "### A -- B | 2020 - 2021\n\n- Led the Leeds team.\n- Led a team of twelve across three branches in two regions.\n"
    flags = [f for f in det.run(text, _ctx(section="experience", jd="")).flags if f.type == "fragment"]
    assert len(flags) == 1 and "Led the Leeds team." in flags[0].excerpt


def test_short_certification_bullets_are_not_fragments():
    assert "fragment" not in _types(det.run("- First Aid\n- CQC Level 3\n", _ctx(section="certifications", jd="")))


# ── Profile shape ─────────────────────────────────────────────────────────────

PARA_A = " ".join(["a"] * 50)
PARA_B = " ".join(["b"] * 50)


def test_profile_with_the_right_shape_passes():
    assert "profile_shape" not in _types(det.run(f"{PARA_A}.\n\n{PARA_B}.", _ctx(section="profile", jd="")))


def test_profile_with_too_few_paragraphs_is_flagged():
    flags = [f for f in det.run(f"{PARA_A} {PARA_B}.", _ctx(section="profile", jd="")).flags
             if f.type == "profile_shape"]
    assert any("1 paragraph" in f.message and "expects 2" in f.message for f in flags)


def test_profile_that_is_too_short_or_too_long_is_flagged():
    short = [f for f in det.run("Short one.\n\nShort two.", _ctx(section="profile", jd="")).flags
             if f.type == "profile_shape"]
    assert any("words; this instance expects 80-120" in f.message for f in short)
    long_ = [f for f in det.run(f"{PARA_A} {PARA_A}.\n\n{PARA_B} {PARA_B}.", _ctx(section="profile", jd="")).flags
             if f.type == "profile_shape"]
    assert any("expects 80-120" in f.message for f in long_)


def test_profile_shape_follows_the_instance_settings(monkeypatch):
    monkeypatch.setattr(config, "PROFILE_PARAGRAPHS", 1)
    monkeypatch.setattr(config, "PROFILE_WORDS_MIN", 40)
    monkeypatch.setattr(config, "PROFILE_WORDS_MAX", 60)
    one = " ".join(["w"] * 50) + "."
    assert "profile_shape" not in _types(det.run(one, _ctx(section="profile", jd="")))


# ── Without a context the judge behaves as before ─────────────────────────────

def test_run_without_a_context_does_none_of_the_section_checks():
    result = det.run("- Led.\n- Led.\n- Led.\n")
    assert "fragment" not in _types(result) and "repetition" not in _types(result)


def test_hotwords_still_flag_without_a_context():
    assert "hotword" in _types(det.run("We leverage robust systems."))


# ── Readability ───────────────────────────────────────────────────────────────

NAMES = (
    "She ran the day with Alexandrovich Konstantinopolous and the Northumberland Metropolitan "
    "Establishment, and kept everyone safe, calm and well looked after."
)
ACRONYMS = (
    "The Registered Manager oversaw CQC compliance, safeguarding KPIs, and Good Care Group "
    "governance across Oxford Aunts operations while managing EHCP reviews and DBS checks."
)


def test_long_names_do_not_cause_a_false_readability_flag():
    # As written this scores far below the 40 threshold, only because of the long proper nouns.
    assert det._flesch_reading_ease(det._protect_dots(NAMES)) < det.FLESCH_MIN_SCORE
    assert det._readability_score(NAMES) >= det.FLESCH_MIN_SCORE
    assert "readability" not in _types(det.run(NAMES))


def test_the_better_of_the_two_readings_is_used():
    # Here the acronyms were propping the score up, so removing them is worse: keep the better.
    protected = det._protect_dots(ACRONYMS)
    assert det._readability_score(ACRONYMS) == max(
        det._flesch_reading_ease(protected), det._flesch_reading_ease(det._without_names(protected)),
    )


def test_names_and_acronyms_can_be_taken_out_of_prose():
    prepared = det._without_names(det._protect_dots(ACRONYMS))
    for gone in ("CQC", "KPIs", "EHCP", "DBS", "Oxford", "Aunts", "Care", "Group"):
        assert gone not in prepared
    assert prepared.startswith("The ")                       # the sentence's first word stays


def test_dotted_names_and_abbreviations_do_not_split_sentences():
    protected = det._protect_dots("Built it in Node.js for 3.5% growth, e.g. for staff. Done well.")
    assert protected.count(".") == 2            # only the two real sentence ends survive


def test_a_sentence_that_ends_on_a_dropped_acronym_keeps_its_end():
    prepared = det._without_names("We reported every case to the CQC. We improved things.")
    assert "CQC" not in prepared and prepared.count(".") == 2


def test_skills_lines_are_neither_prose_nor_a_long_sentence():
    items = ", ".join(f"Tool{i}" for i in range(50))
    result = det.run(f"**Platforms:** {items}\n**Methods:** {items}\n")
    assert "sentence_length" not in _types(result) and "readability" not in _types(result)


def test_real_prose_is_still_checked_for_readability():
    dense = ("Notwithstanding organisational heterogeneity, interdepartmental collaboration "
             "necessitates comprehensive institutionalised standardisation of multidimensional "
             "operational methodologies throughout organisational infrastructures consistently.")
    assert "readability" in _types(det.run(dense))


# ── Plumbing ──────────────────────────────────────────────────────────────────

def test_the_accuracy_judge_gets_standing_rules_and_the_ad_marked_as_not_evidence():
    ctx = _ctx(additions="Never call anyone a customer; say clients.")
    system, user = cheap_llm.build_prompt("- Some claim.", ctx)
    assert "Never call anyone a customer" in user and "STANDING RULES" in user
    assert "JOB AD (NOT evidence" in user and "regional lead" in user
    assert "GENERATED CV SECTION" in user
    assert "How to judge each factual claim" in system and "JOB AD may be shown" in system


def test_without_additions_or_ad_those_blocks_are_left_out():
    ctx = _ctx(additions="", jd="")
    _, user = cheap_llm.build_prompt("- Some claim.", ctx)
    assert "STANDING RULES" not in user and "JOB AD" not in user


def test_evaluate_hands_the_context_to_both_tiers(monkeypatch):
    seen = {}

    def fake_t1(text, ctx=None):
        seen["t1"] = ctx
        return det.DeterministicResult(passed=True, flesch_score=100.0)

    def fake_t2(text, ctx=None):
        seen["t2"] = ctx
        return cheap_llm.CheapLLMResult(passed=True, model="m", prompt_tokens=0, completion_tokens=0)

    monkeypatch.setattr(det, "run", fake_t1)
    monkeypatch.setattr(cheap_llm, "run", fake_t2)
    ctx = _ctx()
    orchestrator.evaluate("text", "r1", ctx)
    assert seen["t1"] is ctx and seen["t2"] is ctx


def test_retry_hides_the_excerpt_for_profile_shape_flags():
    from pipeline.retry import build_constraint_block

    block = build_constraint_block([
        {"type": "profile_shape", "message": "Profile has 1 paragraph", "excerpt": "WHOLE PROFILE TEXT"},
        {"type": "fragment", "message": "Bullet is only 4 words", "excerpt": "Led the Leeds team."},
    ])
    assert "WHOLE PROFILE TEXT" not in block and "Led the Leeds team." in block


# ── Batch 2 checks, verified against the brief ─────────────────────────────────

BANS = (
    "## Wording\n"
    '- Never use the words "genuine" or "genuinely".\n'
    "- Banned words: passionate / thrilled\n"
    '- Always write "Registered Manager" in full, never "RM".\n'
)


def _hot(result) -> list:
    return [f.excerpt.lower() for f in result.flags if f.type == "hotword"]


def test_words_banned_in_the_personal_additions_are_flagged():
    text = "A genuinely careful and passionate leader who is thrilled by the work and genuine."
    hot = _hot(det.run(text, _ctx(section="profile", additions=BANS)))
    assert {"genuinely", "genuine", "passionate", "thrilled"} <= set(hot)


def test_a_banned_word_is_not_flagged_when_there_are_no_such_rules():
    assert "genuine" not in _hot(det.run("A genuine leader.", _ctx(section="profile")))


def test_an_instruction_that_only_names_wording_to_use_is_not_read_as_a_ban():
    text = "The Registered Manager ran the service."
    assert _hot(det.run(text, _ctx(section="profile", additions=BANS))) == []


def test_third_person_pronouns_in_the_profile_are_flagged():
    text = "She leads teams and her work shows it. They value his judgement and their results."
    flags = [f for f in det.run(text, _ctx(section="profile")).flags if f.type == "third_person"]
    assert {f.excerpt.lower() for f in flags} >= {"she", "her", "they", "his", "their"}


def test_the_third_person_check_can_be_switched_off_per_instance(monkeypatch):
    monkeypatch.setattr(config, "FLAG_THIRD_PERSON", False, raising=False)
    assert "third_person" not in _types(det.run("She leads teams.", _ctx(section="profile")))


def test_third_person_pronouns_are_only_checked_in_the_profile():
    # in a role, "their" is about someone else ("coached managers to own their decisions")
    text = "- Coached the managers to own their decisions across all three branches of the group.\n"
    assert "third_person" not in _types(det.run(text, _ctx(section="experience")))


def test_passive_voice_in_the_profile_is_advisory():
    text = "Rotas were redesigned and the service was transformed by the team."
    flags = [f for f in det.run(text, _ctx(section="profile")).flags if f.type == "passive"]
    assert flags and all(getattr(f, "severity", "") == "advisory" for f in flags)


def test_an_advisory_flag_alone_does_not_fail_the_section(monkeypatch):
    from pipeline.judges.deterministic import DeterministicFlag
    monkeypatch.setattr(det, "_check_passive", lambda t: [DeterministicFlag(
        "passive", 0, 1, "x", "advisory", severity="advisory")], raising=False)
    # a profile of the right shape, so nothing else fires
    ok = " ".join(["Led teams of twelve across three branches and kept everyone safe."] * 1)
    monkeypatch.setattr(config, "PROFILE_PARAGRAPHS", 1)
    monkeypatch.setattr(config, "PROFILE_WORDS_MIN", 1)
    monkeypatch.setattr(config, "PROFILE_WORDS_MAX", 200)
    result = det.run(ok, _ctx(section="profile", jd=""))
    assert [f.type for f in result.flags] == ["passive"] and result.passed


def test_active_voice_is_not_flagged_as_passive():
    text = "I redesigned the rotas and the team transformed the service."
    assert "passive" not in _types(det.run(text, _ctx(section="profile")))


def test_a_single_ad_word_used_four_times_is_flagged_even_if_the_library_uses_it():
    library = LIBRARY + " Ran clinical governance for the branches."
    four = "Clinical lead. Clinical audits. Clinical rotas. Clinical notes."
    three = "Clinical lead. Clinical audits. Clinical rotas."
    flags = [f for f in det.run("- Ran a clinical rota.\n", _ctx(library=library, cv_text=four)).flags
             if f.type == "ad_echo"]
    assert any("clinical" in f.message.lower() and "4 times" in f.message for f in flags)
    flags = [f for f in det.run("- Ran a clinical rota.\n", _ctx(library=library, cv_text=three)).flags
             if f.type == "ad_echo"]
    assert not any("clinical" in f.message.lower() for f in flags)


HEAVY = ("We ran pharmacovigilance, interdisciplinary neurodevelopmental multidisciplinary "
         "rehabilitation programmes and kept every patient safe and well looked after by the team.")


def test_words_the_library_uses_do_not_drag_readability_down():
    lib = "pharmacovigilance interdisciplinary neurodevelopmental multidisciplinary rehabilitation"
    assert det._flesch_reading_ease(det._protect_dots(HEAVY)) < det.FLESCH_MIN_SCORE
    assert "readability" in _types(det.run(HEAVY))                                   # no context
    assert "readability" not in _types(det.run(HEAVY, _ctx(section="profile", library=lib, jd="")))
