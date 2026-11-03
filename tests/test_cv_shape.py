"""
CV header from META, profile shape, private library sections, PDF style settings, and the
experience-file ordering. None of this touches the database or needs fonts.
"""

import json

import pytest

import pipeline.config as config
from pipeline import library, meta, privacy, profile_shape
from pipeline.parse_cv import EducationEntry, ExperienceEntry, ParsedCV, parse_cv
from pipeline.render import _build_contact_line, _display_url, _link_rule, _sh, build_typst_doc
from pipeline.sections import _clean_years, split_cv_sections

META_TEXT = """\
# Jane Doe

**Email:** jane@example.com
**Phone:** +44 7700 900000
**Location:** Leeds, UK
**LinkedIn:** [jane](https://www.linkedin.com/in/jane-doe/)
- Website: https://jane.example
"""


# ── META ──────────────────────────────────────────────────────────────────────

def test_meta_parses_the_common_layouts():
    m = meta.parse_meta(META_TEXT)
    assert (m.name, m.email, m.phone, m.location) == ("Jane Doe", "jane@example.com", "+44 7700 900000", "Leeds, UK")
    assert m.linkedin == "https://www.linkedin.com/in/jane-doe/"
    assert m.website == "https://jane.example"
    assert m.missing_required() == []


def test_meta_accepts_plain_and_bold_after_colon_labels():
    m = meta.parse_meta("Name: Sam Roe\nE-mail: sam@x.org\n**Location**: Hull\n")
    assert (m.name, m.email, m.location) == ("Sam Roe", "sam@x.org", "Hull")


def test_meta_website_is_optional():
    m = meta.parse_meta("# Sam\nEmail: sam@x.org\nLocation: Hull\n")
    assert m.website == "" and m.missing_required() == []
    assert m.contact()["website"] == ""


def test_meta_do_not_include_beats_a_value():
    m = meta.parse_meta(META_TEXT + "Personal website: https://old.example - DO NOT INCLUDE\n")
    # the first Website line wins only if not excluded; an exclusion clears it
    assert m.website == "" and "website" in m.excluded


def test_meta_reports_what_is_missing():
    m = meta.parse_meta("# Sam\nEmail: sam@x.org\n")
    assert m.missing_required() == ["location"]


def test_meta_problems_names_the_missing_fields(tmp_path, monkeypatch):
    f = tmp_path / "meta.md"
    f.write_text("# Sam\n", encoding="utf-8")
    monkeypatch.setattr(config, "META_PATH", f)
    problems = meta.meta_problems()
    assert problems and "Email" in problems[0] and "Location" in problems[0]
    monkeypatch.setattr(config, "META_PATH", tmp_path / "missing.md")
    assert "not found" in meta.meta_problems()[0]


def test_header_comes_from_meta_not_from_the_model(tmp_path, monkeypatch):
    from pipeline import tailorer

    f = tmp_path / "meta.md"
    f.write_text(META_TEXT, encoding="utf-8")
    monkeypatch.setattr(config, "META_PATH", f)
    monkeypatch.setattr(tailorer, "LLM_PROVIDER", "anthropic")
    # The model leaves out the contact block and invents a name: neither may reach the CV.
    model_json = json.dumps({
        "reasoning": "r", "name": "Invented Name", "profile": "p",
        "experience": [], "skills": [], "education": [], "certifications": [],
    })
    monkeypatch.setattr(tailorer, "_call_anthropic", lambda s, u: model_json)
    out = tailorer.call_llm("sys", "user")
    assert out["name"] == "Jane Doe"
    assert out["contact"]["email"] == "jane@example.com"
    assert out["contact"]["website"] == "https://jane.example"


def test_missing_meta_fails_before_any_llm_call(tmp_path, monkeypatch):
    from pipeline import tailorer

    f = tmp_path / "meta.md"
    f.write_text("# Sam\n", encoding="utf-8")
    monkeypatch.setattr(config, "META_PATH", f)

    def boom(*a, **k):
        raise AssertionError("the LLM must not be called when META is incomplete")

    monkeypatch.setattr(tailorer, "_call_anthropic", boom)
    with pytest.raises(ValueError, match="Email"):
        tailorer.call_llm("sys", "user")


# ── Profile shape ─────────────────────────────────────────────────────────────

def test_profile_shape_defaults_to_two_paragraphs_80_to_120_words():
    assert profile_shape.paragraphs() == 2
    assert profile_shape.word_bounds() == (80, 120)
    assert "Two paragraphs, 80-120 words" in profile_shape.schema_text()


def test_profile_shape_follows_settings_and_fills_placeholders(monkeypatch):
    monkeypatch.setattr(config, "PROFILE_PARAGRAPHS", 1)
    monkeypatch.setattr(config, "PROFILE_WORDS_MIN", 40)
    monkeypatch.setattr(config, "PROFILE_WORDS_MAX", 60)
    out = profile_shape.apply("{{PROFILE_SCHEMA}} | {{PROFILE_RULES}} | {{PROFILE_RETRY}}")
    assert "{{" not in out
    assert "One paragraph, 40-60 words" in out


