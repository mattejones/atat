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


# ── A bullet that restates its own role's context line ─────────────────────────────────────

CTX_ROLE_A = (
    "### Acme Care — Registered Manager | 2019 - 2023\n"
    "*Ran a twelve-bed residential home for older adults with dementia, managing rotas and safeguarding.*\n"
)
CTX_ROLE_B = (
    "### Beta Homes — Deputy Manager | 2016 - 2019\n"
    "*Supported the registered manager across three branches in the north of England during growth.*\n"
)


def _ce(result) -> list:
    return [f for f in result.flags if f.type == "context_echo"]


def test_a_bullet_that_restates_its_context_line_is_flagged_once():
    text = (
        CTX_ROLE_A
        + "- Ran a residential home for twelve older adults with dementia, managing rotas and safeguarding.\n"
        + "- Cut agency spend by a third by redesigning the rota and recruiting two permanent night staff.\n"
        + "\n" + CTX_ROLE_B
        + "- Trained eight new team leaders to run handovers without escalating every decision upwards.\n"
    )
    flags = _ce(det.run(text, _ctx(section="experience", jd="")))
    assert len(flags) == 1
    assert "residential home for twelve" in flags[0].excerpt
    assert text[flags[0].start_pos:flags[0].end_pos].startswith("- Ran a residential home")


def test_a_context_phrase_repeated_across_roles_is_flagged_once():
    shared = "leading a team of twelve across three branches in the north"
    text = (
        "### Acme Care — Manager | 2019 - 2023\n"
        f"*Registered manager {shared}, with full budget responsibility.*\n"
        "- Cut agency spend by a third by redesigning the rota and recruiting two night staff.\n"
        "\n### Beta Homes — Deputy | 2016 - 2019\n"
        f"*Deputy to the director, {shared}, during a period of fast growth.*\n"
        "- Trained eight new team leaders to run handovers without escalating decisions upwards.\n"
    )
    flags = _ce(det.run(text, _ctx(section="experience", jd="")))
    assert len(flags) == 1
    assert "team of twelve across three branches" in flags[0].message.lower() or \
           "team of twelve across three branches" in flags[0].excerpt.lower()


def test_distinct_contexts_and_bullets_raise_no_context_echo():
    text = (
        CTX_ROLE_A
        + "- Cut agency spend by a third by redesigning the rota and recruiting two permanent night staff.\n"
        + "- Passed two unannounced inspections with no requirement notices in the same year.\n"
        + "\n" + CTX_ROLE_B
        + "- Trained eight new team leaders to run handovers without escalating every decision upwards.\n"
    )
    assert _ce(det.run(text, _ctx(section="experience", jd=""))) == []


def test_context_echo_only_applies_to_the_experience_section():
    text = CTX_ROLE_A + "- Ran a residential home for twelve older adults with dementia, managing rotas.\n"
    assert _ce(det.run(text, _ctx(section="profile", jd=""))) == []


# ── Per-role bullet weighting ("CV weight" header in the experience file) ──────────────────

from pipeline import weights as wt  # noqa: E402


def test_the_weight_header_is_parsed_and_unknown_values_are_ignored():
    assert wt.parse_weight("# Experience: A - B\n**CV weight:** lead\n") == "lead"
    assert wt.parse_weight("**CV weight:** Brief") == "brief"
    assert wt.parse_weight("**CV weight:** huge") is None
    assert wt.parse_weight("no header here") is None


def test_each_weight_maps_to_its_bullet_bounds():
    assert wt.BOUNDS == {"lead": (4, 6), "standard": (2, 4), "brief": (0, 1)}


def _library(tmp_path):
    d = tmp_path / "experience"
    d.mkdir(exist_ok=True)
    (d / "2020-now_acme-care_registered-manager.md").write_text(
        "# Experience: Acme Care — Registered Manager\n**CV weight:** lead\n", encoding="utf-8")
    (d / "2016-2019_beta-homes_deputy.md").write_text(
        "# Experience: Beta Homes — Deputy Manager\n**CV weight:** brief\n", encoding="utf-8")
    (d / "2010-2015_gamma_carer.md").write_text(
        "# Experience: Gamma — Carer\nno weight\n", encoding="utf-8")
    return d


