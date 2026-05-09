"""
applications.py — Routes for managing job applications.
"""

import json
import logging
import re
import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.database import get_db, rows_to_list, row_to_dict
from pipeline.config import OUTPUT_PATH

log = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["applications"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class ApplicationUpdate(BaseModel):
    company:          Optional[str]   = None
    role:             Optional[str]   = None
    source_url:       Optional[str]   = None
    tier:             Optional[str]   = None
    status:           Optional[str]   = None
    notes:            Optional[str]   = None
    location:         Optional[str]   = None
    work_arrangement: Optional[str]   = None
    hybrid_days:      Optional[int]   = None
    salary_min:       Optional[int]   = None
    salary_max:       Optional[int]   = None
    salary_currency:  Optional[str]   = None


class DateCreate(BaseModel):
    date_type: str
    date:      str
    notes:     Optional[str] = None


class AppliedDateUpsert(BaseModel):
    date: Optional[str] = None  # None or empty = delete


class ManualApplicationCreate(BaseModel):
    company:      str
    role:         str
    source_url:   Optional[str] = None
    status:       str           = "applied"
    notes:        Optional[str] = None
    applied_date: Optional[str] = None


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_STATUSES = {
    "generated", "reviewing", "applied", "acknowledged",
    "interviewing", "case_study", "offered",
    "rejected", "ghosted", "excluded", "archived",
}
VALID_TIERS        = {"T1", "T2", "T3", "EX1"}
VALID_ARRANGEMENTS = {"remote", "hybrid", "office"}

# Statuses eligible for auto-ghosting — active but waiting on employer response
_GHOSTABLE_STATUSES = ("applied", "acknowledged", "interviewing", "case_study")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enrich(app: dict) -> dict:
    out_dir = Path(app.get("output_dir") or "")
    app["has_cv"]  = (out_dir / "cv.md").exists()  if out_dir.exists() else False
    app["has_pdf"] = (out_dir / "cv.pdf").exists() if out_dir.exists() else bool(app.get("has_pdf"))
    return app


def _with_applied_date(rows: list, db: sqlite3.Connection) -> list[dict]:
    """
    Enrich a list of application rows with their applied_date from application_dates.
    Uses a single query rather than N+1.
    """
    apps = [_enrich(row_to_dict(r)) for r in rows]
    if not apps:
        return apps

    ids          = [a["id"] for a in apps]
    placeholders = ",".join("?" * len(ids))
    date_rows    = db.execute(
        f"""SELECT application_id, date FROM application_dates
            WHERE application_id IN ({placeholders}) AND date_type = 'applied'
            ORDER BY date DESC""",
        ids,
    ).fetchall()

    # Keep only the most recent applied date per application
    date_map: dict[str, str] = {}
    for row in date_rows:
        app_id = row["application_id"]
        if app_id not in date_map:
            date_map[app_id] = row["date"]

    for app in apps:
        app["applied_date"] = date_map.get(app["id"])

    return apps


def _upsert_applied_date_if_missing(app_id: str, db: sqlite3.Connection) -> None:
    """
    Insert today as the applied date only if no applied date already exists.
    Called automatically when status transitions to 'applied'.
    """
    existing = db.execute(
        "SELECT 1 FROM application_dates WHERE application_id = ? AND date_type = 'applied'",
        (app_id,),
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
            (app_id, date.today().isoformat()),
        )


# ── Auto-ghost logic ──────────────────────────────────────────────────────────

def _do_auto_ghost(db: sqlite3.Connection, days: int) -> list[str]:
    """
    Core ghost logic — runs against a provided connection.

    Finds applications whose status is in _GHOSTABLE_STATUSES and whose most
    recent status_change event (falling back to updated_at) is older than `days`
    days. Transitions each to 'ghosted' and writes an event row.

    Returns the list of application IDs that were ghosted.
    """
    cutoff       = (datetime.now() - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" * len(_GHOSTABLE_STATUSES))

    rows = db.execute(
        f"""
        SELECT a.id, a.status,
               COALESCE(
                   (SELECT MAX(occurred_at) FROM application_events
                    WHERE application_id = a.id AND event_type = 'status_change'),
                   a.updated_at
               ) AS last_status_change
        FROM applications a
        WHERE a.status IN ({placeholders})
        """,
        _GHOSTABLE_STATUSES,
    ).fetchall()

    ghosted_ids: list[str] = []
    for row in rows:
        last = row["last_status_change"]
        if last and last < cutoff:
            db.execute(
                "UPDATE applications SET status = 'ghosted' WHERE id = ?",
                (row["id"],),
            )
            db.execute(
                """INSERT INTO application_events
                   (application_id, event_type, from_status, to_status, detail)
                   VALUES (?, 'status_change', ?, 'ghosted', ?)""",
                (row["id"], row["status"], f"Auto-ghosted after {days} days of inactivity"),
            )
            ghosted_ids.append(row["id"])

    return ghosted_ids


def run_auto_ghost_job() -> None:
    """
    Called by APScheduler — manages its own DB connection via the context manager.
    Safe to call from a background thread.
    """
    from pipeline.config import AUTO_GHOST_DAYS
    from db.database import get_connection

    try:
        with get_connection() as db:
            ghosted = _do_auto_ghost(db, AUTO_GHOST_DAYS)
            if ghosted:
                log.info("Auto-ghost: marked %d application(s) as ghosted", len(ghosted))
            else:
                log.debug("Auto-ghost: no applications eligible for ghosting")
    except Exception:
        log.exception("Auto-ghost job failed")


# ── List & get ────────────────────────────────────────────────────────────────

@router.get("")
def list_applications(
    include_archived: bool = False,
    db: sqlite3.Connection = Depends(get_db),
):
    if include_archived:
        rows = db.execute("SELECT * FROM applications ORDER BY created_at DESC").fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM applications WHERE status != 'archived' ORDER BY created_at DESC"
        ).fetchall()
    return _with_applied_date(rows, db)


@router.get("/recent-notes")
def get_recent_notes(
    limit: int = 10,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Return the most recent distinct generation notes for display on the
    New Application page. Lets users re-use previous AI instructions.

    Declared before /{app_id} to prevent FastAPI matching 'recent-notes'
    as an app_id path parameter.
    """
    rows = db.execute(
        """
        SELECT generation_notes AS text, company, role
        FROM applications
        WHERE generation_notes IS NOT NULL
          AND trim(generation_notes) != ''
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    # Deduplicate by note text, keeping the most recent company/role context
    seen:   set      = set()
    result: list     = []
    for row in rows:
        text = row["text"].strip()
        if text not in seen:
            seen.add(text)
            result.append({
                "text":    text,
                "company": row["company"],
                "role":    row["role"],
            })

    return result


@router.get("/{app_id}")
def get_application(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    apps = _with_applied_date([row], db)
    return apps[0]


# ── Create (manual) ───────────────────────────────────────────────────────────

@router.post("")
def create_manual_application(
    body: ManualApplicationCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status: {body.status}")

    today        = date.today().isoformat()
    company_slug = re.sub(r'[^a-z0-9]+', '-', body.company.lower())[:30].strip('-')
    role_slug    = re.sub(r'[^a-z0-9]+', '-', body.role.lower())[:40].strip('-')
    app_id       = f"{today}_{company_slug}_{role_slug}"

    if db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
        app_id = f"{app_id}_{str(uuid.uuid4())[:6]}"

    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO applications
           (id, company, role, source_url, status, notes, has_pdf, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (app_id, body.company, body.role, body.source_url, body.status, body.notes, now, now)
    )
    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, to_status, detail)
           VALUES (?, 'status_change', ?, 'Application logged manually')""",
        (app_id, body.status)
    )

    # Auto-set applied date when created with applied status and no explicit date given
    if body.status == "applied" and not body.applied_date:
        body.applied_date = today

    if body.applied_date:
        db.execute(
            "INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
            (app_id, body.applied_date)
        )

    row  = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    apps = _with_applied_date([row], db)
    return apps[0]


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{app_id}")
def update_application(
    app_id: str,
    body: ApplicationUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")

    current = row_to_dict(row)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        apps = _with_applied_date([row], db)
        return apps[0]

    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status: {updates['status']}")
    if "tier" in updates and updates["tier"] not in VALID_TIERS:
        raise HTTPException(status_code=422, detail=f"Invalid tier: {updates['tier']}")
    if "work_arrangement" in updates and updates["work_arrangement"] not in VALID_ARRANGEMENTS:
        raise HTTPException(status_code=422, detail=f"Invalid work_arrangement: {updates['work_arrangement']}")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(f"UPDATE applications SET {set_clause} WHERE id = ?", list(updates.values()) + [app_id])

    if "status" in updates and updates["status"] != current["status"]:
        db.execute(
            """INSERT INTO application_events
               (application_id, event_type, from_status, to_status)
               VALUES (?, 'status_change', ?, ?)""",
            (app_id, current["status"], updates["status"])
        )
        # Auto-set applied date when transitioning to 'applied' with no existing date
        if updates["status"] == "applied":
            _upsert_applied_date_if_missing(app_id, db)

    updated = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    apps    = _with_applied_date([updated], db)
    return apps[0]


# ── Auto-ghost endpoint ───────────────────────────────────────────────────────

@router.post("/auto-ghost")
def trigger_auto_ghost(db: sqlite3.Connection = Depends(get_db)):
    """
    Manually trigger the auto-ghost check.
    Uses AUTO_GHOST_DAYS from config. Safe to call at any time.
    """
    from pipeline.config import AUTO_GHOST_DAYS
    ghosted = _do_auto_ghost(db, AUTO_GHOST_DAYS)
    log.info("Manual auto-ghost triggered: %d application(s) ghosted", len(ghosted))
    return {"ghosted": len(ghosted), "ids": ghosted, "days_threshold": AUTO_GHOST_DAYS}


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{app_id}")
def delete_application(
    app_id: str,
    delete_files: bool = False,
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute("SELECT output_dir FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    db.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    if delete_files and row["output_dir"]:
        out = Path(row["output_dir"])
        if out.exists():
            shutil.rmtree(out)
    return {"status": "deleted", "files_removed": delete_files}


# ── CV Markdown ───────────────────────────────────────────────────────────────

@router.get("/{app_id}/cv")
def get_cv_markdown(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT cv_markdown, output_dir FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    content = row["cv_markdown"]
    if not content and row["output_dir"]:
        cv_path = Path(row["output_dir"]) / "cv.md"
        if cv_path.exists():
            content = cv_path.read_text(encoding="utf-8")
    if not content:
        raise HTTPException(status_code=404, detail="CV content not found")
    return {"content": content}


@router.put("/{app_id}/cv")
def update_cv_markdown(app_id: str, body: dict, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT output_dir FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    content = body.get("content", "")
    db.execute("UPDATE applications SET cv_markdown = ? WHERE id = ?", (content, app_id))
    db.execute(
        "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'cv_edited', 'CV Markdown updated')",
        (app_id,)
    )
    if row["output_dir"]:
        cv_path = Path(row["output_dir"]) / "cv.md"
        if cv_path.parent.exists():
            cv_path.write_text(content, encoding="utf-8")
    return {"status": "saved"}


# ── PDF ───────────────────────────────────────────────────────────────────────

@router.get("/{app_id}/pdf")
def get_pdf(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT output_dir, company, cv_markdown FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not row or not row["output_dir"]:
        raise HTTPException(status_code=404, detail="Application not found")

    pdf_path = Path(row["output_dir"]) / "cv.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found — render it first")

    try:
        from pipeline.parse_cv import parse_cv
        from pipeline.render import pdf_filename
        if row["cv_markdown"]:
            cv            = parse_cv(row["cv_markdown"])
            download_name = pdf_filename(cv.name, row["company"] or "")
        else:
            download_name = f"{app_id}.pdf"
    except Exception:
        download_name = f"{app_id}.pdf"

    return FileResponse(path=str(pdf_path), media_type="application/pdf", filename=download_name)


# ── Reasoning ─────────────────────────────────────────────────────────────────

@router.get("/{app_id}/reasoning")
def get_reasoning(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute(
        "SELECT reasoning, output_dir FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    reasoning = row["reasoning"]
    if not reasoning and row["output_dir"]:
        r_path = Path(row["output_dir"]) / "reasoning.md"
        if r_path.exists():
            reasoning = r_path.read_text(encoding="utf-8")
    return {"content": reasoning or "", "has_reasoning": bool(reasoning)}


# ── Events ────────────────────────────────────────────────────────────────────

@router.get("/{app_id}/events")
def get_events(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM application_events WHERE application_id = ? ORDER BY occurred_at DESC",
        (app_id,)
    ).fetchall()
    return rows_to_list(rows)


# ── Dates ─────────────────────────────────────────────────────────────────────

@router.get("/{app_id}/dates")
def get_dates(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM application_dates WHERE application_id = ? ORDER BY date",
        (app_id,)
    ).fetchall()
    return rows_to_list(rows)


@router.post("/{app_id}/dates")
def add_date(app_id: str, body: DateCreate, db: sqlite3.Connection = Depends(get_db)):
    db.execute(
        "INSERT INTO application_dates (application_id, date_type, date, notes) VALUES (?, ?, ?, ?)",
        (app_id, body.date_type, body.date, body.notes)
    )
    if body.date_type == "applied":
        db.execute(
            "UPDATE applications SET status = 'applied' WHERE id = ? AND status IN ('generated','reviewing')",
            (app_id,)
        )
    return {"status": "created"}


@router.put("/{app_id}/dates/applied")
def upsert_applied_date(
    app_id: str,
    body: AppliedDateUpsert,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Upsert the applied date for an application.
    Passing date=None or omitting it clears the applied date.
    """
    if not db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Application not found")

    db.execute(
        "DELETE FROM application_dates WHERE application_id = ? AND date_type = 'applied'",
        (app_id,)
    )

    if body.date:
        db.execute(
            "INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
            (app_id, body.date)
        )

    return {"status": "saved", "date": body.date}


# ── Import from filesystem ────────────────────────────────────────────────────

@router.post("/import")
def import_from_filesystem(db: sqlite3.Connection = Depends(get_db)):
    if not OUTPUT_PATH.exists():
        return {"imported": 0, "skipped": 0}
    imported = skipped = 0
    for folder in sorted(OUTPUT_PATH.iterdir()):
        if not folder.is_dir():
            continue
        app_id = folder.name
        if db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
            skipped += 1
            continue
        meta = {}
        meta_path = folder / "run_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        cv_content = ""
        cv_path = folder / "cv.md"
        if cv_path.exists():
            cv_content = cv_path.read_text(encoding="utf-8")
        parts   = app_id.split("_", 2)
        company = parts[1].replace("-", " ").title() if len(parts) > 1 else app_id
        role    = parts[2].replace("-", " ").title() if len(parts) > 2 else ""
        has_pdf = (folder / "cv.pdf").exists()
        now     = datetime.now().isoformat()
        db.execute(
            """INSERT INTO applications
               (id, company, role, cv_markdown, output_dir, has_pdf,
                model, provider, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?)""",
            (app_id, company, role, cv_content, str(folder), int(has_pdf),
             meta.get("model", ""), meta.get("provider", ""), now, now)
        )
        imported += 1
    return {"imported": imported, "skipped": skipped}
