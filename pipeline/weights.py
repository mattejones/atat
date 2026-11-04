"""
weights.py — how many bullets each role should get on a CV.

An experience file can carry a header line saying how much of the CV the role deserves:

    **CV weight:** lead        (4-6 bullets: the role to lead with)
    **CV weight:** standard    (2-4 bullets)
    **CV weight:** brief       (0-1 bullets, the context line carries it)

The weight is passed to the generation prompt and checked by the deterministic judge. A role with
no header has no bounds at all, which is how every role behaved before this existed.
"""

import re
from pathlib import Path

BOUNDS: dict = {"lead": (4, 6), "standard": (2, 4), "brief": (0, 1)}

_WEIGHT = re.compile(r"^\s*\*\*CV weight:?\*\*:?\s*([A-Za-z]+)", re.IGNORECASE | re.MULTILINE)
_TITLE = re.compile(r"^#\s+Experience:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_weight(text: str):
    """The weight named in a file's header, or None (absent or not one of lead/standard/brief)."""
    m = _WEIGHT.search(text or "")
    weight = m.group(1).lower() if m else None
    return weight if weight in BOUNDS else None


def _split_title(title: str) -> tuple:
    parts = re.split(r"\s*[—–-]{1,2}\s*", title, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def library_weights(directory: Path = None) -> list:
    """[{"company", "role", "weight", "min", "max"}, ...] for every experience file with a weight."""
    from pipeline.config import EXPERIENCE_PATH
    from pipeline.library import experience_files
    from pipeline.tailorer import load_text

    out = []
    for f in experience_files(directory or EXPERIENCE_PATH):
        text = load_text(f)
        weight = parse_weight(text)
        title = _TITLE.search(text)
        if not weight or not title:
            continue
        company, role = _split_title(title.group(1))
        lo, hi = BOUNDS[weight]
        out.append({"company": company, "role": role, "weight": weight, "min": lo, "max": hi})
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def weight_for(company: str, role: str, weights: list):
    """The weight entry for a CV role heading, matched on company (and role if several match)."""
    c, r = _norm(company), _norm(role)
    hits = [w for w in weights if _norm(w["company"]) and _norm(w["company"]) == c]
    if len(hits) > 1:
        hits = [w for w in hits if _norm(w["role"]) == r] or hits
    return hits[0] if hits else None


def describe(weights: list) -> str:
    """A prompt block stating each weighted role's bullet count, or '' when none are weighted."""
    if not weights:
        return ""
    lines = []
    for w in weights:
        name = f"{w['company']} — {w['role']}" if w["role"] else w["company"]
        if w["max"] == 0:
            n = "no bullets (context line only)"
        elif w["min"] == 0:
            n = f"at most {w['max']} bullet (the context line carries the role)"
        else:
            n = f"{w['min']} to {w['max']} bullets"
        lines.append(f"- {name}: {n}")
    return (
        "## BULLETS PER ROLE\n"
        "The person has said how much of the CV each role deserves. Give each role below this many "
        "bullets (roles not listed keep their usual count):\n" + "\n".join(lines) + "\n"
    )
