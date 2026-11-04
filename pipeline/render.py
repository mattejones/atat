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

from pipeline.config import FONTS_PATH

_FONTS_DIR = FONTS_PATH   # shared across instances; override with FONTS_PATH

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

def esc_str(t: str) -> str:
    """Escape for Typst string literals (inside double quotes)."""
    return (
        t.replace('\\', '\\\\').replace('"', '\\"')
         .replace('\r', '').replace('\n', '\\n').replace('\t', '\\t')
    )


def _typographic_apostrophes(t: str) -> str:
    """
    Curl single quotes and apostrophes, as Typst's markup mode used to do for us.

    Library text is emitted as a string literal (see esc), which is not smart-quoted, so the
    straight apostrophes in "Dean's list" would otherwise print straight.
    """
    t = re.sub(r"(?<=\w)'", "\u2019", t)                      # don't, Dean's, 2000's
    t = re.sub(r"(?<![\w\u2019])'(?=\w)", "\u2018", t)        # 'quoted'
    return t.replace("'", "\u2019")                            # a closing quote after punctuation


def esc(t: str) -> str:
    """
    Library text as a Typst expression to drop into markup.

    The text is emitted as a string literal, not escaped character by character, so nothing in
    it can ever be read as markup. Escaping per character missed most of the language: "~400"
    lost its tilde (a non-breaking space in markup), "*x*" and "_x_" turned bold and italic,
    "//" started a comment that swallowed the rest of the bullet, a "[" or "]" unbalanced the
    enclosing block, and "=", "-" or "+" at the start of a line became a heading or a list.

    The trailing ";" ends the expression, so a following "(" or "[" in the template can never be
    read as a call on the string. Typst consumes the ";" and prints nothing for it.
    """
    if not t:
        return ""
    return f'#"{esc_str(_typographic_apostrophes(t))}";'


def esc_url(t: str) -> str:
    return t.replace('\\', '\\\\').replace('"', '\\"')


def esc_display(t: str) -> str:
    """Contact-line text. Same as esc(): a string literal needs no special handling for '@'."""
    return esc(t)


# ── Contact line ───────────────────────────────────────────────────────────────

_DOT_SEP = f'#h(4pt)#text(fill:rgb("{P["accent2"]}"))[·]#h(4pt)'


def _display_url(url: str) -> str:
    """
    How a link reads on the page: no scheme, no www., no trailing slash, so a long LinkedIn
    address fits on one line. The link target stays the full URL.
    """
    shown = re.sub(r'^https?://', '', url.strip(), flags=re.IGNORECASE)
    shown = re.sub(r'^www\.', '', shown, flags=re.IGNORECASE)
    return shown.rstrip('/')


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
            rendered.append(f'#link("{esc_url(url)}")[{esc_display(_display_url(p))}]')
        elif p.startswith(('http://', 'https://')) or (
            re.match(r'^[\w.-]+\.[a-z]{2,}(/\S*)?$', p) and ' ' not in p
        ):
            # Scheme-qualified URLs (https://mej.xyz) previously fell through to the
            # plain-text branch below, because the bare-domain regex does not match ':'
            # or '/'. They rendered as dead text in the PDF. Match them explicitly.
            url = p if p.startswith('http') else 'https://' + p
            rendered.append(f'#link("{esc_url(url)}")[{esc_display(_display_url(p))}]')
        else:
            rendered.append(esc(p))
    return _DOT_SEP.join(rendered)


def _link_rule() -> str:
    """The global Typst rule for links. Colour always marks a link; underline is a setting."""
    import pipeline.config as cfg
    accent, accent2 = P["accent"], P["accent2"]
    if cfg.LINK_UNDERLINE:
        return (
            f'#show link: it => text(fill: rgb("{accent}"))'
            f'[#underline(offset:2pt,stroke:0.5pt+rgb("{accent2}"))[#it]]'
        )
    return f'#show link: it => text(fill: rgb("{accent}"))[#it]'


