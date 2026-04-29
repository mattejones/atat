"""
parse_cv.py — Parse ATAT-generated CV Markdown into a structured representation.

The CV Markdown output from ATAT follows a predictable structure:

    # Name
    contact line(s)         ← may span multiple lines before the first ---
    ---
    ## Profile
    ## Experience
      ### Company — Role Title | Date Range
      *context line*
      - bullet
    ## Skills
      **Category:** items
    ## Education
      **Degree**
      Institution · Year
      *subjects*
    ## Certifications
      - cert
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
    skills: list[tuple[str, str]]   # [(category, items), ...]
    education: list[EducationEntry]
    certifications: list[str]


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

    # ── Contact — all non-empty lines after name up to the first --- ──────────
    # May span multiple lines (e.g. email+phone on one, LinkedIn on the next)
    contact_parts = []
    if name_line_idx >= 0:
        for line in lines[name_line_idx + 1:]:
            stripped = line.strip()
            if stripped == '---' or stripped.startswith('## '):
                break
            if stripped:
                contact_parts.append(stripped)

    # Merge contact lines, stripping Markdown link syntax [text](url) -> text
    raw_contact = ' · '.join(contact_parts)
    # Remove markdown link syntax but preserve the display text
    raw_contact = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', raw_contact)
    # Also handle bare URLs that ended up next to ·
    contact = raw_contact.strip()

    # ── Split into ## sections ─────────────────────────────────────────────────
    sections: dict[str, str] = {}
    current_section  = None
    current_content: list[str] = []

    for line in lines:
        if line.startswith('## '):
            if current_section is not None:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = line[3:].strip()
            current_content = []
        elif current_section is not None:
            current_content.append(line)

    if current_section is not None:
        sections[current_section] = '\n'.join(current_content).strip()

    # ── Profile ────────────────────────────────────────────────────────────────
    profile = sections.get('Profile', '').replace('---', '').strip()

    # ── Experience ─────────────────────────────────────────────────────────────
    experience: list[ExperienceEntry] = []
    exp_text = sections.get('Experience', '')

    for block in re.split(r'^### ', exp_text, flags=re.MULTILINE):
        if not block.strip():
            continue

        block_lines = block.strip().split('\n')
        header      = block_lines[0].strip()

        # Parse "Company — Role | Date Range"
        date_match  = re.search(r'\|\s*(.+)$', header)
        dates       = date_match.group(1).strip() if date_match else ''
        title_part  = re.sub(r'\s*\|.*$', '', header).strip()

        # Split company and role on em/en dash
        company_role = re.split(r'\s*[—–-]{1,2}\s*', title_part, maxsplit=1)
        company = company_role[0].strip() if company_role else title_part
        role    = company_role[1].strip() if len(company_role) > 1 else ''

        context: Optional[str] = None
        bullets: list[str]     = []

        for line in block_lines[1:]:
            s = line.strip()
            if not s or s == '---':
                continue
            elif re.match(r'^\*[^*].*[^*]\*$', s) or re.match(r'^\*[^*]+\*$', s):
                context = s.strip('*').strip()
            elif s.startswith('- '):
                # Strip any inline bold markers from bullets
                bullet_text = s[2:].strip()
                bullet_text = re.sub(r'\*\*(.+?)\*\*', r'\1', bullet_text)
                bullets.append(bullet_text)
            elif s and not context and not bullets and not s.startswith('#'):
                bullets.append(s)

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
        m = re.match(r'^\*\*([^*]+)\*\*[:\s]+(.+)$', line)
        if m:
            skills.append((m.group(1).strip().rstrip(':'), m.group(2).strip()))

    # ── Education ──────────────────────────────────────────────────────────────
    # Handles two formats:
    #
    # Format A (same line):
    #   **Degree** — Institution · 2014–2016
    #   *subjects*
    #
    # Format B (multi-line, newer):
    #   **Degree**
    #   Institution · 2014–2016
    #   *subjects*
    #
    education: list[EducationEntry] = []
    edu_raw = sections.get('Education', '')

    # Split on lines that start with ** (each degree entry)
    edu_blocks = re.split(r'\n(?=\*\*)', edu_raw)

    for block in edu_blocks:
        block = block.strip()
        if not block:
            continue

        block_lines = [l.strip() for l in block.split('\n') if l.strip()]
        if not block_lines:
            continue

        header = block_lines[0]

        # Try Format A: **Degree** — Institution · Year on same line
        m = re.match(r'\*\*(.+?)\*\*\s*[—–-]+\s*(.+?)[\s·,]+(\d{4}[–\-]?\d{0,4})', header)
        if m:
            degree      = m.group(1).strip()
            institution = m.group(2).strip()
            years       = m.group(3).strip()
        else:
            # Format B: degree on line 1, institution · year on line 2
            degree = re.sub(r'\*\*', '', header).strip()
            institution = ''
            years       = ''

            # Look for institution · year on the next non-italic line
            for line in block_lines[1:]:
                if line.startswith('*') and line.endswith('*'):
                    break   # subjects line — stop looking
                # Match "Institution · YYYY" or "Institution · YYYY–YYYY"
                inst_m = re.match(r'^(.+?)[\s·]+(\d{4}[–\-]?\d{0,4})\s*$', line)
                if inst_m:
                    institution = inst_m.group(1).strip(' ·')
                    years       = inst_m.group(2).strip()
                    break
                elif re.search(r'\d{4}', line):
                    # Fallback: line contains a year somewhere
                    year_m = re.search(r'(\d{4}[–\-]?\d{0,4})', line)
                    if year_m:
                        years = year_m.group(1)
                        institution = re.sub(r'[\s·]*\d{4}[–\-]?\d{0,4}', '', line).strip(' ·')
                    break

        # Find subjects (italic line starting with *)
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
            certifications.append(line[2:].strip())

    return ParsedCV(
        name=name, contact=contact, profile=profile,
        experience=experience, skills=skills,
        education=education, certifications=certifications,
    )