def test_shared_generation_prompt_is_neutral_and_fully_filled_in():
    # The shared prompt only: a person's own personal_additions.md is appended separately.
    from pipeline.config import PROMPTS_PATH

    prompt = profile_shape.apply((PROMPTS_PATH / "cv_generation.md").read_text(encoding="utf-8"))
    assert "{{" not in prompt                          # every placeholder is filled
    assert "40-60 words" not in prompt                 # the old hard-coded shape is gone
    assert "Two paragraphs, 80-120 words" in prompt
    assert "contact.website" not in prompt             # the model no longer writes the header
    for stale in ("DataCo", "Zendesk", "mej.xyz", "Aircall", "technical experience"):
        assert stale not in prompt, f"person-specific content in the shared prompt: {stale}"


# ── Profile paragraphs through parse and render ───────────────────────────────

CV_MD = """\
# Jane Doe
jane@example.com · Leeds, UK

---

## Profile

First paragraph about who she is.

Second paragraph with the evidence.

---

## Experience

### Acme -- Head of Ops | Jan 2020 - Present

- Led a team of twelve across three sites in two regions.

### Earlier experience

Built internal tools at Acme Ltd (2011-2015).

---

## Skills

**Ops:** Planning, Rota design

---

## Education

**BA Hons** -- Western University, Expected 2026
*Psychology and Sociology*
- Dean's list 2012
- Vice-Chancellor scholarship

---

## Certifications

- First Aid - 2020
"""


def test_every_profile_paragraph_survives_parsing():
    cv = parse_cv(CV_MD)
    assert len(cv.profile) == 2
    assert cv.profile[1].startswith("Second paragraph")


def test_a_triple_dash_inside_prose_is_not_a_divider():
    cv = parse_cv("# A\nx@y.z\n\n---\n\n## Profile\n\nOne --- two.\n\n---\n")
    assert cv.profile == ["One --- two."]


def test_education_honours_and_expected_year_are_parsed():
    edu = parse_cv(CV_MD).education[0]
    assert edu.years == "Expected 2026"
    assert edu.subjects == "Psychology and Sociology"
    assert edu.honours == ["Dean's list 2012", "Vice-Chancellor scholarship"]


def test_earlier_experience_heading_is_one_title():
    exp = parse_cv(CV_MD).experience
    assert (exp[1].company, exp[1].role) == ("Earlier experience", "")


def test_the_pdf_renders_every_profile_paragraph_with_a_gap():
    doc = build_typst_doc(parse_cv(CV_MD))
    assert doc.count('#par(leading:6.5pt)') == 2
    assert "First paragraph about who she is." in doc and "Second paragraph with the evidence." in doc
    between = doc.split("First paragraph about who she is.")[1].split("Second paragraph")[0]
    assert "#v(6pt)" in between


def test_parsed_cv_still_accepts_a_plain_string_profile():
    cv = ParsedCV(name="A", contact="", profile="One.\n\nTwo.", experience=[], skills=[],
                  education=[], certifications=[])
    assert cv.profile == ["One.", "Two."]


def test_the_pdf_renders_education_honours():
    cv = ParsedCV(
        name="A", contact="", profile=["p"], experience=[], skills=[], certifications=[],
        education=[EducationEntry("BA", "Uni", "2012", None, honours=["Dean's list", "Scholarship"])],
    )
    doc = build_typst_doc(cv)
    assert "Dean's list" in doc and "Scholarship" in doc


# ── Sections ──────────────────────────────────────────────────────────────────

def test_earlier_experience_heading_defaults_and_can_be_changed(monkeypatch):
    import pipeline.sections as sections

    data = {"profile": "p", "experience": [], "earlier_experience": "Old roles.", "skills": [],
            "education": [], "certifications": []}
    assert "### Earlier experience" in split_cv_sections(data)["experience"]
    assert "technical" not in split_cv_sections(data)["experience"]
    monkeypatch.setattr(sections, "EARLIER_EXPERIENCE_HEADING", "Previous roles")
    assert "### Previous roles" in split_cv_sections(data)["experience"]


@pytest.mark.parametrize("raw,clean", [
    ("2012", "2012"), ("Completed, 2012", "2012"), ("Graduated 2012 - 2016", "2012 - 2016"),
    ("Expected 2026", "Expected 2026"), ("2012 - Completed", "2012"),
])
def test_education_years_drop_status_words(raw, clean):
    assert _clean_years(raw) == clean


def test_education_honours_are_composed_as_bullets():
    data = {"profile": "p", "experience": [], "skills": [], "certifications": [],
            "education": [{"degree": "BA", "institution": "Uni", "years": "Completed 2012",
                           "subjects": "Psych", "honours": ["Prize", "Scholarship"]}]}
    edu = split_cv_sections(data)["education"]
    assert "**BA** -- Uni, 2012" in edu and "- Prize" in edu and "- Scholarship" in edu


# ── PDF style settings ────────────────────────────────────────────────────────

