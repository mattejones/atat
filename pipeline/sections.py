"""
sections.py — CV section splitting and composition for ATAT.

Responsibilities:
  1. Define canonical section names as typed constants.
  2. split_cv_sections()  — takes the raw cv_data dict from call_llm() and
                            returns a dict[section_name, section_text] containing
                            the raw content for each section (no headers/dividers).
                            Raw content is what the judge evaluates and what is
                            stored in section files.
  3. compose_cv_markdown() — takes an ordered dict[section_name, section_text]
                             and assembles the full cv.md, inserting headers and
                             dividers. Called at generation time and on section accept.

Section files store raw content only — headers and formatting are added at
composition time. This keeps character-span positions in judge output unambiguous.

Canonical section order is enforced by SECTION_ORDER. Composition always follows
this order regardless of dict insertion order.
"""

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Canonical section names ───────────────────────────────────────────────────

PROFILE       = "profile"
EXPERIENCE    = "experience"
SKILLS        = "skills"
EDUCATION     = "education"
CERTIFICATIONS = "certifications"

SECTION_ORDER = [PROFILE, EXPERIENCE, SKILLS, EDUCATION, CERTIFICATIONS]

SECTION_LABELS = {
    PROFILE:        "Profile",
    EXPERIENCE:     "Experience",
    SKILLS:         "Skills",
    EDUCATION:      "Education",
    CERTIFICATIONS: "Certifications",
}


# ── Section splitter ──────────────────────────────────────────────────────────

def split_cv_sections(cv_data: dict) -> dict[str, str]:
    """
    Split a validated cv_data dict into per-section raw text.

    Returns a dict mapping section name -> raw section content.
    Raw content contains no section headers or horizontal dividers —
    those are added at composition time.

    Raises ValueError if any required section is missing or empty.
    """
    sections: dict[str, str] = {}

    # ── Profile ───────────────────────────────────────────────────────────────
    profile = cv_data.get("profile", "").strip()
    if not profile:
        raise ValueError("cv_data missing required field: profile")
    sections[PROFILE] = profile

    # ── Experience ────────────────────────────────────────────────────────────
    experience_entries = cv_data.get("experience", [])
    earlier = cv_data.get("earlier_experience", "").strip()
    experience_lines: list[str] = []

    for exp in experience_entries:
        experience_lines.append(
            f"### {exp.get('company', '')} -- {exp.get('role', '')} | {exp.get('dates', '')}"
        )
        experience_lines.append("")
        if exp.get("context"):
            experience_lines.append(f"*{exp['context']}*")
            experience_lines.append("")
        for bullet in exp.get("bullets", []):
            experience_lines.append(f"- {bullet}")
        experience_lines.append("")

    if earlier:
        experience_lines.append("### Earlier technical experience")
        experience_lines.append("")
        experience_lines.append(earlier)
        experience_lines.append("")

    sections[EXPERIENCE] = "\n".join(experience_lines).strip()

    # ── Skills ────────────────────────────────────────────────────────────────
    skills_lines: list[str] = []
    for skill in cv_data.get("skills", []):
        skills_lines.append(
            f"**{skill.get('category', '')}:** {skill.get('items', '')}"
        )
    sections[SKILLS] = "\n".join(skills_lines).strip()

    # ── Education ─────────────────────────────────────────────────────────────
    education_lines: list[str] = []
    for edu in cv_data.get("education", []):
        education_lines.append(
            f"**{edu.get('degree', '')}** -- "
            f"{edu.get('institution', '')}, {edu.get('years', '')}"
        )
        if edu.get("subjects"):
            education_lines.append(f"*{edu['subjects']}*")
        education_lines.append("")
    sections[EDUCATION] = "\n".join(education_lines).strip()

    # ── Certifications ────────────────────────────────────────────────────────
    cert_lines: list[str] = []
    for cert in cv_data.get("certifications", []):
        cert_lines.append(f"- {cert}")
    sections[CERTIFICATIONS] = "\n".join(cert_lines).strip()

    log.debug(f"CV split into {len(sections)} sections: {list(sections.keys())}")
    return sections


# ── Composer ──────────────────────────────────────────────────────────────────

def compose_cv_markdown(
    name:            str,
    contact:         dict,
    section_content: dict[str, str],
) -> str:
    """
    Compose a full cv.md from per-section raw content.

    Follows SECTION_ORDER — missing sections are skipped with a warning.
    name and contact are passed separately as they are not section-managed.

    Args:
        name:            Full name from cv_data.
        contact:         Contact dict from cv_data (email, phone, location, linkedin).
        section_content: dict[section_name -> raw section text].

    Returns:
        Full CV as a Markdown string, consistent with the output of cv_to_markdown().
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"# {name}")
    contact_parts = [
        contact.get("email", ""),
        contact.get("phone", ""),
        contact.get("location", ""),
        contact.get("linkedin", ""),
    ]
    lines.append(" · ".join(p for p in contact_parts if p))
    lines += ["", "---", ""]

    # ── Sections ──────────────────────────────────────────────────────────────
    for section_name in SECTION_ORDER:
        content = section_content.get(section_name, "").strip()
        if not content:
            log.warning(f"Section '{section_name}' missing from content map — skipping.")
            continue

        label = SECTION_LABELS[section_name]
        lines.append(f"## {label}")
        lines.append("")
        lines.append(content)
        lines += ["", "---", ""]

    return "\n".join(lines)


# ── File helpers ──────────────────────────────────────────────────────────────

def section_file_path(output_dir: Path, section_name: str, report_id: str) -> Path:
    """
    Return the canonical path for a section report file.

    Pattern: output/{app_id}/sections/{section_name}/{report_id}.md
    """
    return output_dir / "sections" / section_name / f"{report_id}.md"


def write_section_file(
    output_dir:   Path,
    section_name: str,
    report_id:    str,
    content:      str,
) -> Path:
    """Write raw section content to its canonical path. Creates dirs as needed."""
    path = section_file_path(output_dir, section_name, report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.debug(f"Section file written: {path}")
    return path


def read_section_file(path: Path) -> str:
    """Read raw section content from a section file."""
    if not path.exists():
        raise FileNotFoundError(f"Section file not found: {path}")
    return path.read_text(encoding="utf-8")