# ── Section header ─────────────────────────────────────────────────────────────

def _sh(title: str) -> str:
    """
    Section heading. The rule beside it is a setting (HEADING_RULE):
      full  - a line across the page, beside the title (the original look)
      short - a short fixed rule under the title
      none  - the letter-spaced title alone
    """
    import pipeline.config as cfg
    label = (
        f'#text(font:"Poppins",size:7.5pt,weight:"semibold",'
        f'fill:rgb("{P["accent"]}"),tracking:{cfg.HEADING_TRACKING:g}pt)[#upper("{title}")]'
    )
    if cfg.HEADING_RULE == "none":
        return f'#block(above: 0pt, below: 8pt)[{label}]'
    if cfg.HEADING_RULE == "short":
        return (
            f'#block(above: 0pt, below: 8pt)[\n'
            f'  {label}\n'
            f'  #v(3pt)\n'
            f'  #line(length:24pt,stroke:0.7pt+rgb("{P["accent2"]}"))\n'
            f']'
        )
    return (
        f'#block(above: 0pt, below: 8pt)[\n'
        f'  #grid(columns:(auto,1fr),align:(left+horizon,left+horizon),column-gutter:9pt,\n'
        f'    [{label}],\n'
        f'    [#line(length:100%,stroke:0.7pt+rgb("{P["accent2"]}"))])\n'
        f']'
    )


# ── Bullet marker ──────────────────────────────────────────────────────────────

