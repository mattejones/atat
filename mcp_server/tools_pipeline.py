"""
tools_pipeline.py — CV section generation/judge/review MCP tools.

Mirrors api/routes/review.py (get_report/run_judges/accept_report/
regenerate_section) and api/routes/sections.py (list_sections/
get_section_chain). run_judges and regenerate_section run as background jobs
(pipeline/jobs/kinds/sections.py) and return a job_id. An agent uses these to discover a section's current
report_id, inspect judge flags on it, retry against those flags, and accept
the winning report so it becomes canonical in cv.md.

list_sections originally did a naive `SELECT * FROM sections`, which only
carries accepted_report_id — NULL until something's been accepted. That left
no way to find a report_id for a freshly generated, not-yet-reviewed
application at all, making the rest of this module unreachable right after
submit_job. Fixed to mirror the real route's `latest_report` nesting.
"""

from pathlib import Path
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import get_app_by_uuid, get_connection, row_to_dict, rows_to_list

from pipeline.sections import compose_cv_markdown, read_section_file


def _get_report_or_raise(db, report_id: str) -> dict:
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise ValueError(f"No report found with id={report_id!r}")
    return row_to_dict(row)


def _evaluations_with_flags(db, report_id: str) -> list[dict]:
    eval_rows = db.execute(
        "SELECT * FROM evaluations WHERE report_id = ? ORDER BY created_at ASC", (report_id,)
    ).fetchall()
    result = []
    for eval_row in eval_rows:
        evaluation = row_to_dict(eval_row)
        flag_rows = db.execute(
            "SELECT * FROM flags WHERE evaluation_id = ? ORDER BY start_pos ASC", (evaluation["id"],)
        ).fetchall()
        evaluation["flags"] = rows_to_list(flag_rows)
        result.append(evaluation)
    return result


def _load_section_content(db, app_id: str, out_dir: Path) -> dict:
    """Best current content per section: accepted report, else latest attempt."""
    section_rows = db.execute(
        """SELECT s.section_name, s.accepted_report_id, r.file_path as latest_file_path
           FROM sections s
           LEFT JOIN reports r ON r.section_id = s.id
           WHERE s.application_id = ?
           ORDER BY s.section_name, r.attempt DESC""",
        (app_id,),
    ).fetchall()

    seen, best_paths = set(), {}
    for row in section_rows:
        name = row["section_name"]
        if name in seen:
            continue
        seen.add(name)
        if row["accepted_report_id"]:
            accepted = db.execute(
                "SELECT file_path FROM reports WHERE id = ?", (row["accepted_report_id"],)
            ).fetchone()
            if accepted:
                best_paths[name] = accepted["file_path"]
                continue
        if row["latest_file_path"]:
            best_paths[name] = row["latest_file_path"]

    content = {}
    for name, path in best_paths.items():
        try:
            content[name] = read_section_file(Path(path))
        except FileNotFoundError:
            pass
    return content


