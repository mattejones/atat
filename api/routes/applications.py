"""
applications.py — Routes for managing job applications.
"""

import json
import re
import shutil
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.database import get_db, rows_to_list, row_to_dict
from pipeline.config import OUTPUT_PATH

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


class ManualApplicationCreate(BaseModel):
    company:      str
    role:         str
    source_url:   Optional[str] = None
    status:       str           = "applied"
    notes:        Optional[str] = None
    applied_date: Optional[str] = None  # YYYY-MM-DD


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_STATUSES = {
    "generated", "reviewing", "applied", "acknowledged",
    "interviewing", "offered", "rejected", "excluded", "archived",
}

VALID_TIERS        = {"T1", "T2", "T3", "EX1"}
VALID_ARRANGEMENTS = {"remote", "hybrid", "office"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enrich(app: dict) -> dict:
    out_dir = Path(app.get("output_dir") or "")
    app["has_cv"]  = (out_dir / "cv.md").exists()  if out_dir.exists() else False
    app["has_pdf"] = (out_dir / "cv.pdf").exists() if out_dir.exists() else bool(app.get("has_pdf"))
    return app


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
    return [_enrich(row_to_dict(r)) for r in rows]


@router.get("/{app_id}")
def get_application(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return _enrich(row_to_dict(row))


# ── Create (manual) ───────────────────────────────────────────────────────────

@router.post("")
def create_manual_application(
    body: ManualApplicationCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Log an application that was submitted outside ATAT — no CV or JD required.
    Creates a minimal DB record with the given metadata.
    """
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {sorted(VALID_STATUSES)}"
        )

    today        = date.today().isoformat()
    company_slug = re.sub(r'[^a-z0-9]+', '-', body.company.lower())[:30].strip('-')
    role_slug    = re.sub(r'[^a-z0-9]+', '-', body.role.lower())[:40].strip('-')
    app_id       = f"{today}_{company_slug}_{role_slug}"

    # Avoid collisions
    if db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
        app_id = f"{app_id}_{str(uuid.uuid4())[:6]}"

    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO applications
           (id, company, role, source_url, status, notes,
            has_pdf, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (app_id, body.company, body.role, body.source_url,
         body.status, body.notes, now, now)
    )
    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, to_status, detail)
           VALUES (?, 'status_change', ?, 'Application logged manually')""",
        (app_id, body.status)
    )

    # Optionally record the applied date
    if body.applied_date and body.status in ("applied", "acknowledged", "interviewing", "offered", "rejected"):
        db.execute(
            """INSERT INTO application_dates (application_id, date_type, date)
               VALUES (?, 'applied', ?)""",
            (app_id, body.applied_date)
        )

    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return _enrich(row_to_dict(row))


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
        return current

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

    return _enrich(row_to_dict(db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()))


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
            cv = parse_cv(row["cv_markdown"])
            download_name = pdf_filename(cv.name, row["company"] or "")
        else:
            download_name = f"{app_id}.pdf"
    except Exception:
        download_name = f"{app_id}.pdf"

    return FileResponse(path=str(pdf_path), media_type="application/pdf", filename=download_name)


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