def test_library_weights_read_only_the_files_that_have_one(tmp_path):
    ws = wt.library_weights(_library(tmp_path))
    assert {(w["company"], w["weight"]) for w in ws} == {("Acme Care", "lead"), ("Beta Homes", "brief")}


def test_the_prompt_block_states_counts_and_is_empty_without_weights(tmp_path):
    block = wt.describe(wt.library_weights(_library(tmp_path)))
    assert "Acme Care — Registered Manager: 4 to 6 bullets" in block
    assert "Beta Homes — Deputy Manager: at most 1 bullet" in block
    assert wt.describe([]) == ""


def _weights(tmp_path):
    return wt.library_weights(_library(tmp_path))


def _roles_text(a: int, b: int, c: int) -> str:
    def bl(n):
        return "".join(f"- Bullet number {i} describing a distinct piece of real work done here.\n" for i in range(n))
    return (f"### Acme Care — Registered Manager | 2020 - Present\n{bl(a)}\n"
            f"### Beta Homes — Deputy Manager | 2016 - 2019\n{bl(b)}\n"
            f"### Gamma — Carer | 2010 - 2015\n{bl(c)}")


def _bc(result) -> list:
    return [f for f in result.flags if f.type == "bullet_count"]


def test_roles_inside_their_bounds_raise_no_bullet_count_flag(tmp_path):
    ctx = _ctx(section="experience", jd="")
    ctx.role_weights = _weights(tmp_path)
    assert _bc(det.run(_roles_text(5, 1, 9), ctx)) == []        # Gamma has no weight: any count


def test_too_few_and_too_many_bullets_are_flagged_per_role(tmp_path):
    ctx = _ctx(section="experience", jd="")
    ctx.role_weights = _weights(tmp_path)
    text = _roles_text(2, 3, 1)
    flags = _bc(det.run(text, ctx))
    assert len(flags) == 2
    assert "Acme Care has 2 bullets" in flags[0].message and "4-6" in flags[0].message
    assert "Beta Homes has 3 bullets" in flags[1].message and "0-1" in flags[1].message
    assert text[flags[0].start_pos:flags[0].end_pos].startswith("### Acme Care")


def test_without_weights_nothing_changes(tmp_path):
    assert _bc(det.run(_roles_text(1, 9, 0), _ctx(section="experience", jd=""))) == []


def test_generation_prompt_carries_the_bullet_counts_only_when_weights_exist(tmp_path, monkeypatch):
    import pipeline.tailorer as tailorer
    weighted = _weights(tmp_path)
    monkeypatch.setattr(wt, "library_weights", lambda *a, **k: weighted)
    assert "BULLETS PER ROLE" in tailorer._bullets_block()
    monkeypatch.setattr(wt, "library_weights", lambda *a, **k: [])
    assert tailorer._bullets_block() == ""


# ── Weights must work for libraries whose titles have no "Experience:" prefix ──────────────

def _bare_library(tmp_path):
    d = tmp_path / "bare"
    d.mkdir()
    (d / "2020-now_oxford_md.md").write_text(
        "# Oxford Aunts — Managing Director\n**CV weight:** lead\n", encoding="utf-8")
    (d / "2018-2020_carebrain_x.md").write_text(
        "# CareBrain - Head of Care\n**CV weight:** brief\n", encoding="utf-8")
    (d / "2015-2018_good_area.md").write_text(
        "# The Good Care Group — Area Manager, South\n**CV weight:** brief\n", encoding="utf-8")
    return d


def test_titles_without_the_experience_prefix_are_read(tmp_path):
    ws = wt.library_weights(_bare_library(tmp_path))
    assert {(w["company"], w["weight"]) for w in ws} == {
        ("Oxford Aunts", "lead"), ("CareBrain", "brief"), ("The Good Care Group", "brief")}


def test_a_cv_heading_with_a_suffix_still_matches_its_library_company(tmp_path):
    ws = wt.library_weights(_bare_library(tmp_path))
    assert wt.weight_for("Oxford Aunts (The Good Care Group)", "Managing Director", ws)["weight"] == "lead"
    assert wt.weight_for("CareBrain (acquired by QCS)", "Head of Care", ws)["weight"] == "brief"
    assert wt.weight_for("The Good Care Group", "Area Manager, South", ws)["weight"] == "brief"
    assert wt.weight_for("Unrelated Ltd", "X", ws) is None


