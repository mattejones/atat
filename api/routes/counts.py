"""GET /counts — everything the nav badges need in one cheap call."""

from fastapi import APIRouter

router = APIRouter(prefix="/counts", tags=["counts"])


@router.get("")
def counts():
    from db.database import get_connection

    with get_connection() as db:
        drafts = db.execute(
            "SELECT COUNT(*) FROM generation_jobs WHERE status = 'draft'"
        ).fetchone()[0]
        rows = db.execute(
            """SELECT review_status, COUNT(*) FROM inbox_emails
               WHERE review_status IN ('pending', 'untriaged') GROUP BY review_status"""
        ).fetchall()
    inbox = {r[0]: r[1] for r in rows}
    return {
        "drafts":    drafts,
        "inbox":     inbox.get("pending", 0),
        "untriaged": inbox.get("untriaged", 0),
    }
