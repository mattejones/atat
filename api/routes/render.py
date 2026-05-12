"""
render.py — Route for triggering PDF render from an existing cv.md.
Accepts application uuid as the path parameter.
"""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from db.database import get_db
from pipeline.config import OUTPUT_PATH
from pipeline.render import render_cv

router = APIRouter(prefix="/render", tags=["render"])


def _get_app_by_uuid(app_uuid: str, db: sqlite3.Connection) -> dict:
    from db.database import row_to_dict
    row = db.execute("SELECT * FROM applications WHERE uuid = ?", (app_uuid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row_to_dict(row)


@router.post("/{app_uuid}")
def render_application(
    app_uuid: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Render cv.md → cv.pdf and update the database record."""
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]

    out_dir = Path(app["output_dir"]) if app.get("output_dir") else OUTPUT_PATH / app_id
    cv_path = out_dir / "cv.md"

    if not cv_path.exists() and app.get("cv_markdown"):
        out_dir.mkdir(parents=True, exist_ok=True)
        cv_path.write_text(app["cv_markdown"], encoding="utf-8")

    if not cv_path.exists():
        raise HTTPException(status_code=404, detail="cv.md not found")

    try:
        render_cv(cv_path, out_dir, company=app.get("company") or "")
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
