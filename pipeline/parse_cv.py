"""
parse_cv.py — Parse ATAT-generated CV Markdown into a structured representation.

Designed to handle both strictly-formatted and slightly-deviant model output.
Common deviations handled:
  - Bold contact labels: **Email:** email · **Phone:** phone
  - Section named "Core Skills" or "Technical Skills" instead of "Skills"
  - Role dates on separate italic line instead of | Dates in header
  - Multi-line education entries
  - Bold text inside bullets
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExperienceEntry:
    company: str
    role: str
    dates: str
    context: Optional[str]
    bullets: list[str]


@dataclass
class EducationEntry:
    degree: str
    institution: str
    years: str
    subjects: Optional[str]


@dataclass
class ParsedCV:
    name: str
    contact: str
    profile: str
    experience: list[ExperienceEntry]
    skills: list[tuple[str, str]]
    education: list[EducationEntry]
    certifications: list[str]


# Section name normalisation — maps model variants to canonical names
_SECTION_ALIASES = {
    "core skills":       "Skills",
    "technical skills":  "Skills",
    "key skills":        "Skills",
    "skills & tools":    "Skills",
    "skills and tools":  "Skills",
    "profile":           "Profile",
    "summary":           "Profile",
    "professional summary": "Profile",
    "experience":        "Experience",
    "work experience":   "Experience",
    "professional experience": "Experience",
    "employment":        "Experience",
    "education":         "Education",
    "certifications":    "Certifications",
    "certificates":      "Certifications",
}


def _normalise_section(name: str) -> str:
    return _SECTION_ALIASES.get(name.lower().strip(), name.strip())


def _strip_bold_labels(text: str) -> str:
    """Remove bold label prefixes like **Email:** or **Phone:** from contact lines."""
    return re.sub(r'\*\*[^*]+:\*\*\s*', '', text)


def _strip_inline_bold(text: str) -> str:
    """Remove bold markdown from inline text: **word** -> word."""
    return re.sub(r'\*\*(.+?)\*\*', r'\1', text)


def parse_cv(markdown: str) -> ParsedCV:
    """Parse an ATAT-generated Markdown CV into structured sections."""

    text  = markdown.replace('\r\n', '\n').strip()
    lines = text.split('\n')

    # ── Name (first H1) ───────────────────────────────────────────────────────
    name = ""
    name_line_idx = -1
    for i, line in enumerate(lines):
        if line.startswith('# ') and not line.startswith('## '):
            name = line[2:].strip()
            name_line_idx = i
            break

    # ── Contact — all non-empty lines after name up to first --- or ## ────────
    contact_parts = []
    if name_line_idx >= 0:
        for line in lines[name_line_idx + 1:]:
            stripped = line.strip()
            if stripped == '---' or stripped.startswith('## '):
                break
            if stripped:
                contact_parts.append(stripped)

    raw_contact = ' · '.join(contact_parts)
    # Strip markdown link syntax [text](url) -> text
    raw_contact = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', raw_contact)
    # Strip bold labels like **Email:** **Phone:** etc
    raw_contact = _strip_bold_labels(raw_contact)
    # Collapse multiple spaces and clean up stray separators
    raw_contact = re.sub(r'\s+', ' ', raw_contact).strip().strip('·').strip()
    contact = raw_contact

    # ── Split into ## sections ─────────────────────────────────────────────────
    sections: dict[str, str] = {}
    current_section  = None
    current_content: list[str] = []

    for line in lines:
        if line.startswith('## '):
            if current_section is not None:
                sections[current_section] = '\n'.join(current_content).strip()
            raw_name = line[3:].strip()
            current_section = _normalise_section(raw_name)
            current_content = []
        elif current_section is not None:
            current_content.append(line)

    if current_section is not None:
        sections[current_section] = '\n'.join(current_content).strip()

    # ── Profile — first non-empty paragraph only ───────────────────────────────
    profile_raw = sections.get('Profile', '').replace('---', '').strip()
    # If model produced multiple paragraphs, take only the first
    profile_paragraphs = [p.strip() for p in re.split(r'\n\n+', profile_raw) if p.strip()]
    profile = profile_paragraphs[0] if profile_paragraphs else profile_raw

    # ── Experience ─────────────────────────────────────────────────────────────
    experience: list[ExperienceEntry] = []
    exp_text = sections.get('Experience', '')

    for block in re.split(r'^### ', exp_text, flags=re.MULTILINE):
        if not block.strip():
            continue

        block_lines = block.strip().split('\n')
        header      = block_lines[0].strip()

        # Try canonical format: "Company — Role | Date Range"
        date_match  = re.search(r'\|\s*(.+)$', header)
        dates       = date_match.group(1).strip() if date_match else ''
        title_part  = re.sub(r'\s*\|.*$', '', header).strip()

        # Split company and role on em/en dash
        company_role = re.split(r'\s*[—–-]{1,2}\s*', title_part, maxsplit=1)
        company = company_role[0].strip() if company_role else title_part
        role    = company_role[1].strip() if len(company_role) > 1 else ''

        context: Optional[str] = None
        bullets: list[str]     = []

        # Scan remaining lines for context (italic) and bullets
        for line in block_lines[1:]:
            s = line.strip()
            if not s or s == '---':
                continue

            # Dates on a separate italic line (model deviation): *Oct 2016 – Jun 2018*
            # Only treat as dates if we haven't found any yet AND it looks date-like
            if not dates and re.match(r'^\*[A-Z][a-z]{2}\s+\d{4}', s):
                potential_dates = s.strip('*').strip()
                if re.search(r'\d{4}', potential_dates):
                    dates = potential_dates
                    continue

            # Italic context line
            if (re.match(r'^\*[^*].*[^*]\*$', s) or re.match(r'^\*[^*]+\*$', s)) and not context:
                candidate = s.strip('*').strip()
                # Don't treat date-only italics as context
                if not re.match(r'^[A-Z][a-z]{2}\s+\d{4}', candidate):
                    context = candidate
                    continue

            # Bullet
            if s.startswith('- '):
                bullet_text = s[2:].strip()
                bullet_text = _strip_inline_bold(bullet_text)
                bullets.append(bullet_text)
            elif s and not context and not bullets and not s.startswith('#'):
                # Single-line summary (earlier roles) or context prose
                bullets.append(_strip_inline_bold(s))

        if company:
            experience.append(ExperienceEntry(
                company=company, role=role, dates=dates,
                context=context, bullets=bullets,
            ))

    # ── Skills ─────────────────────────────────────────────────────────────────
    skills: list[tuple[str, str]] = []
    for line in sections.get('Skills', '').split('\n'):
        line = line.strip()
        if not line or line == '---':
            continue
        # Match **Category:** items  OR  **Category**: items  OR  Category: items
        m = re.match(r'^\*{0,2}([^*:]+)\*{0,2}[:\s]+(.+)$', line)
        if m:
            cat = m.group(1).strip().rstrip(':').rstrip('*').strip()
            items = _strip_inline_bold(m.group(2).strip())
            if cat and items:
                skills.append((cat, items))

    # ── Education ──────────────────────────────────────────────────────────────
    education: list[EducationEntry] = []
    edu_raw = sections.get('Education', '')

    # Split on lines that start bold ** (each new degree entry)
    edu_blocks = re.split(r'\n(?=\*\*)', edu_raw)

    for block in edu_blocks:
        block = block.strip()
        if not block:
            continue

        block_lines = [l.strip() for l in block.split('\n') if l.strip()]
        if not block_lines:
            continue

        header = block_lines[0]

        # Format A (same line): **Degree** — Institution · Year
        m = re.match(r'\*\*(.+?)\*\*\s*[—–-]+\s*(.+?)[\s·,]+(\d{4}[–\-]?\d{0,4})', header)
        if m:
            degree      = m.group(1).strip()
            institution = m.group(2).strip()
            years       = m.group(3).strip()
        else:
            # Format B (multi-line): degree on line 1, institution · year on line 2
            degree      = re.sub(r'\*\*', '', header).strip()
            institution = ''
            years       = ''

            for line in block_lines[1:]:
                if line.startswith('*') and line.endswith('*'):
                    break  # subjects line
                inst_m = re.match(r'^(.+?)[\s·\-]+(\d{4}[–\-]?\d{0,4})\s*$', line)
                if inst_m:
                    institution = inst_m.group(1).strip(' ·')
                    years       = inst_m.group(2).strip()
                    break
                elif re.search(r'\d{4}', line):
                    year_m = re.search(r'(\d{4}[–\-]?\d{0,4})', line)
                    if year_m:
                        years       = year_m.group(1)
                        institution = re.sub(r'[\s·]*\d{4}[–\-]?\d{0,4}', '', line).strip(' ·')
                    break

        # Subjects (italic line)
        subjects: Optional[str] = None
        for line in block_lines[1:]:
            if re.match(r'^\*[^*].*\*$', line) or re.match(r'^\*[^*]+\*$', line):
                subjects = line.strip('*').strip()
                break

        if degree:
            education.append(EducationEntry(
                degree=degree, institution=institution,
                years=years, subjects=subjects,
            ))

    # ── Certifications ─────────────────────────────────────────────────────────
    certifications: list[str] = []
    for line in sections.get('Certifications', '').split('\n'):
        line = line.strip()
        if line.startswith('- '):
            certifications.append(_strip_inline_bold(line[2:].strip()))

    return ParsedCV(
        name=name, contact=contact, profile=profile,
        experience=experience, skills=skills,
        education=education, certifications=certifications,
    )
