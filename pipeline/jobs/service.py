"""
service.py — create, edit, submit, cancel, and read generation jobs.

This is the whole public surface. The MCP server and the API both call these functions; neither talks to the generation_jobs table or the runner directly.

Every function returns a job "view": a plain dict safe to hand straight back to a
caller. job_id is the reference key.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from db.database import get_connection
from pipeline.jobs import kinds as _kinds  # noqa: F401 — registers every kind
from pipeline.jobs.base import REGISTRY, JobError, JobNotFound, compact_prompt, get_kind, load_context, normalise_params

log = logging.getLogger(__name__)

ACTIVE   = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled")
ACTORS   = ("agent", "human")


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


# ── Row access ────────────────────────────────────────────────────────────────

def _row(db: sqlite3.Connection, job_id: str) -> dict:
    row = db.execute(
        """SELECT j.*, a.uuid AS app_uuid, a.company, a.role
           FROM generation_jobs j LEFT JOIN applications a ON a.id = j.application_id
           WHERE j.id = ?""",
        (job_id,),
    ).fetchone()
    if not row:
        raise JobNotFound(f"No job found with id={job_id!r}")
    job = dict(row)
    job["params"] = json.loads(job["params"] or "{}")
    return job


def _resolve_target(db: sqlite3.Connection, target: str, app_uuid: Optional[str], report_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Map caller-facing identifiers to (application_id, target_id)."""
    if target == "none":
        if app_uuid or report_id:
            raise JobError("This job kind creates its own application — don't pass app_uuid or report_id.")
        return None, None
    if target == "application":
        if not app_uuid:
            raise JobError("app_uuid is required for this job kind.")
        row = db.execute("SELECT id FROM applications WHERE uuid = ?", (app_uuid,)).fetchone()
        if not row:
            raise JobNotFound(f"No application found with uuid={app_uuid!r}")
        return row["id"], None
    if not report_id:
        raise JobError("report_id is required for this job kind.")
    row = db.execute("SELECT application_id FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise JobNotFound(f"No report found with id={report_id!r}")
    return row["application_id"], report_id


def _check(job: dict, params: dict) -> None:
    kind = get_kind(job["kind"])
    with get_connection() as db:
        ctx = load_context(db, kind.target, job["application_id"], job["target_id"])
    kind.check(ctx, params)


def _preview(job: dict) -> dict:
    """The prompt as it would be sent now, with cv-library blocks elided."""
    kind = get_kind(job["kind"])
    try:
        with get_connection() as db:
            ctx = load_context(db, kind.target, job["application_id"], job["target_id"])
        return compact_prompt(kind.preview(ctx, job["params"]))
    except Exception as e:
        log.warning("Prompt preview failed for job %s: %s", job["id"], e)
        return {"error": f"Preview unavailable: {e}"}


# ── Views ─────────────────────────────────────────────────────────────────────

_NEXT_STEP = {
    "draft":     "Show the draft (params and prompt_preview) to the applicant. Edit with update_draft; send with submit_draft; discard with cancel_job.",
    "queued":    "Queued. Carry on with other work and check back with get_job, or call wait_for_job if there's nothing else to do.",
    "running":   "Running. Carry on with other work and check back with get_job, or call wait_for_job if there's nothing else to do.",
    "succeeded": "Done — see result.",
    "failed":    "Failed — see error. retry_job re-queues the same request (optionally as a draft to edit first).",
    "cancelled": "Cancelled. retry_job can revive it as a new job.",
}


def _view(job: dict, include_prompt: bool = False) -> dict:
    kind = get_kind(job["kind"])
    view = {
        "job_id":           job["id"],
        "kind":             job["kind"],
        "status":           job["status"],
        "app_uuid":         job.get("app_uuid"),
        "company":          job.get("company"),
        "role":             job.get("role"),
        "report_id":        job["target_id"],
        "params":           job["params"],
        "version":          job["version"],
        "created_by":       job["created_by"],
        "submitted_by":     job["submitted_by"],
        "created_at":       job["created_at"],
        "submitted_at":     job["submitted_at"],
        "started_at":       job["started_at"],
        "finished_at":      job["finished_at"],
        "cancel_requested": bool(job["cancel_requested"]),
        "next_step":        _NEXT_STEP[job["status"]],
    }
    if job["status"] == "succeeded":
        view["result"] = json.loads(job["result"]) if job["result"] else None
    if job["error"]:
        view["error"] = job["error"]

    if job["status"] == "draft":
        view["editable_params"] = kind.describe_params()
        preview = _preview(job)
    else:
        preview = json.loads(job["prompt_snapshot"]) if job["prompt_snapshot"] else None

    if preview is not None:
        if include_prompt or "error" in preview:
            view["prompt_preview"] = preview
        else:
            # The system prompt is the same for every job of a kind; the user message is
            # what this request changed. Full text on request.
            view["prompt_preview"] = {
                "user":         preview["user"],
                "notes":        preview["notes"],
                "system_chars": len(preview["system"]),
                "hint":         "System prompt omitted; pass include_prompt=true to see it.",
            }
    return view


# ── Create / edit ─────────────────────────────────────────────────────────────

def create(
    kind_name:  str,
    *,
    app_uuid:   Optional[str]  = None,
    report_id:  Optional[str]  = None,
    params:     Optional[dict] = None,
    draft:      bool           = False,
    created_by: str            = "agent",
) -> dict:
    """
    Create a job. With draft=True it stops at 'draft' for review; otherwise it's queued
    and dispatched immediately. Either way the call returns at once with the job_id.
    """
    if created_by not in ACTORS:
        raise JobError(f"created_by must be one of {ACTORS}")
    kind   = get_kind(kind_name)
    params = normalise_params(kind.params, params)

    job_id = str(uuid.uuid4())
    now    = _now()
    with get_connection() as db:
        application_id, target_id = _resolve_target(db, kind.target, app_uuid, report_id)
        ctx = load_context(db, kind.target, application_id, target_id)
        kind.check(ctx, params)
        db.execute(
            """INSERT INTO generation_jobs
               (id, kind, status, application_id, target_id, params, version,
                created_by, created_at, updated_at)
               VALUES (?, ?, 'draft', ?, ?, ?, 1, ?, ?, ?)""",
            (job_id, kind.name, application_id, target_id, json.dumps(params), created_by, now, now),
        )

    if draft:
        return get(job_id)
    try:
        return submit(job_id, submitted_by=created_by)
    except JobError:
        # The caller asked for a job, not a draft: don't leave one behind if it was refused.
        with get_connection() as db:
            db.execute("DELETE FROM generation_jobs WHERE id = ? AND status = 'draft'", (job_id,))
        raise


def update_draft(job_id: str, params: dict, expected_version: Optional[int] = None) -> dict:
    """
    Merge params into a draft. Pass a key with value None to reset it to its default.
    expected_version guards against overwriting an edit you haven't seen.
    """
    with get_connection() as db:
        job = _row(db, job_id)
    if job["status"] != "draft":
        raise JobError(f"Job {job_id} is {job['status']}, not a draft — it can no longer be edited.")
    if expected_version is not None and expected_version != job["version"]:
        raise JobError(
            f"Draft {job_id} has been edited since version {expected_version} (now version "
            f"{job['version']}). Re-read it with get_job before editing."
        )

    kind   = get_kind(job["kind"])
    merged = {**job["params"], **(params or {})}
    merged = {k: v for k, v in merged.items() if v is not None}
    merged = normalise_params(kind.params, merged)
    _check(job, merged)

    with get_connection() as db:
        cur = db.execute(
            """UPDATE generation_jobs SET params = ?, version = version + 1, updated_at = ?
               WHERE id = ? AND status = 'draft' AND version = ?""",
            (json.dumps(merged), _now(), job_id, job["version"]),
        )
        if cur.rowcount == 0:
            raise JobError(f"Draft {job_id} changed while you were editing it. Re-read it with get_job and try again.")
    return get(job_id)


# ── Submit / cancel / retry ───────────────────────────────────────────────────

def submit(job_id: str, submitted_by: str = "agent", expected_version: Optional[int] = None) -> dict:
    """Move a draft to 'queued' and dispatch it. Returns immediately."""
    from pipeline.jobs.runner import get_runner

    if submitted_by not in ACTORS:
        raise JobError(f"submitted_by must be one of {ACTORS}")
    with get_connection() as db:
        job = _row(db, job_id)
    if job["status"] != "draft":
        raise JobError(f"Job {job_id} is {job['status']} — only a draft can be submitted.")
    if expected_version is not None and expected_version != job["version"]:
        raise JobError(
            f"Draft {job_id} has been edited since version {expected_version} (now version "
            f"{job['version']}). Review the current version before submitting it."
        )

    _check(job, job["params"])
    snapshot = json.dumps(_preview(job))
    kind     = get_kind(job["kind"])
    now      = _now()

    with get_connection() as db:
        # One active job per (kind, target): two generate_cv jobs on one application would
        # both pass their precondition check and then race to write. Intake kinds have no
        # target yet, so they're exempt. The guard and the transition are one statement,
        # so two submits can't both slip through.
        guard = ""
        args: list = [snapshot, submitted_by, now, now, job_id, job["version"]]
        if kind.target != "none":
            guard = """AND NOT EXISTS (
                SELECT 1 FROM generation_jobs o
                WHERE o.kind = ? AND o.application_id IS ? AND o.target_id IS ?
                  AND o.status IN ('queued', 'running') AND o.id != ?)"""
            args += [job["kind"], job["application_id"], job["target_id"], job_id]
        cur = db.execute(
            f"""UPDATE generation_jobs
                SET status = 'queued', prompt_snapshot = ?, submitted_by = ?,
                    submitted_at = ?, updated_at = ?
                WHERE id = ? AND status = 'draft' AND version = ? {guard}""",
            args,
        )
        if cur.rowcount == 0:
            clash = db.execute(
                """SELECT id, status FROM generation_jobs
                   WHERE kind = ? AND application_id IS ? AND target_id IS ?
                     AND status IN ('queued', 'running') AND id != ?""",
                (job["kind"], job["application_id"], job["target_id"], job_id),
            ).fetchone()
            if clash:
                raise JobError(
                    f"A {job['kind']} job for this target is already {clash['status']} "
                    f"(job_id={clash['id']}). Wait for it or cancel it first."
                )
            raise JobError(f"Draft {job_id} changed while being submitted. Re-read it with get_job.")

    get_runner().dispatch(job_id)
    return get(job_id)


def cancel(job_id: str) -> dict:
    """
    Discard a draft, withdraw a queued job, or ask a running job to stop.

    A running job's model call can't be interrupted — it finishes and the tokens are
    spent — but its output is discarded before anything is written.
    """
    now = _now()
    with get_connection() as db:
        job = _row(db, job_id)
        if job["status"] in TERMINAL:
            raise JobError(f"Job {job_id} already {job['status']}.")
        if job["status"] == "running":
            db.execute(
                "UPDATE generation_jobs SET cancel_requested = 1, updated_at = ? WHERE id = ? AND status = 'running'",
                (now, job_id),
            )
        else:
            cur = db.execute(
                """UPDATE generation_jobs
                   SET status = 'cancelled', finished_at = ?, updated_at = ?
                   WHERE id = ? AND status IN ('draft', 'queued')""",
                (now, now, job_id),
            )
            if cur.rowcount == 0:  # a worker claimed it between our read and write
                db.execute(
                    "UPDATE generation_jobs SET cancel_requested = 1, updated_at = ? WHERE id = ? AND status = 'running'",
                    (now, job_id),
                )
    return get(job_id)


def retry(job_id: str, draft: bool = False, created_by: str = "agent") -> dict:
    """Start a new job with the same kind, target and params as a finished one."""
    with get_connection() as db:
        job = _row(db, job_id)
    if job["status"] not in TERMINAL:
        raise JobError(f"Job {job_id} is {job['status']} — only a finished job can be retried.")
    return create(
        job["kind"],
        app_uuid=job.get("app_uuid") if get_kind(job["kind"]).target == "application" else None,
        report_id=job["target_id"],
        params=job["params"],
        draft=draft,
        created_by=created_by,
    )


# ── Read ──────────────────────────────────────────────────────────────────────

def describe_kinds() -> dict:
    """Every job kind: what it does, what it targets, and its editable params."""
    return {
        name: {"description": k.description, "target": k.target, "params": k.describe_params()}
        for name, k in sorted(REGISTRY.items()) if not name.startswith("_")
    }


def get(job_id: str, include_prompt: bool = False) -> dict:
    with get_connection() as db:
        job = _row(db, job_id)
    return _view(job, include_prompt=include_prompt)


def list_jobs(
    status:   Optional[str] = None,
    app_uuid: Optional[str] = None,
    kind:     Optional[str] = None,
    limit:    int           = 25,
) -> list[dict]:
    """Newest first. Summary rows only — no results or prompts; use get() for those."""
    sql, args = [], []
    if status:
        sql.append("j.status = ?"); args.append(status)
    if app_uuid:
        sql.append("a.uuid = ?"); args.append(app_uuid)
    if kind:
        sql.append("j.kind = ?"); args.append(kind)
    where = ("WHERE " + " AND ".join(sql)) if sql else ""
    with get_connection() as db:
        rows = db.execute(
            f"""SELECT j.id, j.kind, j.status, a.uuid AS app_uuid, a.company, a.role,
                       j.target_id, j.created_by, j.submitted_by, j.error,
                       j.created_at, j.submitted_at, j.finished_at
                FROM generation_jobs j LEFT JOIN applications a ON a.id = j.application_id
                {where}
                ORDER BY j.created_at DESC LIMIT ?""",
            (*args, max(1, min(limit, 200))),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["job_id"]    = d.pop("id")
        d["report_id"] = d.pop("target_id")
        out.append(d)
    return out


def wait(job_id: str, timeout: float = 30.0) -> dict:
    """
    Block until the job leaves queued/running or `timeout` seconds pass, then return it.
    A draft returns at once — nothing happens to it until someone submits it.
    """
    from pipeline.jobs.runner import get_runner

    get_runner().wait(job_id, timeout)
    return get(job_id)
