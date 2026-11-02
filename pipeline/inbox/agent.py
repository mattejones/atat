"""
agent.py — triage and classify inbox email with an AI agent over MCP, not the API.

With INBOX_CLASSIFIER=agent (or sync_inbox(classifier="agent")), the Gmail sync makes
no model calls: it stores new mail as 'untriaged', tagged with the deterministic hint
(ats_sender / company_named / none). An agent connected to the ATAT MCP server then:

  1. get_untriaged_emails()   a batch of emails — cleaned body, candidate applications
                              from the matcher, and the classification rules
  2. classify_inbox_emails()  its verdicts, validated here and turned into the same
                              never-backwards proposals the API classifier produces

Classified emails join the normal review queue ('pending'); nothing changes an
application until one is accepted. Emails judged not job-related become 'auto_ignored'
and their stored body is wiped, as in API mode.
"""

from __future__ import annotations

import json
from email.utils import parseaddr

from db.database import get_connection
from pipeline.config import PROMPTS_PATH
from pipeline.inbox import review
from pipeline.inbox.classifier import clean_body
from pipeline.inbox.gmail_client import EmailMessage
from pipeline.inbox.matcher import load_applications, shortlist, thread_application
from pipeline.inbox.proposals import CLASSIFICATIONS, propose

MAX_BATCH = 50

_AGENT_NOTES = """
## How to use these tools

You are classifying emails that ATAT has fetched from the applicant's Gmail. For each
email in the batch, decide its classification and which application (if any) it is
about, using the rules above, then send every verdict back in one
classify_inbox_emails call:

  {"email_id": <id>, "classification": "<value>", "application_uuid": "<uuid or null>",
   "confidence": 0.0-1.0, "evidence": "<short verbatim quote>",
   "interview_at": "<ISO datetime or null>", "summary": "<one sentence>"}

- Most emails with hint=null are not about applications: mark them not_job quickly
  (evidence and summary can be empty).
- Prefer a uuid from the email's candidates. If none fit but the email clearly names
  a company, look it up with search_applications and use that uuid.
- Classifying doesn't change any application — the applicant reviews each proposal in
  the Inbox (or asks you to accept it). Don't accept on your own initiative.
- Repeat get_untriaged_emails until remaining is 0.
"""


def instructions() -> str:
    from pipeline.tailorer import load_text

    return load_text(PROMPTS_PATH / "email_classification.md") + "\n" + _AGENT_NOTES


def _as_message(row: dict) -> EmailMessage:
    name, addr = parseaddr(row.get("sender") or "")
    return EmailMessage(
        id=row["gmail_message_id"], thread_id=row.get("gmail_thread_id") or "", internal_date=0,
        sender=row.get("sender") or "", sender_name=name, sender_email=addr.lower(),
        sender_domain=row.get("sender_domain") or "", subject=row.get("subject") or "",
        snippet=row.get("snippet") or "", body=row.get("body_excerpt") or "",
    )


def _candidate(c) -> dict:
    a = c.app
    return {
        "uuid": a["uuid"], "company": a.get("company"), "role": a.get("role"),
        "status": a.get("status"), "applied_date": a.get("applied_date"),
        "matched_on": "+".join(c.reasons),
    }


def untriaged(limit: int = 25, include_body: bool = True, include_instructions: bool = True) -> dict:
    """A batch of untriaged emails, hinted ones first, oldest first within that."""
    limit = max(1, min(limit, MAX_BATCH))
    with get_connection() as db:
        rows = [dict(r) for r in db.execute(
            """SELECT * FROM inbox_emails WHERE review_status = 'untriaged'
               ORDER BY match_reason IS NULL, received_at LIMIT ?""",
            (limit,),
        ).fetchall()]
        total = db.execute("SELECT COUNT(*) FROM inbox_emails WHERE review_status = 'untriaged'").fetchone()[0]
        apps  = load_applications(db)
        threads = {r["id"]: thread_application(db, r["gmail_thread_id"]) if r["gmail_thread_id"] else None for r in rows}

    emails = []
    for r in rows:
        msg = _as_message(r)
        item = {
            "email_id":    r["id"],
            "from":        r["sender"],
            "subject":     r["subject"],
            "received_at": r["received_at"],
            "hint":        r["match_reason"],
            "snippet":     r["snippet"],
            "candidates":  [_candidate(c) for c in shortlist(msg, apps, threads[r["id"]])],
        }
        if include_body:
            item["body"] = clean_body(r["body_excerpt"] or "")
        emails.append(item)

    out = {"emails": emails, "returned": len(emails), "remaining": total - len(emails)}
    if include_instructions:
        out["instructions"] = instructions()
    return out


