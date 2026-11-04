"""
Tests for pipeline/render.py — Typst document generation.

Covers the experience-bullet and certification-bullet markers: a previous
implementation used a hardcoded `#v()` nudge to position each bullet circle,
tuned for one font-size/leading combination, which drifted out of alignment
whenever those values changed (visible as a bullet floating above the text
line instead of sitting centered on it).

These tests operate at two different levels of rigor - see each docstring:

  * test_*_marker_does_not_use_a_hardcoded_offset - a cheap regression
    tripwire on the generated source text. It proves the fragile pattern
    hasn't crept back in, but says nothing about whether the output is
    actually centered.

  * test_bullet_list_compiles_with_typst - proves the document is valid
    Typst that a real compiler accepts. Says nothing about layout.

  * test_bullet_marker_dot_is_geometrically_centered_on_the_text_line -
    the real proof. It asks Typst's own layout engine (via #context/
    measure()/query()) where the marker and the body text actually end up
    on the page, and asserts their vertical centers coincide to within
    0.05pt. This is independent of *how* the marker is implemented - it
    would catch a regression even if someone rewrote _bullet_marker() to
    use a completely different technique that happened to be off-center.
"""

import json
from pathlib import Path

import pytest

from pipeline.parse_cv import EducationEntry, ExperienceEntry, ParsedCV
from pipeline.render import P, _bullet_marker, build_typst_doc

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

pytestmark = pytest.mark.skipif(
    not FONTS_DIR.exists() or not any(FONTS_DIR.glob("*.ttf")),
    reason="Poppins fonts not available in this environment",
)


def _sample_cv(bullets: list[str]) -> ParsedCV:
    return ParsedCV(
        name="Matt Jones",
        contact="matt@example.com · +44 7700 900000",
        profile="A test profile.",
        experience=[
            ExperienceEntry(
                company="Aircall",
                role="Manager, Go-To-Market Systems",
                dates="Jan 2025 - Mar 2026",
                context="CCaaS SaaS platform, Series D.",
                bullets=bullets,
            )
        ],
        skills=[("CRM", "HubSpot, Salesforce")],
        education=[
            EducationEntry(
                degree="BSc Computer Science",
                institution="Example University",
                years="2012 - 2016",
                subjects=None,
            )
        ],
        certifications=["Example Certification"],
    )


def test_experience_marker_does_not_use_a_hardcoded_offset():
    cv = _sample_cv(["Led the full migration from Marketo to HubSpot."])
    doc = build_typst_doc(cv)
    assert "#v(3pt)#circle" not in doc


def test_certification_marker_does_not_use_a_hardcoded_offset():
    cv = _sample_cv(["A bullet."])
    doc = build_typst_doc(cv)
    assert "#v(4pt)#circle" not in doc


def test_bullet_list_omitted_when_no_bullets():
    cv = _sample_cv([])
    doc = build_typst_doc(cv)
    assert "#list(" not in doc


def test_bullet_list_compiles_with_typst(tmp_path):
    typst_lib = pytest.importorskip("typst")

    cv = _sample_cv([
        "Led the full migration from Marketo to HubSpot, building the "
        "business case, evaluating platforms, and delivering the "
        "end-to-end implementation across a six-month timeline.",
        "Designed lead routing architecture within HubSpot Flows.",
    ])
    doc = build_typst_doc(cv)

    typ_path = tmp_path / "cv.typ"
    typ_path.write_text(doc, encoding="utf-8")
    pdf_path = tmp_path / "cv.pdf"

    compiler = typst_lib.Compiler(
        input=str(typ_path),
        root=str(tmp_path),
        font_paths=[str(FONTS_DIR)],
        ignore_system_fonts=True,
    )
    compiler.compile(output=str(pdf_path), format="pdf")

    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0


def _pt(s: str) -> float:
    return float(s.rstrip("pt"))


def _query_one(compiler, label: str):
    return json.loads(compiler.query(selector=label, field="value"))[0]


