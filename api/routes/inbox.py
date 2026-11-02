"""
inbox.py — Gmail inbox sync and the email review queue.

Endpoints:
  GET   /inbox                  — emails, newest first (?review_status=pending|untriaged|accepted|dismissed|auto_ignored,
                                  ?application_uuid=, ?limit=). Omit review_status= for pending; pass
                                  review_status=all for every row.
  GET   /inbox/status           — Gmail connection, last sync, pending count
  GET   /inbox/counts           — just {pending, untriaged}: the nav badge's cheap poll
  GET   /inbox/{id}             — one email
  POST  /inbox/sync             — queue a gmail_sync job (or return the one running)         (202)
  POST  /inbox/{id}/accept      — apply the proposed change; optional application_uuid override
  POST  /inbox/{id}/relink      — link to another application (or none); recomputes the proposal
  POST  /inbox/{id}/dismiss     — drop from the queue without changing anything
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.inbox import review
from pipeline.jobs.base import JobError

router = APIRouter(prefix="/inbox", tags=["inbox"])


class SyncRequest(BaseModel):
    max_messages: Optional[int] = None
    classifier:   Optional[str] = None       # api | agent (default INBOX_CLASSIFIER)


class AcceptRequest(BaseModel):
    application_uuid: Optional[str] = None
    apply_status:     bool = True
    apply_date:       bool = True


class RelinkRequest(BaseModel):
    application_uuid: Optional[str] = None


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except review.EmailNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (review.ReviewError, JobError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("")
def list_inbox(
    review_status:    Optional[str] = "pending",
    application_uuid: Optional[str] = None,
    limit:            int = 100,
):
    status = None if review_status == "all" else review_status
    return call(review.list_emails, review_status=status, application_uuid=application_uuid, limit=limit)


@router.get("/status")
def inbox_status():
    from pipeline.config import GMAIL_ENABLED, GMAIL_SYNC_INTERVAL_MINUTES
    from pipeline.inbox.auth import connection_status
    from pipeline.inbox.sync import sync_state

    return {
        **connection_status(),
        "scheduled":        GMAIL_ENABLED,
        "interval_minutes": GMAIL_SYNC_INTERVAL_MINUTES,
        "sync":             sync_state(),
    }


@router.get("/counts")
def inbox_counts():
    from db.database import get_connection

    with get_connection() as db:
        rows = db.execute(
            """SELECT review_status, COUNT(*) FROM inbox_emails
               WHERE review_status IN ('pending', 'untriaged') GROUP BY review_status"""
        ).fetchall()
    counts = {r[0]: r[1] for r in rows}
    return {"pending": counts.get("pending", 0), "untriaged": counts.get("untriaged", 0)}


@router.post("/sync", status_code=202)
def sync_inbox(body: Optional[SyncRequest] = None):
    from pipeline.inbox.sync import start_sync_job

    return call(
        start_sync_job, created_by="human",
        max_messages=body.max_messages if body else None,
        classifier=body.classifier if body else None,
    )


@router.get("/{email_id}")
def get_email(email_id: int):
    return call(review.get_email, email_id)


@router.post("/{email_id}/accept")
def accept_email(email_id: int, body: Optional[AcceptRequest] = None):
    body = body or AcceptRequest()
    return call(
        review.accept, email_id,
        application_uuid=body.application_uuid,
        apply_status=body.apply_status,
        apply_date=body.apply_date,
    )


@router.post("/{email_id}/relink")
def relink_email(email_id: int, body: RelinkRequest):
    return call(review.relink, email_id, body.application_uuid)


@router.post("/{email_id}/dismiss")
def dismiss_email(email_id: int):
    return call(review.dismiss, email_id)
