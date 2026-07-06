"""
Tests for pipeline/render.py — Typst document generation.

Covers the experience-bullet marker specifically: a previous implementation
used a hardcoded `#v(3pt)` nudge to position the bullet circle, which was
tuned for one font-size/leading combination and drifted out of alignment
whenever those values changed (visible as a bullet floating above the
text line instead of sitting centered on it).
"""

from pathlib import Path

import pytest

from pipeline.parse_cv import EducationEntry, ExperienceEntry, ParsedCV
from pipeline.render import build_typst_doc

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"


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


def test_bullet_marker_centers_on_font_metrics_not_a_hardcoded_offset():
    """
    The marker must derive its vertical position from the bullet text's own
    font size (via a 1em box + horizon alignment), not from a fixed-point
    #v() nudge that only happens to line up for one specific text size.
    """
    cv = _sample_cv(["Led the full migration from Marketo to HubSpot."])
    doc = build_typst_doc(cv)

    assert "#box(width:8pt,height:1em)" in doc
    assert "align(center+horizon)" in doc
    # Guard against regressing to the old fragile offset hack.
    assert "#v(3pt)#circle" not in doc


def test_bullet_list_omitted_when_no_bullets():
    cv = _sample_cv([])
    doc = build_typst_doc(cv)
    assert "#list(" not in doc


@pytest.mark.skipif(
    not FONTS_DIR.exists() or not any(FONTS_DIR.glob("*.ttf")),
    reason="Poppins fonts not available in this environment",
)
def test_bullet_list_compiles_with_typst(tmp_path):
    """
    Regression/build-pipeline check: the generated document must actually
    compile with Typst. Catches syntax errors in the marker (or anywhere
    else) that a plain string assertion would miss.
    """
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
