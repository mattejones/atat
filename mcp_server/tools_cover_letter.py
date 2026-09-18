"""
tools_cover_letter.py — cover letter generation/editing MCP tools.

Mirrors api/routes/cover_letter.py. One cover letter per application, stored
in the DB and mirrored to cover_letter.md in the application's output dir.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import get_app_by_uuid, get_connection, output_dir_for, row_to_dict


def _extract_name_contact(cv_markdown: Optional[str]) -> tuple[str, str]:
    if not cv_markdown:
        return "", ""
    try:
        from pipeline.parse_cv import parse_cv
        parsed = parse_cv(cv_markdown)
        return parsed.name, parsed.contact
    except Exception:
        return "", ""


def _get_or_create_cover_letter(app_id: str, db) -> dict:
    row = db.execute("SELECT * FROM cover_letters WHERE application_id = ?", (app_id,)).fetchone()
    if row:
        return row_to_dict(row)
    cl_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db.execute(
        "INSERT INTO cover_letters (id, application_id, status, created_at, updated_at) VALUES (?, ?, 'draft', ?, ?)",
        (cl_id, app_id, now, now),
    )
    row = db.execute("SELECT * FROM cover_letters WHERE id = ?", (cl_id,)).fetchone()
    return row_to_dict(row)


@mcp.tool()
def get_cover_letter(app_uuid: str) -> dict:
    """Return the current cover letter state for an application, creating a draft record if absent."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        cl = _get_or_create_cover_letter(app["id"], db)
        out_dir = output_dir_for(app)
        cl["has_pdf"] = (out_dir / "cover_letter.pdf").exists()
        return cl


@mcp.tool()
def generate_cover_letter(
    app_uuid: str,
    research_company: bool = True,
    research_role: bool = True,
    draft_input: Optional[str] = None,
    key_points: Optional[str] = None,
    draft: bool = False,
) -> dict:
    """
    Generate a cover letter for an application via the two-phase pipeline
    (optional company/role web research, then LLM generation using CV context,
    JD, and reasoning). Overwrites any existing generated draft.

    ASYNC: returns immediately with a job_id; the cover letter arrives via get_job
    (and stays readable with get_cover_letter). draft=True returns a draft for review
    instead — note its prompt preview can't include the research brief, which only
    exists once the job runs.

    draft_input: optional rough draft supplied by the user to work from.
    key_points: optional bullet points the letter should hit.
    """
    from pipeline.jobs import service
    return service.create(
        "generate_cover_letter",
        app_uuid=app_uuid,
        params={
            "research_company": research_company, "research_role": research_role,
            "draft_input": draft_input, "key_points": key_points,
        },
        draft=draft,
    )


@mcp.tool()
def update_cover_letter(app_uuid: str, markdown: str) -> dict:
    """Save edited cover letter markdown. Mirrors to cover_letter.md on disk."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        cl = _get_or_create_cover_letter(app["id"], db)
        now = datetime.now().isoformat()
        out_dir = output_dir_for(app)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cover_letter.md").write_text(markdown, encoding="utf-8")
        db.execute(
            "UPDATE cover_letters SET markdown = ?, status = 'edited', updated_at = ? WHERE id = ?",
            (markdown, now, cl["id"]),
        )
        row = db.execute("SELECT * FROM cover_letters WHERE id = ?", (cl["id"],)).fetchone()
        result = row_to_dict(row)
        result["has_pdf"] = (out_dir / "cover_letter.pdf").exists()
        return result


@mcp.tool()
def render_cover_letter(app_uuid: str) -> dict:
    """Render cover_letter.md to cover_letter.pdf via Typst. Generate the letter first if none exists."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        cl = _get_or_create_cover_letter(app["id"], db)
        if not cl.get("markdown"):
            raise ValueError("No cover letter content — generate one first.")

        out_dir = output_dir_for(app)
        cl_path = out_dir / "cover_letter.md"
        out_dir.mkdir(parents=True, exist_ok=True)
        cl_path.write_text(cl["markdown"], encoding="utf-8")

        name, contact = _extract_name_contact(app.get("cv_markdown"))

        from pipeline.render import render_cover_letter as _render
        try:
            _render(
                cl_md_path=cl_path,
                output_dir=out_dir,
                name=name,
                contact=contact,
                company=app.get("company") or "",
            )
        except Exception as e:
            raise ValueError(f"Render failed: {e}")

        db.execute(
            "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'cover_letter_rendered', 'Cover letter PDF rendered')",
            (app["id"],),
        )
        return {"status": "rendered", "pdf_path": str(out_dir / "cover_letter.pdf")}