def test_heading_rule_settings(monkeypatch):
    monkeypatch.setattr(config, "HEADING_RULE", "full")
    assert "#line(length:100%" in _sh("Profile")
    monkeypatch.setattr(config, "HEADING_RULE", "short")
    assert "#line(length:24pt" in _sh("Profile") and "length:100%" not in _sh("Profile")
    monkeypatch.setattr(config, "HEADING_RULE", "none")
    assert "#line" not in _sh("Profile") and "PROFILE" in _sh("Profile").upper()


def test_link_underline_setting(monkeypatch):
    monkeypatch.setattr(config, "LINK_UNDERLINE", True)
    assert "#underline" in _link_rule()
    monkeypatch.setattr(config, "LINK_UNDERLINE", False)
    assert "#underline" not in _link_rule() and "#show link" in _link_rule()


def test_contact_urls_display_short_but_link_to_the_full_address():
    assert _display_url("https://www.linkedin.com/in/jane-doe/") == "linkedin.com/in/jane-doe"
    line = _build_contact_line("https://www.linkedin.com/in/jane-doe/ · https://jane.example/")
    assert '#link("https://www.linkedin.com/in/jane-doe/")[linkedin.com/in/jane-doe]' in line
    assert '[jane.example]' in line


# ── Private library sections ──────────────────────────────────────────────────

def test_private_heading_hides_its_section_only():
    text = "# Role\n\nKept.\n\n## Private\nHidden fact.\n### Sub\nAlso hidden.\n\n## Next\nKept too.\n"
    out = privacy.strip_private(text)
    assert "Kept." in out and "Kept too." in out
    assert "Hidden fact" not in out and "Also hidden" not in out


@pytest.mark.parametrize("heading", ["Private", "private notes", "Private: do not use", "Private (confidential)"])
def test_private_heading_variants(heading):
    assert "SECRET" not in privacy.strip_private(f"## {heading}\nSECRET\n\n## After\nok\n")


def test_private_sector_is_not_private():
    out = privacy.strip_private("## Private sector experience\nreal content\n")
    assert "real content" in out


def test_private_blocks_and_unclosed_blocks_fail_safe():
    assert "X" not in privacy.strip_private("a <!-- private -->X<!-- /private --> b") and "a" in privacy.strip_private("a <!-- private -->X<!-- /private --> b")
    out = privacy.strip_private("keep\n<!-- private -->\nsecret with no closing tag\nmore secret")
    assert "keep" in out and "secret" not in out


def test_library_files_are_stripped_when_loaded_but_other_files_are_not(tmp_path, monkeypatch):
    from pipeline.tailorer import load_text

    lib = tmp_path / "lib"
    (lib / "experience").mkdir(parents=True)
    note = lib / "experience" / "2020-now_x.md"
    note.write_text("## Work\nok\n## Private\nSECRET\n", encoding="utf-8")
    outside = tmp_path / "prompt.md"
    outside.write_text("## Private\nstays: this is a prompt, not a library file\n", encoding="utf-8")
    monkeypatch.setattr(config, "CV_LIBRARY_PATH", lib)
    assert "SECRET" not in load_text(note) and "ok" in load_text(note)
    assert "stays" in load_text(outside)


def test_mcp_library_tools_hide_private_by_default(tmp_path, monkeypatch):
    import mcp_server.tools_library as tl

    exp = tmp_path / "experience"
    exp.mkdir()
    (exp / "2020-now_acme_head.md").write_text("## Work\nopen fact\n## Private\nSECRET fact\n", encoding="utf-8")
    monkeypatch.setattr(tl, "EXPERIENCE_PATH", exp)
    monkeypatch.setattr(tl, "PERSONAS_PATH", tmp_path / "none")
    monkeypatch.setattr(tl, "SKILLS_PATH", tmp_path / "none.md")

    assert "SECRET" not in tl.get_experience_entry("2020-now_acme_head.md")
    assert "SECRET" in tl.get_experience_entry("2020-now_acme_head.md", include_private=True)
    assert tl.search_library("secret") == []            # private lines are never searchable
    assert tl.search_library("open")                    # public ones are


def test_personal_additions_tool_uses_the_instance_folder():
    import mcp_server.tools_prompt_tuning as tt
    assert tt._ADDITIONS_PATH == config.PERSONAL_PROMPTS_PATH / "personal_additions.md"


# ── Experience file ordering ──────────────────────────────────────────────────

def test_current_roles_sort_first_and_now_present_current_are_accepted(tmp_path):
    names = [
        "2018-2020_a_x.md", "2023-2025_b_y.md", "2025-now_c_z.md", "2019-present_d_w.md",
        "2021-current_e_v.md", "notes.md", "2023-2024_f_u.md",
    ]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")
    order = [p.name for p in library.experience_files(tmp_path)]
    assert order[:3] == ["2025-now_c_z.md", "2021-current_e_v.md", "2019-present_d_w.md"]
    assert order[3:6] == ["2023-2025_b_y.md", "2023-2024_f_u.md", "2018-2020_a_x.md"]
    assert order[-1] == "notes.md"


def test_missing_library_folder_is_empty(tmp_path):
    assert library.experience_files(tmp_path / "nope") == []
