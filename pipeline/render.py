"""
render.py — Render an ATAT-generated CV Markdown file to a PDF via Typst.

Pipeline:
    cv.md  ->  parse_cv()  ->  ParsedCV  ->  build_typst_doc()  ->  .typ  ->  typst.Compiler  ->  cv.pdf

Design language:
  - Font:    Poppins throughout
  - Palette: Warm off-white background, sage green accent, warm grays
  - Layout:  margin: (top: PAGE_TOP) on ALL pages
             Page 1 cancels margin with #v(-PAGE_TOP) for full-bleed header
  - Metadata: #set document() sets ATS-readable title/author/keywords

Public API:
    render_cv(cv_md_path, output_dir, company=None) -> Path
    pdf_filename(name, company) -> str   — e.g. "MattJonesStripeCV.pdf"

CLI:
    python -m pipeline.render path/to/cv.md [--out dir] [--company Stripe]
"""

import logging
import re
from pathlib import Path

from pipeline.parse_cv import ParsedCV, ExperienceEntry, EducationEntry, parse_cv

log = logging.getLogger(__name__)

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

P: dict[str, str] = {
    "bg":      "#F8F5F1",
    "hdr_bg":  "#EDE8E1",
    "name":    "#1C1812",
    "contact": "#5A5248",
    "accent":  "#5C7254",
    "accent2": "#7A9670",
    "body":    "#2A2520",
    "second":  "#6B6158",
    "rule":    "#C8C0B6",
    "dot":     "#7A9670",
    "date":    "#5C7254",
}

PAGE_TOP    = "16mm"
PAGE_SIDE   = "22mm"
PAGE_BOTTOM = "22mm"


# ── Filename helper ────────────────────────────────────────────────────────────

def pdf_filename(name: str, company: str) -> str:
    """
    Format the PDF download filename as FirstNameLastNameCompanyCV.pdf.
    e.g. pdf_filename("Matt Jones", "Stripe") -> "MattJonesStripeCV.pdf"
    """
    def slug(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s.title())

    name_slug    = slug(name)    if name    else "CV"
    company_slug = slug(company) if company else ""
    return f"{name_slug}{company_slug}CV.pdf"


# ── Typst escaping ─────────────────────────────────────────────────────────────

def esc(t: str) -> str:
    if not t:
        return ""
    for s, r in [
        ('\\', '\\\\'), ('#', '\\#'), ('$', '\\$'),
        ('"', '\\"'), ('<', '\\<'), ('>', '\\>'), ('@', '\\@'),
    ]:
        t = t.replace(s, r)
    return t


def esc_str(t: str) -> str:
    """Escape for Typst string literals (inside double quotes)."""
    return t.replace('\\', '\\\\').replace('"', '\\"')


def esc_url(t: str) -> str:
    return t.replace('\\', '\\\\').replace('"', '\\"')


def esc_display(t: str) -> str:
    if not t:
        return ""
    for s, r in [
        ('\\', '\\\\'), ('#', '\\#'), ('$', '\\$'),
        ('"', '\\"'), ('<', '\\<'), ('>', '\\>'),
    ]:
        t = t.replace(s, r)
    t = t.replace('@', '#"@"')
    return t


# ── Contact line ───────────────────────────────────────────────────────────────

_DOT_SEP = f'#h(4pt)#text(fill:rgb("{P["accent2"]}"))[·]#h(4pt)'


def _build_contact_line(contact: str) -> str:
    parts    = [p.strip() for p in contact.split('·')]
    rendered = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if '@' in p and '.' in p and ' ' not in p:
            rendered.append(f'#link("{esc_url("mailto:" + p)}")[{esc_display(p)}]')
        elif p.startswith('+') or re.match(r'^[\d\s\-()]+$', p):
            tel = esc_url(re.sub(r'\s', '', p))
            rendered.append(f'#link("tel:{tel}")[{esc_display(p)}]')
        elif 'linkedin' in p.lower():
            url = p if p.startswith('http') else 'https://' + p
            rendered.append(f'#link("{esc_url(url)}")[{esc_display(p)}]')
        elif re.match(r'^[\w.-]+\.[a-z]{2,}(/\S*)?$', p) and ' ' not in p:
            url = p if p.startswith('http') else 'https://' + p
            rendered.append(f'#link("{esc_url(url)}")[{esc_display(p)}]')
        else:
            rendered.append(esc(p))
    return _DOT_SEP.join(rendered)


