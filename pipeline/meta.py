"""
meta.py — the applicant's contact details, read from the CV library's META file.

The CV header (name, email, phone, location, LinkedIn, website) used to be written by the
model, which was asked to copy it out of META. That cost a full generation run whenever the
model dropped a field, and the rules about which fields were mandatory were written for one
person (a website was "REQUIRED"). Now the header is built here, in code, straight from META,
and a missing required field is reported in seconds, before any LLM call.

META is free text written by hand, so the parser is forgiving about layout:

    # Jane Doe                       (H1 gives the name)
    **Email:** jane@example.com      (bold label)
    Location: Leeds, UK              (plain label)
    - Website: https://jane.example  (bullet)
    **LinkedIn**: [jane](https://linkedin.com/in/jane)

A line that says "DO NOT INCLUDE" is treated as an exclusion, not a value, so
"Personal website: x.com - DO NOT INCLUDE" keeps the website off the CV.
"""

import re
from dataclasses import dataclass, field

# Canonical field -> the labels that mean it (lower case).
_LABELS = {
    "name":     ("name", "full name"),
    "email":    ("email", "e-mail", "email address"),
    "phone":    ("phone", "telephone", "tel", "mobile", "phone number"),
    "location": ("location", "based in", "city"),
    "linkedin": ("linkedin", "linkedin url", "linkedin profile"),
    "website":  ("website", "personal website", "web", "site", "portfolio"),
}
_LABEL_TO_FIELD = {label: f for f, labels in _LABELS.items() for label in labels}

# Fields without which a CV can't be produced. Everything else is optional.
REQUIRED = ("name", "email", "location")

_LINE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?(?P<key>[A-Za-z][A-Za-z \-]{0,30}?)(?:\*\*|__)?\s*:\s*"
    r"(?:\*\*|__)?\s*(?P<val>\S.*?)\s*$"
)
_H1 = re.compile(r"^\s*#\s+(?P<name>[^#\n].*?)\s*$")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_EXCLUDED = re.compile(r"do\s*not\s*include", re.IGNORECASE)


@dataclass
class Meta:
    name:     str = ""
    email:    str = ""
    phone:    str = ""
    location: str = ""
    linkedin: str = ""
    website:  str = ""
    excluded: set = field(default_factory=set)   # fields META says must not appear

    def missing_required(self) -> list:
        return [f for f in REQUIRED if not getattr(self, f)]

    def contact(self) -> dict:
        """The header's contact fields, in the order compose_cv_markdown prints them."""
        return {
            "email":    self.email,
            "phone":    self.phone,
            "location": self.location,
            "linkedin": self.linkedin,
            "website":  self.website,
        }


def _clean_value(key: str, val: str) -> str:
    """Unwrap markdown links and autolinks; strip stray emphasis."""
    def _link(m):
        text, target = m.group(1).strip(), m.group(2).strip()
        if key == "email":
            return text or target.removeprefix("mailto:")
        return target or text

    val = _MD_LINK.sub(_link, val)
    val = val.strip().strip("<>").strip("*_ ").strip()
    return val


def parse_meta(text: str) -> Meta:
    """Read the contact fields out of META text. Never raises: missing fields are empty."""
    meta = Meta()
    for line in (text or "").splitlines():
        h1 = _H1.match(line)
        if h1 and not meta.name:
            meta.name = h1.group("name").strip()
            continue
        m = _LINE.match(line)
        if not m:
            continue
        fld = _LABEL_TO_FIELD.get(m.group("key").strip().lower())
        if not fld:
            continue
        if _EXCLUDED.search(line):
            meta.excluded.add(fld)
            continue
        if not getattr(meta, fld):          # first occurrence wins
            setattr(meta, fld, _clean_value(fld, m.group("val")))
    for fld in meta.excluded:                # an exclusion beats a value found earlier
        setattr(meta, fld, "")
    return meta


def load_meta() -> Meta:
    from pipeline.config import META_PATH
    from pipeline.tailorer import load_text
    return parse_meta(load_text(META_PATH))


def meta_problems(meta: Meta | None = None) -> list:
    """Human-readable problems that would stop a CV being produced; empty when fine."""
    from pipeline.config import META_PATH

    if meta is None:
        if not META_PATH.exists():
            return [f"META file not found at {META_PATH}"]
        meta = load_meta()
    missing = meta.missing_required()
    if not missing:
        return []
    labels = ", ".join(m.capitalize() for m in missing)
    return [
        f"META ({META_PATH}) has no {labels}. Add lines such as "
        "'**Email:** you@example.com' and '**Location:** City, Country' "
        "(and a '# Your Name' heading) so the CV header can be filled in."
    ]


def apply_meta_header(cv_data: dict, meta: Meta | None = None) -> dict:
    """Overwrite the model's name/contact with the values from META (in place)."""
    meta = meta or load_meta()
    cv_data["name"] = meta.name or cv_data.get("name", "")
    cv_data["contact"] = meta.contact()
    return cv_data