def classify(results: list[dict]) -> dict:
    """Apply an agent's verdicts. Each is validated on its own; bad ones are reported, not fatal."""
    updated, errors = [], []
    for r in results:
        try:
            updated.append(_apply_one(r))
        except (review.ReviewError, ValueError, KeyError, TypeError) as e:
            errors.append({"email_id": r.get("email_id") if isinstance(r, dict) else None, "error": str(e)})
    with get_connection() as db:
        remaining = db.execute("SELECT COUNT(*) FROM inbox_emails WHERE review_status = 'untriaged'").fetchone()[0]
    return {"updated": updated, "errors": errors, "untriaged_remaining": remaining}


def _apply_one(r: dict) -> dict:
    email_id = int(r["email_id"])
    cls      = r.get("classification")
    if cls not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {', '.join(CLASSIFICATIONS)}, not {cls!r}")
    try:
        confidence = max(0.0, min(1.0, float(r.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    interview_at = r.get("interview_at") if isinstance(r.get("interview_at"), str) and r.get("interview_at") else None
    summary      = str(r.get("summary") or "")[:300]

    with get_connection() as db:
        email = review._row(db, email_id)
        if email["review_status"] not in ("untriaged", "pending"):
            raise review.ReviewError(f"Email {email_id} is {email['review_status']}; only untriaged or pending emails can be classified.")
        uuid = r.get("application_uuid")
        app  = review._app(db, uuid=uuid) if uuid else None      # raises ReviewError for an unknown uuid

        if cls == "not_job" or (cls == "other_job" and app is None):
            db.execute(
                """UPDATE inbox_emails
                   SET review_status = 'auto_ignored', classification = ?, match_reason = ?,
                       snippet = NULL, body_excerpt = NULL, application_id = NULL, model = 'agent'
                   WHERE id = ?""",
                (cls, summary or None, email_id),
            )
        else:
            proposal  = propose(cls, app, (email["received_at"] or "")[:10], interview_at)
            extracted = {k: v for k, v in (("summary", summary), ("interview_at", interview_at),
                                           ("company", r.get("company")), ("role", r.get("role"))) if v}
            db.execute(
                """UPDATE inbox_emails
                   SET review_status = 'pending', classification = ?, confidence = ?,
                       application_id = ?, match_reason = ?, evidence = ?, extracted_json = ?,
                       proposed_status = ?, proposed_date_type = ?, proposed_date = ?, model = 'agent'
                   WHERE id = ?""",
                (cls, confidence, app["id"] if app else None, "agent" if app else None,
                 str(r.get("evidence") or "")[:500] or None, json.dumps(extracted),
                 proposal["proposed_status"], proposal["proposed_date_type"], proposal["proposed_date"],
                 email_id),
            )
        out = review._row(db, email_id)
    return {
        "email_id":        email_id,
        "review_status":   out["review_status"],
        "classification":  out["classification"],
        "app_uuid":        out["app_uuid"],
        "proposed_status": out["proposed_status"],
        "proposed_date":   out["proposed_date"],
    }


def reclassify_accepted(results: list[dict]) -> dict:
    """
    Correct the classification of emails already accepted — for example when a new type
    (progress_update) splits an old one. Only the classification, evidence and summary
    change: the application's status and dates, applied when the email was accepted,
    are left alone. The previous classification is kept in extracted_json.
    Each item: {"email_id", "classification", "evidence"?, "summary"?}.
    """
    updated, errors = [], []
    for r in results:
        try:
            email_id = int(r["email_id"])
            cls = r.get("classification")
            if cls not in CLASSIFICATIONS or cls == "not_job":
                raise ValueError(f"classification must be one of {', '.join(c for c in CLASSIFICATIONS if c != 'not_job')}, not {cls!r}")
            with get_connection() as db:
                email = review._row(db, email_id)
                if email["review_status"] != "accepted":
                    raise review.ReviewError(f"Email {email_id} is {email['review_status']}; use classify_inbox_emails for emails not yet accepted.")
                extracted = dict(email["extracted"])
                if email["classification"] != cls:
                    extracted.setdefault("previous_classification", email["classification"])
                if r.get("summary"):
                    extracted["summary"] = str(r["summary"])[:300]
                db.execute(
                    "UPDATE inbox_emails SET classification = ?, evidence = COALESCE(?, evidence), extracted_json = ? WHERE id = ?",
                    (cls, (str(r.get("evidence") or "")[:500] or None), json.dumps(extracted), email_id),
                )
            updated.append({"email_id": email_id, "from": email["classification"], "to": cls})
        except (review.ReviewError, ValueError, KeyError, TypeError) as e:
            errors.append({"email_id": r.get("email_id") if isinstance(r, dict) else None, "error": str(e)})
    return {"updated": updated, "errors": errors}