def test_bullet_marker_dot_is_geometrically_centered_on_the_text_line(tmp_path):
    """
    Ground-truth geometry check via Typst's own layout engine, not a guess
    about how #list aligns cells. We label the top of the marker cell and
    the top of the body cell with #metadata(here().position()), and
    measure() the *actual* rendered height of the real _bullet_marker(8)
    output alongside the real height of a representative bullet paragraph.

    Both heights are measured by re-running the exact same content through
    Typst's measure() (not by re-deriving an expected value some other
    way), so this fails if _bullet_marker() ever regresses to a size that
    doesn't match the text it sits next to - e.g. a hardcoded #v() offset
    or a static "1em" that doesn't equal the font's real line metrics. If
    both cells start at the same row-top (asserted explicitly) and both
    measured heights match, then - since align(center+horizon) is Typst's
    own well-defined primitive, not something we need to re-verify - the
    circle's vertical center is mathematically guaranteed to equal the
    text line's vertical center.
    """
    typst_lib = pytest.importorskip("typst")

    bullet_text = "Led the full migration from Marketo to HubSpot."
    marker = _bullet_marker(8)
    doc = f'''
#set page(width: 300pt, height: 150pt, margin: 10pt)
#set text(font: "Poppins", size: 9.5pt, fill: rgb("{P["body"]}"))

#list(
  marker:[#context [#metadata(here().position()) <marker-top>]{marker}],
  indent:0pt,spacing:5pt,body-indent:10pt,
  [#context [#metadata(here().position()) <body-top>]#par(leading:5.8pt)[#text(font:"Poppins",size:9pt,weight:"regular",fill:rgb("{P["body"]}"))[{bullet_text}]]]
)

#context [#metadata(measure([{marker}]).height) <marker-height>]
#context [#metadata(measure([#par(leading:5.8pt)[#text(font:"Poppins",size:9pt,weight:"regular")[{bullet_text}]]]).height) <body-line-height>]
'''
    typ_path = tmp_path / "geometry.typ"
    typ_path.write_text(doc, encoding="utf-8")

    compiler = typst_lib.Compiler(
        input=str(typ_path),
        root=str(tmp_path),
        font_paths=[str(FONTS_DIR)],
        ignore_system_fonts=True,
    )

    marker_top = _query_one(compiler, "<marker-top>")
    body_top = _query_one(compiler, "<body-top>")
    marker_h = _pt(_query_one(compiler, "<marker-height>"))
    body_h = _pt(_query_one(compiler, "<body-line-height>"))

    assert marker_top["y"] == body_top["y"], (
        "marker and body cells are not row-aligned - the height comparison "
        "below would not imply a center comparison"
    )

    marker_center = _pt(marker_top["y"]) + marker_h / 2
    body_center = _pt(body_top["y"]) + body_h / 2
    assert abs(marker_center - body_center) < 0.05, (
        f"bullet dot center ({marker_center}pt) is not aligned with the "
        f"text line center ({body_center}pt)"
    )


# ── Profile paragraphs, education honours, and the style settings must be valid Typst ──────────

def _compile(doc: str, tmp_path) -> int:
    typst_lib = pytest.importorskip("typst")
    typ_path = tmp_path / "cv.typ"
    typ_path.write_text(doc, encoding="utf-8")
    pdf_path = tmp_path / "cv.pdf"
    typst_lib.Compiler(
        input=str(typ_path), root=str(tmp_path),
        font_paths=[str(FONTS_DIR)], ignore_system_fonts=True,
    ).compile(output=str(pdf_path), format="pdf")
    return pdf_path.stat().st_size


def _rich_cv() -> ParsedCV:
    return ParsedCV(
        name="Jane Doe",
        contact="jane@example.com · +44 7700 900000 · Leeds, UK · "
                "https://www.linkedin.com/in/jane-doe/ · https://jane.example/",
        profile=["First paragraph about who she is.", "Second paragraph with the evidence."],
        experience=[ExperienceEntry("Acme", "Head of Ops", "Jan 2020 - Present", "Context.", ["A bullet that is long enough."])],
        skills=[("Ops", "Planning, Rota design")],
        education=[EducationEntry("BA Hons", "Western University", "Expected 2026", "Psychology",
                                  honours=["Dean's list 2012", "Vice-Chancellor scholarship"])],
        certifications=["First Aid"],
    )


@pytest.mark.parametrize("rule", ["full", "short", "none"])
@pytest.mark.parametrize("underline", [True, False])
def test_every_style_setting_compiles(tmp_path, monkeypatch, rule, underline):
    import pipeline.config as config

    monkeypatch.setattr(config, "HEADING_RULE", rule)
    monkeypatch.setattr(config, "LINK_UNDERLINE", underline)
    assert _compile(build_typst_doc(_rich_cv()), tmp_path) > 0