# ── Section header ─────────────────────────────────────────────────────────────

def _sh(title: str) -> str:
    return (
        f'#block(above: 0pt, below: 8pt)[\n'
        f'  #grid(columns:(auto,1fr),align:(left+horizon,left+horizon),column-gutter:9pt,\n'
        f'    [#text(font:"Poppins",size:7.5pt,weight:"semibold",'
        f'fill:rgb("{P["accent"]}"),tracking:1.8pt)[#upper("{title}")]],\n'
        f'    [#line(length:100%,stroke:0.7pt+rgb("{P["accent2"]}"))])\n'
        f']'
    )


# ── Experience ─────────────────────────────────────────────────────────────────

def _render_exp(e: ExperienceEntry) -> str:
    co, ro, dt = esc(e.company), esc(e.role), esc(e.dates)

    if ro:
        title = (
            f'#text(font:"Poppins",size:10pt,weight:"semibold",fill:rgb("{P["body"]}"))[{co}]'
            f'#h(6pt)'
            f'#text(font:"Poppins",size:9.5pt,weight:"regular",fill:rgb("{P["second"]}"))[{ro}]'
        )
    else:
        title = f'#text(font:"Poppins",size:10pt,weight:"semibold",fill:rgb("{P["body"]}"))[{co}]'

    out = [
        f'#grid(columns:(1fr,auto),align:(left+bottom,right+bottom),'
        f'[{title}],'
        f'[#text(font:"Poppins",size:8.5pt,weight:"semibold",fill:rgb("{P["date"]}"))[{dt}]])',
    ]

    if e.context:
        ctx = esc(e.context)
        out += [
            '#v(6pt)',
            f'#text(font:"Poppins",size:8.5pt,weight:"light",style:"italic",'
            f'fill:rgb("{P["second"]}"))[{ctx}]',
            '#v(11pt)',
        ]
    else:
        out.append('#v(9pt)')

    if e.bullets:
        items = ',\n'.join(
            f'  [#par(leading:5.8pt)[#text(font:"Poppins",size:9pt,weight:"regular",'
            f'fill:rgb("{P["body"]}"))[{esc(b)}]]]'
            for b in e.bullets
        )
        out.append(
            f'#list(\n'
            f'  marker:[#v(3pt)#circle(radius:2pt,fill:rgb("{P["dot"]}"))],\n'
            f'  indent:0pt,spacing:5pt,body-indent:10pt,\n'
            f'{items}\n'
            f')'
        )

    out.append('#v(14pt)')
    return '\n'.join(out)


# ── Education ──────────────────────────────────────────────────────────────────

def _render_edu(e: EducationEntry) -> str:
    deg, inst, yrs, subj = esc(e.degree), esc(e.institution), esc(e.years), esc(e.subjects or '')
    lines = [
        f'#text(font:"Poppins",size:9.5pt,weight:"semibold",fill:rgb("{P["body"]}"))[{deg}]',
        '#v(3pt)',
        f'#text(font:"Poppins",size:8.5pt,weight:"regular",fill:rgb("{P["second"]}"))[{inst} \u00b7 {yrs}]',
    ]
    if subj:
        lines += [
            '#v(2pt)',
            f'#text(font:"Poppins",size:8.5pt,style:"italic",fill:rgb("{P["second"]}"))[{subj}]',
        ]
    lines.append('#v(10pt)')
    return '\n'.join(lines)


# ── Certification ──────────────────────────────────────────────────────────────

