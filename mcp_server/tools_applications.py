"""
tools_applications.py — read/write MCP tools for the core applications table.

Mirrors api/routes/applications.py. These are the primary tools an agent uses
to browse and update the application tracker.
"""

from datetime import date as _date
from pathlib import Path
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import (
    enrich_app,
    get_app_by_uuid,
    get_connection,
    row_to_dict,
    rows_to_list,
)

VALID_STATUSES = {
    "generated", "reviewing", "applied", "acknowledged",
    "interviewing", "case_study", "offered",
    "rejected", "ghosted", "excluded", "archived",
}
VALID_TIERS = {"T1", "T2", "T3", "EX1"}
VALID_ARRANGEMENTS = {"remote", "hybrid", "office"}


@mcp.tool()
def list_applications(include_archived: bool = False, status: Optional[str] = None) -> list[dict]:
    """
    List applications, most recent first.

    Args:
        include_archived: include applications with status='archived'.
        status: optionally filter to a single status value.
    """
    with get_connection() as db:
        if include_archived:
            rows = db.execute("SELECT * FROM applications ORDER BY created_at DESC").fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM applications WHERE status != 'archived' ORDER BY created_at DESC"
            ).fetchall()
        apps = [enrich_app(row_to_dict(r)) for r in rows]
        if status:
            apps = [a for a in apps if a["status"] == status]
        return apps


@mcp.tool()
def get_application(app_uuid: str) -> dict:
    """Get full detail for a single application, addressed by its uuid."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        return enrich_app(app)


@mcp.tool()
def get_cv_markdown(app_uuid: str) -> str:
    """Return the current composed CV markdown for an application."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
    content = app.get("cv_markdown")
    if not content and app.get("output_dir"):
        cv_path = Path(app["output_dir"]) / "cv.md"
        if cv_path.exists():
            content = cv_path.read_text(encoding="utf-8")
    if not content:
        raise ValueError("CV content not found for this application")
    return content


@mcp.tool()
def get_reasoning(app_uuid: str) -> str:
    """Return the LLM's chain-of-thought reasoning captured at generation time, if any."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
    reasoning = app.get("reasoning")
    if not reasoning and app.get("output_dir"):
        r_path = Path(app["output_dir"]) / "reasoning.md"
        if r_path.exists():
            reasoning = r_path.read_text(encoding="utf-8")
    return reasoning or ""


@mcp.tool()
def get_events(app_uuid: str) -> list[dict]:
    """Return the full audit-log timeline (status changes, notes, edits) for an application."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        rows = db.execute(
            "SELECT * FROM application_events WHERE application_id = ? ORDER BY occurred_at DESC",
            (app["id"],),
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def get_dates(app_uuid: str) -> list[dict]:
    """Return structured dates (applied, interview, offer_received, offer_deadline, rejected) for an application."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        rows = db.execute(
            "SELECT * FROM application_dates WHERE application_id = ? ORDER BY date",
            (app["id"],),
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def log_date(app_uuid: str, date_type: str, date: str, notes: Optional[str] = None) -> dict:
    """
    Record a structured date against an application.

    Args:
        date_type: one of applied | interview | offer_received | offer_deadline | rejected.
        date: ISO date string, e.g. "2026-07-09".
        notes: optional freetext, e.g. "phone screen with hiring manager".

    Logging an 'applied' date also flips status to 'applied' if the application
    is currently in 'generated' or 'reviewing'.
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        db.execute(
            "INSERT INTO application_dates (application_id, date_type, date, notes) VALUES (?, ?, ?, ?)",
            (app["id"], date_type, date, notes),
        )
        if date_type == "applied":
            db.execute(
                "UPDATE applications SET status = 'applied' WHERE id = ? AND status IN ('generated','reviewing')",
                (app["id"],),
            )
    return {"status": "created", "date_type": date_type, "date": date}