def _latest_report_summary(db, section_id: str) -> Optional[dict]:
    row = db.execute(
        """SELECT id, attempt, status, escalated, escalation_reason, created_at, parent_report_id
           FROM reports WHERE section_id = ? ORDER BY attempt DESC LIMIT 1""",
        (section_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


@mcp.tool()
def list_sections(app_uuid: str) -> list[dict]:
    """
    List all CV sections for an application, each with `latest_report`
    (the most recent generation attempt for that section — its id, attempt
    number, status, and whether it was escalated).

    `latest_report.id` is the `report_id` to pass into get_report/run_judges/
    accept_report/regenerate_section. Use this, not `accepted_report_id` —
    accepted_report_id is NULL until something has actually been accepted,
    so right after submit_job (before any review has happened) it's the
    *only* way to find a section's report id.
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        rows = db.execute(
            "SELECT id, section_name, accepted_report_id, updated_at FROM sections WHERE application_id = ? ORDER BY section_name",
            (app["id"],),
        ).fetchall()
        result = []
        for row in rows:
            section = row_to_dict(row)
            section["latest_report"] = _latest_report_summary(db, section["id"])
            result.append(section)
        return result


@mcp.tool()
def get_section_chain(app_uuid: str, section_name: str) -> dict:
    """
    Return the full generation history for one section — every attempt
    (accepted, rejected, and pending), oldest first, with each report's id,
    status, and escalation info. Use this to see the whole retry chain
    rather than just the latest attempt (list_sections gives you that).

    section_name: profile | experience | skills | education | certifications
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        section_row = db.execute(
            "SELECT id, section_name, accepted_report_id FROM sections WHERE application_id = ? AND section_name = ?",
            (app["id"], section_name),
        ).fetchone()
        if not section_row:
            raise ValueError(f"Section {section_name!r} not found for this application")
        section = row_to_dict(section_row)
        reports = db.execute(
            """SELECT id, attempt, status, escalated, escalation_reason, created_at, parent_report_id
               FROM reports WHERE section_id = ? ORDER BY attempt ASC""",
            (section["id"],),
        ).fetchall()
        return {
            "section_name": section["section_name"],
            "accepted_report_id": section["accepted_report_id"],
            "reports": rows_to_list(reports),
        }


@mcp.tool()
def get_report(report_id: str) -> dict:
    """Get a section generation report, including its judge evaluations, flags, and current text."""
    with get_connection() as db:
        report = _get_report_or_raise(db, report_id)
        evaluations = _evaluations_with_flags(db, report_id)
        all_flags = [f for e in evaluations for f in e["flags"]]
        generated_text = None
        try:
            generated_text = read_section_file(Path(report["file_path"]))
        except FileNotFoundError:
            pass
        return {
            **report,
            "evaluations": evaluations,
            "all_flags": all_flags,
            "generated_text": generated_text,
            "total_flags": len(all_flags),
            "active_flags": sum(1 for f in all_flags if f["status"] == "active"),
        }


@mcp.tool()
def run_judges(report_id: str, draft: bool = False) -> dict:
    """
    Run the tiered judge pipeline (deterministic + cheap LLM accuracy check) against a
    section report.

    ASYNC: returns immediately with a job_id; pass/flag counts arrive via get_job, and
    the flags themselves via get_report. draft=True returns a draft for review instead.
    """
    from pipeline.jobs import service
    return service.create("run_judges", report_id=report_id, draft=draft)


@mcp.tool()
def accept_report(report_id: str) -> dict:
    """
    Accept a section report as canonical for its section.

    Marks other reports for that section as rejected, and recomposes cv.md
    from the accepted (or latest pending) content of every section.
    """
    with get_connection() as db:
        report = _get_report_or_raise(db, report_id)
        app_id, section_name, section_id = report["application_id"], report["section_name"], report["section_id"]

        db.execute(
            "UPDATE reports SET status = 'rejected' WHERE section_id = ? AND id != ? AND status != 'rejected'",
            (section_id, report_id),
        )
        db.execute("UPDATE reports SET status = 'accepted' WHERE id = ?", (report_id,))
        db.execute("UPDATE sections SET accepted_report_id = ? WHERE id = ?", (report_id, section_id))

        app_row = db.execute(
            "SELECT output_dir, cv_markdown FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if not app_row or not app_row["output_dir"]:
            raise ValueError("Application output directory not found")
        out_dir = Path(app_row["output_dir"])

        # Parse name/contact from the existing cv_markdown header (line 0 = "# Name",
        # line 1 = contact string) — same approach as the accept route.
        name, contact = "", ""
        existing_md = app_row["cv_markdown"] or ""
        if existing_md:
            md_lines = existing_md.splitlines()
            if md_lines:
                name = md_lines[0].lstrip("# ").strip()
            if len(md_lines) > 1:
                contact = md_lines[1].strip()

        section_content = _load_section_content(db, app_id, out_dir)
        composed = compose_cv_markdown(name, contact, section_content)
        (out_dir / "cv.md").write_text(composed, encoding="utf-8")
        db.execute("UPDATE applications SET cv_markdown = ? WHERE id = ?", (composed, app_id))

        return {"status": "accepted", "report_id": report_id, "section_name": section_name, "app_id": app_id}


@mcp.tool()
def regenerate_section(report_id: str, global_comment: Optional[str] = None, draft: bool = False) -> dict:
    """
    Retry a section report — rewrites it against its active judge flags plus an
    optional freeform comment, chains a new report row (attempt = MAX+1), and
    re-runs the judge pipeline against the new text.

    ASYNC: returns immediately with a job_id; the result (new_report_id, judge outcome)
    arrives via get_job. draft=True returns a draft whose prompt_preview shows the flags
    and comment the retry will be steered by, for review before it's sent.
    """
    from pipeline.jobs import service
    return service.create(
        "regenerate_section", report_id=report_id, params={"global_comment": global_comment}, draft=draft,
    )
