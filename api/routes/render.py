"""
render.py — Route for triggering PDF render from an existing cv.md.
Updates has_pdf in the database after successful render.
"""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from db.database import get_db
from pipeline.config import OUTPUT_PATH
from pipeline.render import render_cv

router = APIRouter(prefix="/render", tags=["render"])


@router.post("/{app_id}")
def render_application(
    app_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Render cv.md → cv.pdf and update the database record."""
    row = db.execute(
        "SELECT output_dir, cv_markdown, company FROM applications WHERE id = ?", (app_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Application not found")

    out_dir = Path(row["output_dir"]) if row["output_dir"] else OUTPUT_PATH / app_id
    cv_path = out_dir / "cv.md"

    if not cv_path.exists() and row["cv_markdown"]:
        out_dir.mkdir(parents=True, exist_ok=True)
        cv_path.write_text(row["cv_markdown"], encoding="utf-8")

    if not cv_path.exists():
        raise HTTPException(status_code=404, detail="cv.md not found")

    try:
        render_cv(cv_path, out_dir, company=row["company"] or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {e}")

    db.execute("UPDATE applications SET has_pdf = 1 WHERE id = ?", (app_id,))
    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, detail)
           VALUES (?, 'pdf_rendered', 'PDF rendered successfully')""",
        (app_id,)
    )

    return {"status": "rendered", "pdf": "cv.pdf"}