def test_two_paragraph_profile_compiles_and_shows_both(tmp_path):
    typst_lib = pytest.importorskip("typst")
    doc = build_typst_doc(_rich_cv())
    assert doc.count("#par(leading:6.5pt)") == 2
    assert _compile(doc, tmp_path) > 0


# ── ATS readability: section headings must extract as whole words ───────────────────────────

SECTION_WORDS = ("PROFILE", "EXPERIENCE", "SKILLS", "EDUCATION", "CERTIFICATIONS")


def _pdf_text(doc: str, tmp_path) -> str:
    """Render a Typst document and extract its text the way an ATS parser would."""
    pypdf = pytest.importorskip("pypdf")
    _compile(doc, tmp_path)
    reader = pypdf.PdfReader(str(tmp_path / "cv.pdf"))
    return "\n".join(page.extract_text() for page in reader.pages)


def _missing_sections(text: str) -> list:
    import re
    return [w for w in SECTION_WORDS if not re.search(rf"\b{w}\b", text, re.IGNORECASE)]


def test_default_headings_extract_as_whole_words(tmp_path):
    text = _pdf_text(build_typst_doc(_rich_cv()), tmp_path)
    assert _missing_sections(text) == [], f"headings did not extract cleanly:\n{text[:400]}"


@pytest.mark.parametrize("rule", ["full", "short", "none"])
def test_headings_extract_whatever_the_rule_style(tmp_path, monkeypatch, rule):
    import pipeline.config as config
    monkeypatch.setattr(config, "HEADING_RULE", rule)
    assert _missing_sections(_pdf_text(build_typst_doc(_rich_cv()), tmp_path)) == []


def test_wide_tracking_breaks_extraction_which_is_why_the_default_is_small(tmp_path, monkeypatch):
    # The control: the same CV with the old 1.8pt spacing reads "P R O F I L E" to a parser.
    import pipeline.config as config
    monkeypatch.setattr(config, "HEADING_TRACKING", 1.8)
    text = _pdf_text(build_typst_doc(_rich_cv()), tmp_path)
    assert _missing_sections(text), "wide tracking was expected to break extraction"


def test_the_tracking_default_is_below_the_extraction_threshold():
    import pipeline.config as config
    # Extractors insert a space once a gap passes ~10% of the font size (7.5pt -> 0.75pt).
    assert 0 <= config.HEADING_TRACKING <= 0.6
    assert "tracking:0.5pt" in build_typst_doc(_rich_cv())


# ── Library text must never be read as Typst markup ───────────────────────────────────────────

TRICKY = "~400 staff, *not* bold, _not_ italic, a // b, [x], = heading"


def _tricky_cv() -> ParsedCV:
    return ParsedCV(
        name="Jane Doe",
        contact="jane@example.com · Leeds, UK",
        profile=[f"Profile: {TRICKY}", "- not a list item, + nor this one, 1. nor a number"],
        experience=[ExperienceEntry(
            company="Acme (the *big* one)", role="Head of #Ops", dates="Jan 2020 - Present",
            context=f"Context: {TRICKY}",
            bullets=[f"Bullet: {TRICKY}", "A bullet ending with a backtick ` and a $dollar and <tag> and @ref."],
        )],
        skills=[("Ops", f"Skills: {TRICKY}")],
        education=[EducationEntry("BA (Hons) = Psychology", "Uni // Leeds", "2012", None,
                                  honours=[f"Honour: {TRICKY}"])],
        certifications=[f"Cert: {TRICKY}"],
    )