@mcp.tool()
def add_note(app_uuid: str, text: str) -> dict:
    """Append a freetext note to an application's notes field and log it as an event."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        existing = app.get("notes") or ""
        combined = f"{existing}\n\n{text}".strip() if existing else text
        db.execute("UPDATE applications SET notes = ? WHERE id = ?", (combined, app["id"]))
        db.execute(
            "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'note_added', ?)",
            (app["id"], text),
        )
    return {"status": "saved"}


@mcp.tool()
def update_application(
    app_uuid: str,
    company: Optional[str] = None,
    role: Optional[str] = None,
    source_url: Optional[str] = None,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    location: Optional[str] = None,
    work_arrangement: Optional[str] = None,
    hybrid_days: Optional[int] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    salary_currency: Optional[str] = None,
) -> dict:
    """
    Update fields on an application. Only arguments you pass are changed.

    Status changes are logged as application_events automatically, and moving
    to 'applied' backfills today's applied date if none is logged yet.

    tier: T1 | T2 | T3 | EX1
    status: generated | reviewing | applied | acknowledged | interviewing |
            case_study | offered | rejected | ghosted | excluded | archived
    work_arrangement: remote | hybrid | office
    """
    updates = {
        k: v for k, v in {
            "company": company, "role": role, "source_url": source_url, "tier": tier,
            "status": status, "notes": notes, "location": location,
            "work_arrangement": work_arrangement, "hybrid_days": hybrid_days,
            "salary_min": salary_min, "salary_max": salary_max,
            "salary_currency": salary_currency,
        }.items() if v is not None
    }
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {updates['status']!r}. Must be one of {sorted(VALID_STATUSES)}")
    if "tier" in updates and updates["tier"] not in VALID_TIERS:
        raise ValueError(f"Invalid tier: {updates['tier']!r}. Must be one of {sorted(VALID_TIERS)}")
    if "work_arrangement" in updates and updates["work_arrangement"] not in VALID_ARRANGEMENTS:
        raise ValueError(f"Invalid work_arrangement: {updates['work_arrangement']!r}. Must be one of {sorted(VALID_ARRANGEMENTS)}")

    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        if not updates:
            return enrich_app(app)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(f"UPDATE applications SET {set_clause} WHERE id = ?", list(updates.values()) + [app["id"]])

        if "status" in updates and updates["status"] != app["status"]:
            db.execute(
                """INSERT INTO application_events
                   (application_id, event_type, from_status, to_status)
                   VALUES (?, 'status_change', ?, ?)""",
                (app["id"], app["status"], updates["status"]),
            )
            if updates["status"] == "applied":
                existing = db.execute(
                    "SELECT 1 FROM application_dates WHERE application_id = ? AND date_type = 'applied'",
                    (app["id"],),
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO application_dates (application_id, date_type, date) VALUES (?, 'applied', ?)",
                        (app["id"], _date.today().isoformat()),
                    )

        updated = db.execute("SELECT * FROM applications WHERE id = ?", (app["id"],)).fetchone()
        return enrich_app(row_to_dict(updated))


@mcp.tool()
def render_cv(app_uuid: str) -> dict:
    """
    Render the application's current cv.md to cv.pdf via Typst.

    Call this after accept_report or regenerate_section changes cv.md — those
    don't auto-render (same as the web UI, which requires an explicit render
    step). get_application_bundle's `ready` flag depends on a current PDF
    existing, so run this before assembling a submission bundle.
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        out_dir = Path(app["output_dir"]) if app.get("output_dir") else None
        if out_dir is None:
            raise ValueError("Application has no output directory")

        cv_path = out_dir / "cv.md"
        if not cv_path.exists() and app.get("cv_markdown"):
            out_dir.mkdir(parents=True, exist_ok=True)
            cv_path.write_text(app["cv_markdown"], encoding="utf-8")
        if not cv_path.exists():
            raise ValueError("cv.md not found — generate or accept a CV first")

        from pipeline.render import render_cv as _render_cv
        try:
            _render_cv(cv_path, out_dir, company=app.get("company") or "")
        except Exception as e:
            raise ValueError(f"Render failed: {e}")

        db.execute("UPDATE applications SET has_pdf = 1 WHERE id = ?", (app["id"],))
        db.execute(
            "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'pdf_rendered', 'PDF rendered successfully')",
            (app["id"],),
        )
        return {"status": "rendered", "pdf_path": str(out_dir / "cv.pdf")}


@mcp.tool()
def update_cv_markdown(app_uuid: str, content: str) -> dict:
    """Overwrite the composed CV markdown for an application (both DB and cv.md on disk)."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        db.execute("UPDATE applications SET cv_markdown = ? WHERE id = ?", (content, app["id"]))
        db.execute(
            """INSERT INTO application_events (application_id, event_type, detail)
               VALUES (?, 'cv_edited', 'CV Markdown updated via MCP')""",
            (app["id"],),
        )
        if app.get("output_dir"):
            cv_path = Path(app["output_dir"]) / "cv.md"
            if cv_path.parent.exists():
                cv_path.write_text(content, encoding="utf-8")
    return {"status": "saved"}