def test_a_longer_company_name_wins_over_a_shorter_prefix():
    ws = [{"company": "Care", "role": "", "weight": "brief", "min": 0, "max": 1},
          {"company": "Care Brain", "role": "", "weight": "lead", "min": 4, "max": 6}]
    assert wt.weight_for("Care Brain (acquired)", "", ws)["weight"] == "lead"


def test_the_judge_counts_a_suffixed_heading_against_its_weight(tmp_path):
    ctx = _ctx(section="experience", jd="")
    ctx.role_weights = wt.library_weights(_bare_library(tmp_path))
    bl = "".join(f"- Bullet number {i} describing a distinct piece of real work done here.\n" for i in range(2))
    text = f"### Oxford Aunts (The Good Care Group) — Managing Director | 2020 - Present\n{bl}"
    flags = _bc(det.run(text, ctx))
    assert len(flags) == 1 and "calls for 4-6" in flags[0].message


def test_a_weighted_file_without_a_parseable_title_is_warned_about(tmp_path, caplog):
    d = tmp_path / "notitle"
    d.mkdir()
    (d / "2020-now_x.md").write_text("Just some text\n**CV weight:** lead\n", encoding="utf-8")
    import logging
    with caplog.at_level(logging.WARNING):
        assert wt.library_weights(d) == []
    assert "2020-now_x.md" in caplog.text and "title" in caplog.text.lower()


# ── Common words must not be flagged as hotwords or repetition ─────────────────────────────

def test_ban_terms_never_include_stopwords_punctuation_or_tiny_words():
    additions = (
        "Banned words: genuine, over, or, in, ,\n"
        'Avoid the word "in" and "CVs" in headings, and "," too.\n'
        "Don't use passionate, in, or thrilled.\n"
    )
    terms = [t.lower() for t in det.banned_terms(additions)]
    assert "genuine" in terms and "passionate" in terms and "thrilled" in terms
    for junk in ("in", "or", "over", ","):
        assert junk not in terms


def test_a_quoted_word_the_rule_says_to_use_instead_is_not_banned():
    additions = 'Never use "customers" for the people we support, use "service users" instead.\n'
    assert [t.lower() for t in det.banned_terms(additions)] == ["customers"]


def test_a_prose_colon_line_is_not_split_into_ban_terms():
    additions = ("Avoid anything that reads as filler: it should be short, and it should say what "
                 "you did in plain words, or it should go.\n")
    assert det.banned_terms(additions) == []


def test_common_words_in_a_text_are_not_hotwords_when_rules_are_noisy():
    additions = "Banned words: genuine, over, or, in, ,\n"
    text = "Led customers in the north, over three years, or more."
    assert _hot(det.run(text, _ctx(section="profile", additions=additions))) == []


def _bullets(word: str, n: int) -> str:
    sentences = [
        "Resolved {w} complaints within two days using a new escalation route across the region.",
        "Trained eight supervisors to coach {w} through difficult conversations without delay.",
        "Redesigned the weekly rota so {w} always met a familiar face on arrival.",
        "Negotiated a lower supplier price while protecting what {w} actually noticed.",
        "Audited every incident log monthly and reported themes upwards to directors.",
    ]
    return "### Acme — Lead | 2020 - 2023\n" + "".join(
        "- " + sentences[i].format(w=word) + "\n" for i in range(n))


def test_a_generic_workplace_word_repeated_in_a_role_is_not_flagged():
    for word in ("customers", "team", "service"):
        assert "repetition" not in _types(det.run(_bullets(word, 3), _ctx(section="experience", jd="")))


def test_a_word_the_persons_own_library_uses_a_lot_is_not_flagged_as_repetition():
    lib = " ".join(["rehabilitation programmes"] * 8)
    ctx = _ctx(section="experience", jd="", library=lib)
    assert "repetition" not in _types(det.run(_bullets("rehabilitation", 3), ctx))


def test_a_distinctive_word_repeated_is_still_flagged():
    assert "repetition" in _types(det.run(_bullets("tribunal", 3), _ctx(section="experience", jd="")))