def _bullet_marker(width_pt: int) -> str:
    """
    Bullet dot, vertically centered against the *measured* line-height of
    9pt body text. Poppins' real ascent+descent metric (~6.3pt at 9pt size)
    is noticeably shorter than the nominal 1em/font-size box, so sizing the
    marker box to a static 1em left the dot sitting visibly off-center.
    Measuring a representative glyph run at compile time keeps this correct
    regardless of the font or size in use.
    """
    return (
        f'#context [\n'
        f'  #let lh = measure([#text(size:9pt)[Mg]]).height\n'
        f'  #box(width:{width_pt}pt,height:lh)[#align(center+horizon)'
        f'[#circle(radius:2pt,fill:rgb("{P["dot"]}"))]]\n'
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

    def _bullet_list(bs: list[str]) -> str:
        items = ',\n'.join(
            f'  [#par(leading:5.8pt)[#text(font:"Poppins",size:9pt,weight:"regular",'
            f'fill:rgb("{P["body"]}"))[{esc(b)}]]]'
            for b in bs
        )
        return (
            f'#list(\n'
            f'  marker:[{_bullet_marker(8)}],\n'
            f'  indent:0pt,spacing:5pt,body-indent:10pt,\n'
            f'{items}\n'
            f')'
        )

    # The role header (title, context line) and the FIRST bullet are one unbreakable block, so a
    # role is never left stranded at the foot of a page with its bullets on the next page. The
    # remaining bullets flow and may break across pages normally.
    if e.bullets:
        out.append(_bullet_list(e.bullets[:1]))
    out = ['#block(breakable: false)[\n' + '\n'.join(out) + '\n]']
    if len(e.bullets) > 1:
        out.append('#v(5pt)')
        out.append(_bullet_list(e.bullets[1:]))

    out.append('#v(14pt)')
    return '\n'.join(out)


# ── Education ──────────────────────────────────────────────────────────────────

def _render_edu(e: EducationEntry) -> str:
    deg, inst, yrs, subj = esc(e.degree), esc(e.institution), esc(e.years), esc(e.subjects or '')
    lines = [
        f'#text(font:"Poppins",size:9.5pt,weight:"semibold",fill:rgb("{P["body"]}"))[{deg}]',
        '#v(5pt)',   # the institution line sat too tight under the degree title at 3pt
        f'#text(font:"Poppins",size:8.5pt,weight:"regular",fill:rgb("{P["second"]}"))[{inst} \u00b7 {yrs}]',
    ]
    if subj:
        lines += [
            '#v(2pt)',
            f'#text(font:"Poppins",size:8.5pt,style:"italic",fill:rgb("{P["second"]}"))[{subj}]',
        ]
    # Prizes, scholarships and merit lists: one short line each, not folded into the subjects.
    for h in getattr(e, 'honours', None) or []:
        lines += [
            '#v(2pt)',
            f'#text(font:"Poppins",size:8.5pt,weight:"regular",fill:rgb("{P["second"]}"))[{esc(h)}]',
        ]
    lines.append('#v(10pt)')
    return '\n'.join(lines)


# ── Certification ──────────────────────────────────────────────────────────────

def _render_cert(cert: str) -> str:
    c = esc(cert)
    return (
        f'#grid(columns:(14pt,1fr),align:(center+top,left+top),'
        f'[{_bullet_marker(14)}],\n'
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
    metadata = _build_doc_metadata(cv, company)

    # Every profile paragraph is its own #par. The global `#set par(spacing: 0pt)` means a
    # blank line inside one #par would not separate them, so the gap is explicit.
    profile_block = '\n#v(6pt)\n'.join(
        f'#par(leading:6.5pt)[#text(font:"Poppins",size:9.5pt,weight:"regular",'
        f'fill:rgb("{P["body"]}"))[{esc(p)}]]'
        for p in cv.profile
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
        _link_rule(),
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
        # Invisible: lets render_cv ask the compiler how long the document came out.
        '#context [#metadata((pages: counter(page).final().first(), '
        'end-y: here().position().y.pt())) <atat-layout>]',
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

    _record_layout(compiler, output_dir)
    return pdf_path


LAYOUT_FILE = "cv.layout.json"
_LINE_PT = 13.5   # a body line plus its leading, to say how far onto an extra page a CV runs


def _record_layout(compiler, output_dir: Path) -> None:
    """Save how many pages the CV came out at, so the review status can flag one that is too long.
    Never fatal: a CV without this record just goes unchecked."""
    try:
        import json
        meta = json.loads(compiler.query(selector="<atat-layout>", field="value"))[0]
        layout = {"pages": int(meta["pages"]), "end_y_pt": float(meta["end-y"])}
        (output_dir / LAYOUT_FILE).write_text(json.dumps(layout), encoding="utf-8")
    except Exception:
        log.warning("Could not read the page count of the rendered CV", exc_info=True)


def read_layout(output_dir: Path):
    """The layout record written by render_cv, or None."""
    try:
        import json
        return json.loads((Path(output_dir) / LAYOUT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def length_warning(layout, max_pages: int):
    """A warning when the rendered CV is longer than `max_pages` (0 = no limit), else None."""
    if not layout or not max_pages or layout.get("pages", 0) <= max_pages:
        return None
    pages = layout["pages"]
    msg = f"This CV renders to {pages} pages; the limit is {max_pages}."
    if pages == max_pages + 1:
        top_pt = float(PAGE_TOP.rstrip("mm")) * 72 / 25.4
        lines = max(1, round((layout.get("end_y_pt", 0) - top_pt) / _LINE_PT))
        msg += f" It runs about {lines} line{'s' if lines != 1 else ''} onto page {pages}."
    return msg + " Shorten it before sending; nothing has been cut."


# ── Cover letter document builder ────────────────────────────────────────────────

def _para_to_typst(text: str, size: str = "10pt") -> str:
    """
    Convert a markdown paragraph to a Typst text block.
    Single newlines within a paragraph become #linebreak() for address-style
    blocks (date, salutation, sign-off). Applies standard Poppins styling.
    """
    lines   = [esc(ln) for ln in text.splitlines()]
    content = "#linebreak()\n".join(lines)
    return (
        f'#par(leading:7pt)[#text(font:"Poppins",size:{size},weight:"regular",'
        f'fill:rgb("{P["body"]}"))[{content}]]'
    )


def build_cover_letter_typst_doc(
    body_text: str,
    name:      str = "",
    contact:   str = "",
    company:   str = "",
) -> str:
    """
    Build a Typst document for a cover letter. Reuses the CV palette, fonts,
    and page layout — same header band, same visual language.

    body_text is the raw cover letter markdown (paragraphs separated by blank
    lines). The header block (name, contact) is added from application data;
    the body is parsed and rendered as Typst #par() blocks.
    """
    ne = esc(name)
    ct = _build_contact_line(contact) if contact else ""

    # Split on blank lines; drop empty paragraphs
    paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]
    body_typst = "\n#v(10pt)\n".join(_para_to_typst(p) for p in paragraphs)

    # PDF metadata
    title_parts = [name] if name else ["Cover Letter"]
    if company:
        title_parts.append(f"Cover Letter for {company}")
    metadata = "\n".join([
        "#set document(",
        f'  title: "{esc_str(" — ".join(title_parts))}",',
        f'  author: "{esc_str(name)}",',
        ")",
    ])

    # Header band — only rendered if we have a name
    if ne:
        header_lines = [
            f"#v(-{PAGE_TOP})",
            f'#block(width: 100%, fill: rgb("{P["hdr_bg"]}"), above: 0pt, below: 0pt)[',
            f'  #pad(top: 11mm, bottom: 10mm, left: {PAGE_SIDE}, right: {PAGE_SIDE})[',
            f'    #text(font:"Poppins",size:30pt,weight:"bold",fill:rgb("{P["name"]}"))[{ne}]',
            '    #v(8pt)',
        ]
        if ct:
            header_lines.append(
                f'    #text(font:"Poppins",size:8.5pt,weight:"regular",'
                f'fill:rgb("{P["contact"]}"))[{ct}]'
            )
        header_lines += ["  ]", "]"]
        header_block = "\n".join(header_lines)
        rule_block   = (
            f'#pad(left: {PAGE_SIDE}, right: {PAGE_SIDE})'
            f'[#line(length: 100%, stroke: 0.5pt + rgb("{P["rule"]}"))]'
        )
    else:
        header_block = ""
        rule_block   = ""

    return "\n".join([
        "// ATAT Cover Letter — generated by render.py",
        metadata,
        "#set page(",
        '  paper: "a4",',
        f"  margin: (top: {PAGE_TOP}, bottom: {PAGE_BOTTOM}, left: 0pt, right: 0pt),",
        ")",
        "#set par(leading: 7pt, spacing: 0pt)",
        f'#set text(font: "Poppins", size: 10pt, fill: rgb("{P["body"]}"))',
        _link_rule(),
        "",
        header_block,
        rule_block,
        "",
        f"#pad(left: {PAGE_SIDE}, right: {PAGE_SIDE})[",
        "#v(18pt)",
        body_typst,
        "#v(18pt)",
        "] // end body pad",
    ])


def render_cover_letter(
    cl_md_path: Path,
    output_dir: Path,
    name:       str = "",
    contact:    str = "",
    company:    str = "",
) -> Path:
    """
    Parse a cover letter Markdown file and render it to PDF via Typst.
    Returns the path to the generated PDF (cover_letter.pdf in output_dir).

    name and contact are sourced from the application's CV (via parse_cv) by
    the caller and injected here to build the matching header block.
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

    log.info("Rendering cover letter: %s", cl_md_path.name)

    markdown   = cl_md_path.read_text(encoding="utf-8")
    typ_source = build_cover_letter_typst_doc(
        markdown, name=name, contact=contact, company=company
    )

    typ_path = output_dir / "cover_letter.typ"
    typ_path.write_text(typ_source, encoding="utf-8")

    pdf_path = output_dir / "cover_letter.pdf"
    try:
        compiler = typst_lib.Compiler(
            input=str(typ_path),
            root=str(output_dir),
            font_paths=[str(_FONTS_DIR)],
            ignore_system_fonts=True,
        )
        compiler.compile(output=str(pdf_path), format="pdf")
        log.info("Cover letter PDF rendered: %s", pdf_path)
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
