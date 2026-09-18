"""
runner.py — executes queued generation jobs on a background thread pool.

One Runner per process (get_runner()). Whichever process accepts a submit — the MCP
server or the API — runs the job itself; there is no separate worker process. Job state
lives in SQLite, so any process can report on any job.

Claiming is a single conditional UPDATE (status queued -> running), so a job can only
ever be claimed once, even with two processes sweeping the same table.

While a job runs, a heartbeat thread refreshes heartbeat_at every HEARTBEAT_S seconds.
If a process dies mid-run, its jobs stop heartbeating; the next sweep (from any live
process) marks them failed. They are NOT re-run automatically: a re-run spends tokens
again, and the dead attempt may have half-written its output. retry_job is explicit.

A job left 'queued' by a process that died before claiming it is adopted by the next
sweep and run — it was submitted, so running it is what was asked for.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

from db.database import get_connection
from pipeline.jobs.base import JobCancelled, JobHandle, get_kind, load_context

log = logging.getLogger(__name__)

HEARTBEAT_S   = 10    # how often running jobs are marked alive
STALE_AFTER_S = 90    # a running job this long without a heartbeat is presumed dead
ADOPT_AFTER_S = 15    # a queued job this long unclaimed is presumed orphaned


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _ago(seconds: float) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="microseconds")


class Runner:
    def __init__(self, max_workers: Optional[int] = None):
        workers = max_workers or int(os.getenv("ATAT_JOB_WORKERS", "2"))
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._pool     = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="atat-job")
        self._events: dict[str, threading.Event] = {}
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Recover orphans now, then keep heartbeating and sweeping in the background."""
        with self._lock:
            if self._thread:
                return
            self._thread = threading.Thread(target=self._loop, name="atat-job-heartbeat", daemon=True)
        self.sweep()
        self._thread.start()
        log.info("Job runner started (worker_id=%s)", self.worker_id)

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        self._pool.shutdown(wait=wait)

    def _loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self.heartbeat()
                self.sweep()
            except Exception:
                log.exception("Job runner heartbeat/sweep failed")

    def heartbeat(self) -> None:
        with get_connection() as db:
            db.execute(
                "UPDATE generation_jobs SET heartbeat_at = ? WHERE worker_id = ? AND status = 'running'",
                (_now(), self.worker_id),
            )

    def sweep(self) -> None:
        """Fail jobs whose worker died mid-run; adopt queued jobs nobody claimed."""
        now = _now()
        with get_connection() as db:
            cur = db.execute(
                """UPDATE generation_jobs
                   SET status = 'failed', finished_at = ?, updated_at = ?,
                       error = 'Interrupted: the process running this job stopped before it finished. '
                               || 'Output may be partial. Check the application, then retry_job if needed.'
                   WHERE status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)""",
                (now, now, _ago(STALE_AFTER_S)),
            )
            if cur.rowcount:
                log.warning("Marked %d interrupted job(s) as failed", cur.rowcount)
            orphans = [r["id"] for r in db.execute(
                "SELECT id FROM generation_jobs WHERE status = 'queued' AND submitted_at < ?",
                (_ago(ADOPT_AFTER_S),),
            ).fetchall()]
        for job_id in orphans:
            log.info("Adopting orphaned queued job %s", job_id)
            self.dispatch(job_id)

    # ── Execution ─────────────────────────────────────────────────────────────

    def dispatch(self, job_id: str) -> None:
        with self._lock:
            event = self._events.get(job_id)
            if event is not None and not event.is_set():
                return  # already in flight here
            self._events[job_id] = threading.Event()
        self._pool.submit(self._execute, job_id)

    def _claim(self, job_id: str) -> bool:
        now = _now()
        with get_connection() as db:
            cur = db.execute(
                """UPDATE generation_jobs
                   SET status = 'running', worker_id = ?, started_at = ?, heartbeat_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (self.worker_id, now, now, now, job_id),
            )
            return cur.rowcount == 1

    def _finish(self, job_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None) -> None:
        now = _now()
        with get_connection() as db:
            db.execute(
                """UPDATE generation_jobs
                   SET status = ?, result = ?, error = ?, finished_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'running' AND worker_id = ?""",
                (
                    status, json.dumps(result, default=str) if result is not None else None,
                    error, now, now, job_id, self.worker_id,
                ),
            )

    def _cancel_requested(self, job_id: str) -> bool:
        with get_connection() as db:
            row = db.execute("SELECT cancel_requested FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def _attach_application(self, job_id: str, application_id: str) -> None:
        with get_connection() as db:
            db.execute(
                "UPDATE generation_jobs SET application_id = ?, updated_at = ? WHERE id = ?",
                (application_id, _now(), job_id),
            )

    def _execute(self, job_id: str) -> None:
        try:
            if not self._claim(job_id):
                return  # cancelled, or another worker got it
            with get_connection() as db:
                job = dict(db.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone())
            kind   = get_kind(job["kind"])
            params = json.loads(job["params"] or "{}")
            log.info("Job %s (%s) started", job_id, kind.name)
            try:
                with get_connection() as db:
                    ctx = load_context(db, kind.target, job["application_id"], job["target_id"])
                # State may have moved on while the job sat in the queue.
                kind.check(ctx, params)
                handle = JobHandle(
                    job_id,
                    is_cancel_requested=lambda: self._cancel_requested(job_id),
                    attach_application=lambda app_id: self._attach_application(job_id, app_id),
                )
                result = kind.run(ctx, params, handle)
            except JobCancelled:
                log.info("Job %s cancelled while running; output discarded", job_id)
                self._finish(job_id, "cancelled", error="Cancelled while running. The model call completed but its output was discarded.")
                return
            except Exception as e:
                log.exception("Job %s (%s) failed", job_id, kind.name)
                self._finish(job_id, "failed", error=f"{type(e).__name__}: {e}")
                return
            self._finish(job_id, "succeeded", result=result)
            log.info("Job %s (%s) succeeded", job_id, kind.name)
        except Exception:
            # Bookkeeping itself failed (DB unreachable, etc.). The heartbeat will stop and
            # a sweep will mark the job failed.
            log.exception("Job %s: runner error", job_id)
        finally:
            with self._lock:
                event = self._events.get(job_id)
            if event:
                event.set()

    # ── Waiting ───────────────────────────────────────────────────────────────

    def wait(self, job_id: str, timeout: float) -> None:
        """Return when the job is no longer queued/running, or after `timeout` seconds."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with get_connection() as db:
                row = db.execute("SELECT status FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["status"] not in ("queued", "running"):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            # A job running in this process signals its event; one running elsewhere is
            # re-read from the DB each second.
            with self._lock:
                event = self._events.get(job_id)
            if event is not None:
                event.wait(min(remaining, 1.0))
            else:
                time.sleep(min(remaining, 1.0))


_runner: Optional[Runner] = None
_runner_lock = threading.Lock()


def get_runner() -> Runner:
    """The process-wide runner, started on first use."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = Runner()
            _runner.start()
        return _runner