def _render_cert(cert: str) -> str:
    c = esc(cert)
    return (
        f'#grid(columns:(14pt,1fr),align:(center+top,left+top),'
        f'[#v(4pt)#circle(radius:2pt,fill:rgb("{P["dot"]}"))],\n'
        f'[#par(leading:6pt)[#text(font:"Poppins",size:9pt,weight:"regular",'
        f'fill:rgb("{P["body"]}"))[{c}]]])\n'
        f'#v(5pt)'
    )


# ── PDF metadata ───────────────────────────────────────────────────────────────

def _build_doc_metadata(cv: ParsedCV, company: str = "") -> str:
    """
    Build the #set document() block for ATS-readable PDF metadata.

    Title:    "Matt Jones – Senior Engineer at Stripe"
    Author:   "Matt Jones"
    Keywords: derived from skills categories and any certification names
    """
    name    = cv.name or "CV"
    title_parts = [name]
    if cv.experience:
        first = cv.experience[0]
        if first.role:
            title_parts.append(first.role)
        if company:
            title_parts.append(f"at {company}")
    doc_title  = esc_str(" – ".join(title_parts))
    doc_author = esc_str(name)

    # Build keyword list from skill categories + top-level cert names
    kw_parts: list[str] = []
    for cat, _ in cv.skills:
        kw_parts.append(f'"{esc_str(cat)}"')
    # Limit to 10 keywords
    kw_parts = kw_parts[:10]
    keywords_str = ", ".join(kw_parts) if kw_parts else '"CV"'

    return '\n'.join([
        f'#set document(',
        f'  title: "{doc_title}",',
        f'  author: "{doc_author}",',
        f'  keywords: ({keywords_str}),',
        f')',
    ])


# ── Document builder ───────────────────────────────────────────────────────────

def build_typst_doc(cv: ParsedCV, company: str = "") -> str:
    ne = esc(cv.name)
    ct = _build_contact_line(cv.contact)
    pe = esc(cv.profile)

    metadata = _build_doc_metadata(cv, company)

    profile_block = (
        f'#par(leading:6.5pt)[#text(font:"Poppins",size:9.5pt,weight:"regular",'
        f'fill:rgb("{P["body"]}"))[{pe}]]'
    )

    exp_entries = [_render_exp(e) for e in cv.experience]
    if exp_entries:
        exp_section = (
            f'#block(breakable: false)[\n'
            f'{_sh("Experience")}\n'
            f'#v(2pt)\n'
            f'{exp_entries[0]}\n'
            f']\n' +
            '\n'.join(exp_entries[1:])
        )
    else:
        exp_section = _sh("Experience")

    skill_rows = '\n'.join(
        f'#grid(columns:(96pt,1fr),align:(left+top,left+top),column-gutter:8pt,'
        f'[#text(font:"Poppins",size:8.5pt,weight:"semibold",fill:rgb("{P["second"]}"))[{esc(c)}]],'
        f'[#par(leading:5.5pt)[#text(font:"Poppins",size:9pt,weight:"regular",'
        f'fill:rgb("{P["body"]}"))[{esc(i)}]]])\n#v(5pt)'
        for c, i in cv.skills
    )
    skills_section = (
        f'#block(breakable: false)[\n'
        f'{_sh("Skills")}\n'
        f'#v(2pt)\n'
        f'{skill_rows}\n'
        f']'
    )

    edu_blocks = [_render_edu(e) for e in cv.education]
    edu_section = (
        f'#block(breakable: false)[\n'
        f'{_sh("Education")}\n'
        f'#v(2pt)\n'
        + '\n'.join(edu_blocks) +
        '\n]'
    ) if edu_blocks else _sh("Education")

    cert_items = '\n'.join(_render_cert(c) for c in cv.certifications)
    certs_section = (
        f'#block(breakable: false)[\n'
        f'{_sh("Certifications")}\n'
        f'#v(2pt)\n'
        f'{cert_items}\n'
        f']'
    )

    return '\n'.join([
        '// ATAT CV — generated by render.py',
        metadata,
        '#set page(',
        '  paper: "a4",',
        f'  margin: (top: {PAGE_TOP}, bottom: {PAGE_BOTTOM}, left: 0pt, right: 0pt),',
        ')',
        '#set par(leading: 5.5pt, spacing: 0pt)',
        f'#set text(font: "Poppins", size: 9.5pt, fill: rgb("{P["body"]}"))',
        f'#show link: it => text(fill: rgb("{P["accent"]}"))'
        f'[#underline(offset:2pt,stroke:0.5pt+rgb("{P["accent2"]}"))[#it]]',
        '',
        f'#v(-{PAGE_TOP})',
        f'#block(width: 100%, fill: rgb("{P["hdr_bg"]}"), above: 0pt, below: 0pt)[',
        f'  #pad(top: 11mm, bottom: 10mm, left: {PAGE_SIDE}, right: {PAGE_SIDE})[',
        f'    #text(font:"Poppins",size:30pt,weight:"bold",fill:rgb("{P["name"]}"))[{ne}]',
        '    #v(8pt)',
        f'    #text(font:"Poppins",size:8.5pt,weight:"regular",fill:rgb("{P["contact"]}"))[{ct}]',
        '  ]',
        ']',
        f'#pad(left: {PAGE_SIDE}, right: {PAGE_SIDE})[',
        f'  #line(length: 100%, stroke: 0.5pt + rgb("{P["rule"]}"))',
        ']',
        '',
        f'#pad(left: {PAGE_SIDE}, right: {PAGE_SIDE})[',
        '',
        f'#v(14pt)',
        _sh("Profile"),
        profile_block,
        '',
        f'#v(16pt)',
        exp_section,
        '',
        f'#v(4pt)',
        skills_section,
        '',
        f'#v(4pt)',
        edu_section,
        '',
        f'#v(4pt)',
        certs_section,
        '',
        '] // end body pad',
    ])


