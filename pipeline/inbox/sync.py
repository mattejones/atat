"""
sync.py — one pass over new Gmail messages: fetch, triage, match, classify, queue for review.

Runs as the gmail_sync job (pipeline/jobs/kinds/inbox.py), from the scheduler, the
Inbox page's "Sync now", or the sync_inbox MCP tool. Nothing here changes an
application — every job-related email lands in inbox_emails as 'pending' with a
proposed change, and only review.accept() applies it.

Resumable by design:
  - messages are processed oldest first, and each one's row and the watermark are
    written in the same transaction, so a crash or cancel loses at most the message
    in flight, which the next run picks up again;
  - every message id seen is stored (irrelevant ones as 'auto_ignored', headers only),
    so a re-sync never reprocesses or double-queues an email;
  - GMAIL_MAX_PER_SYNC caps a run; the rest are left for the next one;
  - mail is fetched and processed in batches, so a failure part-way keeps what's done;
  - it never reads further back than GMAIL_MAX_LOOKBACK_DAYS, whatever the watermark says.

Each run's result includes the model calls and input/output tokens it used.

Two ways to classify (INBOX_CLASSIFIER, or per run):
  api    the sync triages and classifies with INBOX_MODEL, as above.
  agent  the sync makes no model calls: it stores mail as 'untriaged' (with the
         deterministic hint) and an AI agent classifies it over MCP — see
         pipeline/inbox/agent.py and the get_untriaged_emails / classify_inbox_emails tools.

No database connection is held across a Gmail or model call (see pipeline/jobs/base.py).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from db.database import get_connection
from pipeline.config import GMAIL_MAX_LOOKBACK_DAYS, GMAIL_MAX_PER_SYNC, INBOX_CLASSIFIER
from pipeline.inbox import classifier, llm, prefilter
from pipeline.inbox.gmail_client import EmailMessage, MailSource
from pipeline.inbox.matcher import STRONG_SCORE, load_applications, shortlist, thread_application
from pipeline.inbox.proposals import propose

log = logging.getLogger(__name__)

KIND = "gmail_sync"

# Messages fetched, triaged and stored per batch. Matches the triage batch size, so each
# batch is one triage call.
BATCH_SIZE = 25

CLASSIFIERS = ("api", "agent")

# One sync at a time per process. The jobs layer doesn't guard kinds with no target,
# so start_sync_job() checks for an active job too; this catches the rest.
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_ms(value: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T", 1))
    except (AttributeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _days_ago_ms(days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)


def initial_watermark(db) -> int:
    """First run: from a day before the earliest application, or 30 days back if there are none."""
    row = db.execute("SELECT MIN(created_at) AS m FROM applications").fetchone()
    earliest = _to_ms(row["m"]) if row and row["m"] else None
    if earliest is None:
        return _days_ago_ms(30)
    return earliest - 24 * 3600 * 1000


def start_point(db) -> int:
    """Where this sync reads from: the watermark, but never more than GMAIL_MAX_LOOKBACK_DAYS back."""
    state = db.execute("SELECT last_internal_date FROM gmail_sync_state WHERE id = 1").fetchone()
    after = state["last_internal_date"] if state and state["last_internal_date"] else initial_watermark(db)
    return max(after, _days_ago_ms(GMAIL_MAX_LOOKBACK_DAYS))


def sync_state() -> dict:
    with get_connection() as db:
        row = db.execute("SELECT * FROM gmail_sync_state WHERE id = 1").fetchone()
        counts = {r[0]: r[1] for r in db.execute(
            """SELECT review_status, COUNT(*) FROM inbox_emails
               WHERE review_status IN ('pending', 'untriaged') GROUP BY review_status"""
        ).fetchall()}
    state = dict(row) if row else {}
    state["last_result"] = json.loads(state["last_result"]) if state.get("last_result") else None
    state["pending"]    = counts.get("pending", 0)
    state["untriaged"]  = counts.get("untriaged", 0)
    state["classifier"] = INBOX_CLASSIFIER
    return state


def _known_ids(ids: list[str]) -> set[str]:
    known: set[str] = set()
    with get_connection() as db:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows  = db.execute(
                f"SELECT gmail_message_id FROM inbox_emails WHERE gmail_message_id IN ({','.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
            known.update(r["gmail_message_id"] for r in rows)
    return known


def _store(msg: EmailMessage, fields: dict) -> None:
    """Insert one row and advance the watermark past it, atomically."""
    row = {
        "gmail_message_id": msg.id,
        "gmail_thread_id":  msg.thread_id,
        "sender":           msg.sender,
        "sender_domain":    msg.sender_domain,
        "subject":          msg.subject,
        "received_at":      msg.received_at,
        **fields,
    }
    cols = ", ".join(row)
    with get_connection() as db:
        db.execute(
            f"INSERT INTO inbox_emails ({cols}) VALUES ({', '.join('?' * len(row))}) "
            "ON CONFLICT(gmail_message_id) DO NOTHING",
            list(row.values()),
        )
        db.execute(
            """UPDATE gmail_sync_state
               SET last_internal_date = MAX(COALESCE(last_internal_date, 0), ?)
               WHERE id = 1""",
            (msg.internal_date,),
        )


def _ignored(classification: Optional[str] = None, reason: Optional[str] = None) -> dict:
    # Headers only: no snippet or body is kept for mail that isn't about an application.
    return {"review_status": "auto_ignored", "classification": classification, "match_reason": reason}


def run_sync(
    client:       Optional[MailSource] = None,
    *,
    checkpoint:   Optional[Callable[[], None]] = None,
    max_messages: Optional[int] = None,
    classifier:   Optional[str] = None,
) -> dict:
    classifier = classifier or INBOX_CLASSIFIER
    if classifier not in CLASSIFIERS:
        raise ValueError(f"classifier must be one of {CLASSIFIERS}, not {classifier!r}")
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("A Gmail sync is already running in this process.")
    try:
        with llm.usage_scope() as usage:
            try:
                result = _run(client, checkpoint or (lambda: None), max_messages or GMAIL_MAX_PER_SYNC, classifier)
            finally:
                log.info("Inbox sync token usage: %s", usage)
        result.update(usage)
    except Exception as e:
        with get_connection() as db:
            db.execute("UPDATE gmail_sync_state SET last_error = ? WHERE id = 1", (f"{_now()}: {e}",))
        raise
    finally:
        _LOCK.release()
    with get_connection() as db:
        db.execute(
            "UPDATE gmail_sync_state SET last_synced_at = ?, last_error = NULL, last_result = ? WHERE id = 1",
            (_now(), json.dumps(result)),
        )
    return result


def _run(client: Optional[MailSource], checkpoint: Callable[[], None], limit: int, classifier: str = "api") -> dict:
    with get_connection() as db:
        after = start_point(db)

    if client is None:
        from pipeline.inbox.gmail_client import GmailClient
        client = GmailClient()

    ids     = client.list_message_ids(after)
    known   = _known_ids(ids)
    new_ids = [i for i in ids if i not in known]
    todo    = new_ids[:limit]

    with get_connection() as db:
        apps = load_applications(db)

    counts = {"fetched": 0, "queued": 0, "ignored": 0, "failed": 0, "untriaged": 0,
              "remaining": len(new_ids) - len(todo), "classifier": classifier}

    # Fetch and process in batches, oldest first, so a failure part-way (a Gmail quota
    # error that outlasts the retries, a crash) keeps everything already stored.
    for start in range(0, len(todo), BATCH_SIZE):
        batch: list[EmailMessage] = []
        for mid in todo[start:start + BATCH_SIZE]:
            checkpoint()
            batch.append(client.get_message(mid))
        batch.sort(key=lambda m: m.internal_date)
        counts["fetched"] += len(batch)
        if classifier == "agent":
            _gather_batch(batch, apps, checkpoint, counts)
        else:
            _process_batch(batch, apps, checkpoint, counts)

    log.info("Inbox sync: %s", counts)
    return counts


def _gather_batch(messages: list[EmailMessage], apps: list[dict], checkpoint: Callable[[], None], counts: dict) -> None:
    """Agent mode: store each message for an agent to classify over MCP. No model calls."""
    for msg in messages:
        checkpoint()
        _store(msg, {
            "snippet":       msg.snippet,
            "body_excerpt":  msg.body,
            "review_status": "untriaged",
            "match_reason":  prefilter.hint(msg, apps),     # ats_sender | company_named | None
        })
        counts["untriaged"] += 1


def _process_batch(messages: list[EmailMessage], apps: list[dict], checkpoint: Callable[[], None], counts: dict) -> None:
    by_uuid  = {a["uuid"]: a for a in apps}
    hints    = {m.id: prefilter.hint(m, apps) for m in messages}
    unhinted = [m for m in messages if not hints[m.id]]
    related  = prefilter.triage(unhinted) if unhinted else set()

    for msg in messages:
        checkpoint()
        if not hints[msg.id] and msg.id not in related:
            _store(msg, _ignored(reason="triage: not job-related"))
            counts["ignored"] += 1
            continue

        with get_connection() as db:
            thread_app_id = thread_application(db, msg.thread_id)
        candidates = shortlist(msg, apps, thread_app_id)
        others     = classifier.other_open_apps(apps, candidates)
        content    = {"snippet": msg.snippet, "body_excerpt": msg.body}

        try:
            c = classifier.classify(msg, candidates, others)
        except RuntimeError as e:
            # Keep it rather than lose it: queue it unclassified for a human to sort out.
            log.warning("Inbox: classifying %s failed: %s", msg.id, e)
            top = candidates[0] if candidates and candidates[0].score >= STRONG_SCORE else None
            checkpoint()
            _store(msg, {
                **content,
                "review_status":  "pending",
                "application_id": top.app["id"] if top else None,
                "match_reason":   f"classification failed: {e}"[:500],
            })
            counts["failed"] += 1
            continue

        app = by_uuid.get(c.application_uuid) if c.application_uuid else None
        checkpoint()
        if c.classification == "not_job" or (c.classification == "other_job" and app is None):
            _store(msg, _ignored(c.classification, c.summary or None))
            counts["ignored"] += 1
            continue

        cand   = next((x for x in candidates if app and x.uuid == app["uuid"]), None)
        reason = "+".join(cand.reasons) if cand else ("model" if app else None)
        _store(msg, {
            **content,
            "classification": c.classification,
            "confidence":     c.confidence,
            "application_id": app["id"] if app else None,
            "match_reason":   reason,
            "evidence":       c.evidence or None,
            "extracted_json": classifier.to_json(c),
            "review_status":  "pending",
            "model":          c.model,
            **propose(c.classification, app, msg.received_date, c.interview_at),
        })
        counts["queued"] += 1


def start_sync_job(created_by: str = "human", max_messages: Optional[int] = None, classifier: Optional[str] = None) -> dict:
    """Queue a gmail_sync job, or return the one already queued/running."""
    from pipeline.jobs import service

    with get_connection() as db:
        active = db.execute(
            "SELECT id FROM generation_jobs WHERE kind = ? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
            (KIND,),
        ).fetchone()
    if active:
        return service.get(active["id"])
    params = {k: v for k, v in (("max_messages", max_messages), ("classifier", classifier)) if v} or None
    return service.create(KIND, params=params, created_by=created_by)
