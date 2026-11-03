"""
review_status.py — has this CV's text actually been reviewed?

A generated CV is only as trustworthy as the review of it: each section has judge flags to read,
and a person accepts a section once they are happy. Nothing stops a PDF being rendered or an
application being recorded as sent before that, so the places that do those things report this
status as a warning (it never blocks: you may well want a draft PDF to check the layout).
"""

import sqlite3

from pipeline.sections import SECTION_LABELS, SECTION_ORDER


def review_status(db: sqlite3.Connection, application_id) -> dict:
    """
    {"total": n, "accepted": k, "unaccepted": ["Profile", ...], "warning": str | None}

    An application with no sections (one logged by hand, or created before sections existed)
    has nothing to review and no warning.
    """
    rows = db.execute(
        "SELECT section_name, accepted_report_id FROM sections WHERE application_id = ?",
        (application_id,),
    ).fetchall()
    sections = {r["section_name"]: r["accepted_report_id"] for r in rows}
    total = len(sections)
    unaccepted = [
        SECTION_LABELS.get(name, name.title())
        for name in SECTION_ORDER + sorted(set(sections) - set(SECTION_ORDER))
        if name in sections and not sections[name]
    ]
    accepted = total - len(unaccepted)
    warning = None
    if unaccepted:
        warning = (
            f"{accepted} of {total} sections have been reviewed and accepted "
            f"(not yet: {', '.join(unaccepted)}). Check the rest before sending this CV."
        )
    return {"total": total, "accepted": accepted, "unaccepted": unaccepted, "warning": warning}


def warning_fields(db: sqlite3.Connection, application_id) -> dict:
    """`{"review": {...}, "warnings": [...]}`, ready to merge into an API or MCP response."""
    review = review_status(db, application_id)
    return {"review": review, "warnings": [review["warning"]] if review["warning"] else []}