def _squash(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", s).strip()


def test_markup_characters_in_library_text_come_through_unchanged(tmp_path):
    text = _squash(_pdf_text(build_typst_doc(_tricky_cv()), tmp_path))
    for label in ("Profile", "Context", "Bullet", "Skills", "Honour", "Cert"):
        assert f"{label}: {TRICKY}" in text, f"{label} field was altered:\n{text[:600]}"
    assert "BA (Hons) = Psychology" in text and "Uni // Leeds" in text
    assert "Acme (the *big* one)" in text and "Head of #Ops" in text
    assert "backtick ` and a $dollar and <tag> and @ref." in text


def test_a_slash_comment_inside_a_bullet_no_longer_swallows_the_rest(tmp_path):
    cv = _tricky_cv()
    cv.experience[0].bullets = ["Led the Leeds team // then the York team, and kept both on budget."]
    assert "then the York team, and kept both on budget." in _squash(_pdf_text(build_typst_doc(cv), tmp_path))


def test_apostrophes_are_still_typographic():
    from pipeline.render import esc
    assert esc("Dean's list") == '#"Dean\u2019s list";'
    assert esc("a 'quoted' word") == '#"a \u2018quoted\u2019 word";'
    assert esc("") == ""


def test_text_is_a_string_literal_so_no_character_needs_escaping():
    from pipeline.render import esc
    out = esc('~ * _ ` [ ] // /* = - + # $ < > @ \ " end')
    assert out.startswith('#"') and out.endswith('";')
    assert '\\"' in out and '\\' in out        # only the string's own quote and backslash are escaped


# ── A role header must travel with its first bullet ─────────────────────────────────────────

def _page_texts(doc: str, tmp_path) -> list:
    pypdf = pytest.importorskip("pypdf")
    _compile(doc, tmp_path)
    return [p.extract_text() for p in pypdf.PdfReader(str(tmp_path / "cv.pdf")).pages]


def _boundary_cv(filler_lines: int) -> ParsedCV:
    return ParsedCV(
        name="Jane Doe",
        contact="jane@example.com",
        profile=[f"Filler line {i}." for i in range(filler_lines)],
        experience=[
            ExperienceEntry("FirstCo", "Lead", "2020 - 2022", "", ["Opening bullet here."]),
            ExperienceEntry("ZEBRACO", "Manager", "2018 - 2020", "A context line.",
                            ["FIRSTBULLETMARK did a thing.", "Second bullet.", "Third bullet.",
                             "Fourth bullet."]),
        ],
        skills=[("Ops", "Planning")],
        education=[], certifications=[],
    )


def test_a_role_header_is_never_left_at_the_foot_of_a_page_without_its_first_bullet(tmp_path):
    """Sweep the amount of text above the role so its header lands at every position near the
    page break, including the last ~40pt of page 1. Wherever the company name is, the first bullet
    must be on the same page."""
    stranded, seen_break = [], False
    for n in range(10, 60):
        pages = _page_texts(build_typst_doc(_boundary_cv(n)), tmp_path)
        co_page = next((i for i, t in enumerate(pages) if "ZEBRACO" in t), None)
        bu_page = next((i for i, t in enumerate(pages) if "FIRSTBULLETMARK" in t), None)
        assert co_page is not None and bu_page is not None
        if co_page == 1:
            seen_break = True
        if co_page != bu_page:
            stranded.append((n, co_page, bu_page))
    assert seen_break, "sweep never pushed the role onto page 2, so it proved nothing"
    assert not stranded, f"role header separated from its first bullet at: {stranded}"


# ── Page-count check ───────────────────────────────────────────────────────────────────────

def _layout_of(cv: ParsedCV, tmp_path) -> dict:
    """Compile a CV and read back the layout record render_cv writes beside the PDF."""
    typst_lib = pytest.importorskip("typst")
    from pipeline.render import _record_layout, read_layout
    typ = tmp_path / "cv.typ"
    typ.write_text(build_typst_doc(cv), encoding="utf-8")
    compiler = typst_lib.Compiler(input=str(typ), root=str(tmp_path),
                                  font_paths=[str(FONTS_DIR)], ignore_system_fonts=True)
    compiler.compile(output=str(tmp_path / "cv.pdf"), format="pdf")
    _record_layout(compiler, tmp_path)
    return read_layout(tmp_path)


def _cv_of_roles(n_roles: int) -> ParsedCV:
    roles = [ExperienceEntry(f"Company{i}", "Role", "2020 - 2021", "Context line.",
                             [f"Bullet number {j} describing some work done." for j in range(4)])
             for i in range(n_roles)]
    return ParsedCV(name="Jane Doe", contact="jane@example.com", profile=["A profile."],
                    experience=roles, skills=[("Ops", "Planning")], education=[], certifications=[])


def test_page_count_is_recorded_and_matches_the_pdf(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    for n_roles in (2, 8):
        d = tmp_path / str(n_roles)
        d.mkdir()
        layout = _layout_of(_cv_of_roles(n_roles), d)
        assert layout["pages"] == len(pypdf.PdfReader(str(d / "cv.pdf")).pages)


def test_a_three_page_cv_is_flagged_and_a_two_page_one_is_not(tmp_path):
    from pipeline.render import length_warning
    (tmp_path / "short").mkdir()
    (tmp_path / "long").mkdir()
    short = _layout_of(_cv_of_roles(9), tmp_path / "short")
    long_ = _layout_of(_cv_of_roles(20), tmp_path / "long")
    assert short["pages"] == 2 and long_["pages"] >= 3
    assert length_warning(short, 2) is None
    msg = length_warning(long_, 2)
    assert msg and f"{long_['pages']} pages" in msg and "limit is 2" in msg


def test_the_page_limit_can_be_turned_off_and_overflow_is_described():
    from pipeline.render import length_warning
    assert length_warning({"pages": 5, "end_y_pt": 300}, 0) is None
    assert length_warning(None, 2) is None
    msg = length_warning({"pages": 3, "end_y_pt": 45.35 + 13.5 * 9}, 2)
    assert "about 9 lines onto page 3" in msg


def test_the_length_warning_reaches_the_warning_list_the_unreviewed_warning_uses(tmp_path, monkeypatch):
    import sqlite3
    import pipeline.config as config
    from pipeline.review_status import warning_fields

    layout_dir = tmp_path / "app1"
    layout_dir.mkdir()
    (layout_dir / "cv.layout.json").write_text('{"pages": 3, "end_y_pt": 100}', encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE applications (id TEXT, output_dir TEXT)")
    db.execute("CREATE TABLE sections (application_id TEXT, section_name TEXT, accepted_report_id TEXT)")
    db.execute("INSERT INTO applications VALUES ('app1', ?)", (str(layout_dir),))

    monkeypatch.setattr(config, "MAX_CV_PAGES", 2)
    assert any("3 pages" in w for w in warning_fields(db, "app1")["warnings"])
    monkeypatch.setattr(config, "MAX_CV_PAGES", 3)
    assert warning_fields(db, "app1")["warnings"] == []


# ── Education block spacing ────────────────────────────────────────────────────────────────

def _edu_cv() -> ParsedCV:
    long_honour = ("Awarded the Faculty Prize for outstanding achievement across the programme, "
                   "together with a named university scholarship and a place on the dean's list "
                   "in each year of study, recognised at the annual ceremony")
    return ParsedCV(
        name="Jane Doe", contact="jane@example.com", profile=["A profile."],
        experience=[ExperienceEntry("Acme", "Lead", "2020 - 2022", "", ["A bullet."])],
        skills=[("Ops", "Planning")],
        education=[EducationEntry("BA Hons", "Western University", "2008 - 2011",
                                  "Psychology; Sociology",
                                  honours=[long_honour, "Short prize 2010", long_honour])],
        certifications=[],
    )


def test_education_spacing_is_explicit_in_the_typst():
    doc = build_typst_doc(_edu_cv())
    edu = doc[doc.index("Western University"):]
    # a gap after the institution line (5pt) and after the majors line (6pt)
    assert "#v(5pt)" in edu and "#v(6pt)" in edu
    # every honours line is its own paragraph with its own leading, behind a dot marker
    assert edu.count("#par(leading:6pt)") == 3
    # and a gap between consecutive honours lines (two gaps for three lines)
    assert edu.count("#v(5pt)") >= 3


def test_education_with_wrapping_honours_renders_and_saves_a_2x_png(tmp_path):
    typst_lib = pytest.importorskip("typst")
    typ = tmp_path / "cv.typ"
    typ.write_text(build_typst_doc(_edu_cv()), encoding="utf-8")
    compiler = typst_lib.Compiler(input=str(typ), root=str(tmp_path),
                                  font_paths=[str(FONTS_DIR)], ignore_system_fonts=True)
    # 144 ppi = 2x the 72 ppi default, to look at the spacing by eye
    pages = compiler.compile(format="png", ppi=144.0)
    pages = pages if isinstance(pages, list) else [pages]
    png = tmp_path / "education_2x.png"
    png.write_bytes(pages[0])
    assert png.stat().st_size > 1000 and png.read_bytes()[:4] == b"\x89PNG"