# ── Renderer ───────────────────────────────────────────────────────────────────

def render_cv(cv_md_path: Path, output_dir: Path, company: str = "") -> Path:
    """
    Parse a CV Markdown file and render it to PDF via Typst.
    Returns the path to the generated PDF (always named cv.pdf in output_dir).
    """
    try:
        import typst as typst_lib
    except ImportError:
        raise ImportError("Run: pip install typst")

    if not _FONTS_DIR.exists() or not any(_FONTS_DIR.glob("*.ttf")):
        raise FileNotFoundError(
            f"No fonts found in {_FONTS_DIR}. "
            "Download Poppins TTFs into atat/fonts/ — see README."
        )

    log.info(f"Rendering CV: {cv_md_path.name}")

    markdown   = cv_md_path.read_text(encoding='utf-8')
    cv         = parse_cv(markdown)
    typ_source = build_typst_doc(cv, company=company)

    typ_path = output_dir / 'cv.typ'
    typ_path.write_text(typ_source, encoding='utf-8')

    pdf_path = output_dir / 'cv.pdf'
    try:
        compiler = typst_lib.Compiler(
            input=str(typ_path),
            root=str(output_dir),
            font_paths=[str(_FONTS_DIR)],
            ignore_system_fonts=True,
        )
        compiler.compile(output=str(pdf_path), format="pdf")
        log.info(f"PDF rendered: {pdf_path}")
    except typst_lib.TypstError as e:
        raise RuntimeError(f"Typst compilation failed: {e}") from e

    return pdf_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse, sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Render an ATAT cv.md to PDF via Typst.")
    parser.add_argument("cv_md",    metavar="cv.md", help="Path to the CV Markdown file.")
    parser.add_argument("--out",     metavar="DIR",     default=None)
    parser.add_argument("--company", metavar="COMPANY", default="")
    args = parser.parse_args()

    cv_md_path = Path(args.cv_md).resolve()
    if not cv_md_path.exists():
        print(f"Error: file not found: {cv_md_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.out).resolve() if args.out else cv_md_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        pdf = render_cv(cv_md_path, output_dir, company=args.company)
        print(f"PDF written: {pdf}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
