"""
review.py — the review queue: list, accept, relink, dismiss.

accept() is the only code path through which an email changes an application. It
re-checks the proposed status against the application's status *now* (it may have
moved since the sync) and applies, in one transaction:

  - the status change, logged as a status_change event
  - the proposed date in application_dates (skipped if that exact date is already there)
  - an email_received event, which also counts as activity for auto-ghosting
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.database import get_connection
from pipeline.inbox.proposals import propose, status_allowed

REVIEW_STATUSES = ("untriaged", "pending", "accepted", "dismissed", "auto_ignored")


class ReviewError(ValueError):
    """The request can't be applied as given."""


class EmailNotFound(ReviewError):
    pass


_SELECT = """
    SELECT e.*, a.uuid AS app_uuid, a.company AS app_company, a.role AS app_role,
           a.status AS app_status
    FROM inbox_emails e
    LEFT JOIN applications a ON a.id = e.application_id
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _view(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["extracted"] = json.loads(out.pop("extracted_json") or "{}")
    return out


def _row(db: sqlite3.Connection, email_id: int) -> dict:
    row = db.execute(_SELECT + " WHERE e.id = ?", (email_id,)).fetchone()
    if not row:
        raise EmailNotFound(f"No inbox email with id={email_id}")
    return _view(row)


def _app(db: sqlite3.Connection, *, uuid: Optional[str] = None, app_id: Optional[str] = None) -> dict:
    col, val = ("uuid", uuid) if uuid else ("id", app_id)
    row = db.execute(
        f"""SELECT a.*, (SELECT MAX(date) FROM application_dates d
                        WHERE d.application_id = a.id AND d.date_type = 'applied') AS applied_date
            FROM applications a WHERE a.{col} = ?""",
        (val,),
    ).fetchone()
    if not row:
        raise ReviewError(f"No application found with {col}={val!r}")
    return dict(row)


def list_emails(
    review_status:    Optional[str] = "pending",
    application_uuid: Optional[str] = None,
    limit:            int = 100,
    classification:   Optional[str] = None,
) -> list[dict]:
    if review_status and review_status not in REVIEW_STATUSES:
        raise ReviewError(f"review_status must be one of {REVIEW_STATUSES}")
    clauses, args = [], []
    if review_status:
        clauses.append("e.review_status = ?")
        args.append(review_status)
    if application_uuid:
        clauses.append("a.uuid = ?")
        args.append(application_uuid)
    if classification:
        clauses.append("e.classification = ?")
        args.append(classification)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as db:
        rows = db.execute(
            _SELECT + where + " ORDER BY e.received_at DESC LIMIT ?",
            [*args, max(1, min(limit, 500))],
        ).fetchall()
    return [_view(r) for r in rows]


def get_email(email_id: int) -> dict:
    with get_connection() as db:
        return _row(db, email_id)


def relink(email_id: int, application_uuid: Optional[str]) -> dict:
    """Point an email at a different application (or none) and recompute its proposal."""
    with get_connection() as db:
        email = _row(db, email_id)
        if email["review_status"] == "accepted":
            raise ReviewError("This email has already been accepted; its changes are applied.")
        app = _app(db, uuid=application_uuid) if application_uuid else None
        interview_at = email["extracted"].get("interview_at")
        proposal = propose(email["classification"], app, (email["received_at"] or _now())[:10], interview_at)
        db.execute(
            """UPDATE inbox_emails
               SET application_id = ?, match_reason = 'manual', review_status = 'pending',
                   proposed_status = ?, proposed_date_type = ?, proposed_date = ?
               WHERE id = ?""",
            (app["id"] if app else None, proposal["proposed_status"],
             proposal["proposed_date_type"], proposal["proposed_date"], email_id),
        )
        return _row(db, email_id)


def dismiss(email_id: int) -> dict:
    with get_connection() as db:
        email = _row(db, email_id)
        if email["review_status"] == "accepted":
            raise ReviewError("This email has already been accepted; its changes are applied.")
        db.execute(
            "UPDATE inbox_emails SET review_status = 'dismissed', reviewed_at = ? WHERE id = ?",
            (_now(), email_id),
        )
        return _row(db, email_id)


def accept(
    email_id:         int,
    application_uuid: Optional[str] = None,
    apply_status:     bool = True,
    apply_date:       bool = True,
) -> dict:
    """
    Link the email to its application and apply the proposed change. Pass
    application_uuid to link somewhere other than the suggested application (the
    proposal is recomputed for it). Returns the email plus what was applied.
    """
    if application_uuid:
        email = get_email(email_id)
        if email["app_uuid"] != application_uuid:
            relink(email_id, application_uuid)

    applied: dict = {"status": None, "date": None, "skipped": []}
    with get_connection() as db:
        email = _row(db, email_id)
        if email["review_status"] == "accepted":
            raise ReviewError("This email has already been accepted.")
        if not email["application_id"]:
            raise ReviewError("Link this email to an application before accepting it.")
        app    = _app(db, app_id=email["application_id"])
        app_id = app["id"]
        source = f"From email: {email['subject'] or '(no subject)'}"

        target = email["proposed_status"]
        if apply_status and target:
            if status_allowed(app["status"], target):
                db.execute("UPDATE applications SET status = ? WHERE id = ?", (target, app_id))
                db.execute(
                    """INSERT INTO application_events
                       (application_id, event_type, from_status, to_status, detail)
                       VALUES (?, 'status_change', ?, ?, ?)""",
                    (app_id, app["status"], target, source),
                )
                applied["status"] = {"from": app["status"], "to": target}
            else:
                applied["skipped"].append(
                    f"status: application is now {app['status']!r}, so moving it to {target!r} would go backwards"
                )

        date_type, date_value = email["proposed_date_type"], email["proposed_date"]
        if apply_date and date_type and date_value:
            exists = db.execute(
                "SELECT 1 FROM application_dates WHERE application_id = ? AND date_type = ? AND date = ?",
                (app_id, date_type, date_value),
            ).fetchone()
            if date_type == "applied" and db.execute(
                "SELECT 1 FROM application_dates WHERE application_id = ? AND date_type = 'applied'", (app_id,)
            ).fetchone():
                applied["skipped"].append("date: an applied date is already recorded")
            elif exists:
                applied["skipped"].append(f"date: {date_type} {date_value} is already recorded")
            else:
                db.execute(
                    "INSERT INTO application_dates (application_id, date_type, date, notes) VALUES (?, ?, ?, ?)",
                    (app_id, date_type, date_value, source),
                )
                applied["date"] = {"type": date_type, "date": date_value}

        db.execute(
            """INSERT INTO application_events (application_id, event_type, detail)
               VALUES (?, 'email_received', ?)""",
            (app_id, f"{email['classification'] or 'email'}: {email['subject'] or '(no subject)'} — {email['sender'] or ''}"),
        )
        db.execute(
            "UPDATE inbox_emails SET review_status = 'accepted', reviewed_at = ? WHERE id = ?",
            (_now(), email_id),
        )
        out = _row(db, email_id)
    out["applied"] = applied
    return out
