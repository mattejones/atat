"""
cover_letter.py — Routes for cover letter generation, editing, and rendering.

One cover letter per application. Content is stored in the database and mirrored
to cover_letter.md in the application's output directory.

Endpoints:
  GET    /cover-letter/{app_uuid}           — fetch current state
  POST   /cover-letter/{app_uuid}/generate  — run two-phase pipeline
  PUT    /cover-letter/{app_uuid}           — save edited markdown (auto-save)
  POST   /cover-letter/{app_uuid}/render    — render cover_letter.md → cover_letter.pdf
  GET    /cover-letter/{app_uuid}/pdf       — serve the rendered PDF
"""

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.database import get_db, row_to_dict
from pipeline.config import LLM_MODEL, OUTPUT_PATH

log = logging.getLogger(__name__)

router = APIRouter(prefix="/cover-letter", tags=["cover-letter"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_app_by_uuid(app_uuid: str, db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT * FROM applications WHERE uuid = ?", (app_uuid,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row_to_dict(row)


def _get_or_create_cover_letter(app_id: str, db: sqlite3.Connection) -> dict:
    """Return the cover letter row, creating a draft row if one doesn't exist."""
    row = db.execute(
        "SELECT * FROM cover_letters WHERE application_id = ?", (app_id,)
    ).fetchone()
    if row:
        return row_to_dict(row)

    cl_id = str(uuid.uuid4())
    now   = datetime.now().isoformat()
    db.execute(
        """INSERT INTO cover_letters
           (id, application_id, status, created_at, updated_at)
           VALUES (?, ?, 'draft', ?, ?)""",
        (cl_id, app_id, now, now),
    )
    row = db.execute(
        "SELECT * FROM cover_letters WHERE id = ?", (cl_id,)
    ).fetchone()
    return row_to_dict(row)


def _output_dir(app: dict) -> Path:
    return Path(app["output_dir"]) if app.get("output_dir") else OUTPUT_PATH / app["id"]


def _extract_name_contact(cv_markdown: Optional[str]) -> tuple[str, str]:
    """Pull name and contact string from the application's CV markdown."""
    if not cv_markdown:
        return "", ""
    try:
        from pipeline.parse_cv import parse_cv
        parsed = parse_cv(cv_markdown)
        return parsed.name, parsed.contact
    except Exception as e:
        log.warning("Could not parse CV for name/contact: %s", e)
        return "", ""


# ── Pydantic models ───────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    research_company: bool          = True
    research_role:    bool          = True
    draft_input:      Optional[str] = None
    key_points:       Optional[str] = None


class CoverLetterUpdate(BaseModel):
    markdown: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{app_uuid}")
def get_cover_letter(
    app_uuid: str,
    db:       sqlite3.Connection = Depends(get_db),
):
    """Return the current cover letter state, creating a draft record if absent."""
    app = _get_app_by_uuid(app_uuid, db)
    cl  = _get_or_create_cover_letter(app["id"], db)

    out_dir = _output_dir(app)
    cl["has_pdf"] = (out_dir / "cover_letter.pdf").exists()
    return cl


@router.post("/{app_uuid}/generate")
def generate_cover_letter(
    app_uuid: str,
    body:     GenerateRequest,
    db:       sqlite3.Connection = Depends(get_db),
):
    """
    Run the two-phase cover letter pipeline.
    Phase 1 (optional): company/role research via web search.
    Phase 2: LLM generation using CV context, JD, reasoning, and research brief.
    """
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]

    jd_text     = app.get("jd_text")     or ""
    cv_markdown = app.get("cv_markdown") or ""
    reasoning   = app.get("reasoning")

    if not jd_text and not cv_markdown:
        raise HTTPException(
            status_code=422,
            detail="Application has no JD or CV content — cannot generate a cover letter.",
        )

    from pipeline.cover_letter_generator import generate_cover_letter as _generate

    try:
        markdown, brief = _generate(
            company=          app.get("company") or "Unknown",
            role=             app.get("role")    or "Unknown",
            jd_text=          jd_text,
            cv_markdown=      cv_markdown,
            reasoning=        reasoning,
            research_company= body.research_company,
            research_role=    body.research_role,
            draft_input=      body.draft_input,
            key_points=       body.key_points,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    now     = datetime.now().isoformat()
    out_dir = _output_dir(app)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cover_letter.md").write_text(markdown, encoding="utf-8")

    if brief and brief.combined:
        (out_dir / "cover_letter_research.md").write_text(
            brief.combined, encoding="utf-8"
        )

    cl = _get_or_create_cover_letter(app_id, db)
    db.execute(
        """UPDATE cover_letters
           SET markdown         = ?,
               status           = 'generated',
               research_company = ?,
               research_role    = ?,
               draft_input      = ?,
               key_points       = ?,
               research_brief   = ?,
               model            = ?,
               generated_at     = ?,
               updated_at       = ?
           WHERE id = ?""",
        (
            markdown,
            int(body.research_company),
            int(body.research_role),
            body.draft_input,
            body.key_points,
            brief.combined if brief else None,
            LLM_MODEL,
            now,
            now,
            cl["id"],
        ),
    )

    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, detail)
           VALUES (?, 'cover_letter_generated', 'Cover letter generated')""",
        (app_id,),
    )

    row    = db.execute(
        "SELECT * FROM cover_letters WHERE id = ?", (cl["id"],)
    ).fetchone()
    result = row_to_dict(row)
    result["has_pdf"] = False
    return result


@router.put("/{app_uuid}")
def update_cover_letter(
    app_uuid: str,
    body:     CoverLetterUpdate,
    db:       sqlite3.Connection = Depends(get_db),
):
    """Save edited markdown. Mirrors to cover_letter.md. Auto-save target."""
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
    cl     = _get_or_create_cover_letter(app_id, db)

    now     = datetime.now().isoformat()
    out_dir = _output_dir(app)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cover_letter.md").write_text(body.markdown, encoding="utf-8")

    db.execute(
        """UPDATE cover_letters
           SET markdown   = ?,
               status     = 'edited',
               updated_at = ?
           WHERE id = ?""",
        (body.markdown, now, cl["id"]),
    )

    row    = db.execute(
        "SELECT * FROM cover_letters WHERE id = ?", (cl["id"],)
    ).fetchone()
    result = row_to_dict(row)
    out_dir = _output_dir(app)
    result["has_pdf"] = (out_dir / "cover_letter.pdf").exists()
    return result


@router.post("/{app_uuid}/render")
def render_cover_letter_route(
    app_uuid: str,
    db:       sqlite3.Connection = Depends(get_db),
):
    """Render cover_letter.md → cover_letter.pdf via Typst."""
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
    cl     = _get_or_create_cover_letter(app_id, db)

    if not cl.get("markdown"):
        raise HTTPException(
            status_code=422,
            detail="No cover letter content — generate one first.",
        )

    out_dir = _output_dir(app)
    cl_path = out_dir / "cover_letter.md"

    # Ensure the file is current (may differ from DB if edited externally)
    out_dir.mkdir(parents=True, exist_ok=True)
    cl_path.write_text(cl["markdown"], encoding="utf-8")

    name, contact = _extract_name_contact(app.get("cv_markdown"))

    try:
        from pipeline.render import render_cover_letter
        render_cover_letter(
            cl_md_path=cl_path,
            output_dir=out_dir,
            name=name,
            contact=contact,
            company=app.get("company") or "",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {e}")

    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, detail)
           VALUES (?, 'cover_letter_rendered', 'Cover letter PDF rendered')""",
        (app_id,),
    )

    return {"status": "rendered", "pdf": "cover_letter.pdf"}


@router.get("/{app_uuid}/pdf")
def get_cover_letter_pdf(
    app_uuid: str,
    db:       sqlite3.Connection = Depends(get_db),
):
    """Serve the rendered cover letter PDF."""
    app     = _get_app_by_uuid(app_uuid, db)
    out_dir = _output_dir(app)
    pdf     = out_dir / "cover_letter.pdf"

    if not pdf.exists():
        raise HTTPException(
            status_code=404,
            detail="Cover letter PDF not found — render it first.",
        )

    name, _ = _extract_name_contact(app.get("cv_markdown"))
    company = (app.get("company") or "").replace(" ", "")
    name_slug = name.replace(" ", "") if name else ""
    filename  = f"{name_slug}{company}CoverLetter.pdf" if name_slug else "cover_letter.pdf"

    return FileResponse(
        path=str(pdf),
        media_type="application/pdf",
        filename=filename,
    )
