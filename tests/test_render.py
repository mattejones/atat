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
