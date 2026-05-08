"""
review.py — Human-in-the-loop review surface for ATAT section judge pipeline.

Endpoints:
  GET    /review/{report_id}                   — Full report with evaluations and flags
  POST   /review/{report_id}/evaluate          — Run judge orchestrator against a report
  PATCH  /review/{report_id}/flags/{flag_id}   — Update flag status / add user comment
  POST   /review/{report_id}/accept            — Accept report; recompose cv.md
  POST   /review/{report_id}/retry             — Build retry payload; regenerate; chain

The retry flow:
  1. Load active (non-dismissed) flags from the current report
  2. Build constraint block from flags + user's global comment
  3. Load application context (jd_text, generation_notes, output_dir)
  4. Call regenerate_section() with RETRY_MODEL (no thinking budget)
  5. Write new section file
  6. Insert chained report row — attempt = MAX(attempt for section) + 1
  7. Run orchestrator against new report
  8. Return new report id and orchestrator result

The accept flow:
  1. Mark report as 'accepted', mark all others for this section as 'rejected'
  2. Update sections.accepted_report_id
  3. Load accepted (or latest pending) content for ALL sections of the application
  4. Parse name and contact string from existing cv_markdown header
  5. Recompose cv.md using compose_cv_markdown() and update applications.cv_markdown

Attempt numbering:
  New attempts are always MAX(attempt) + 1 for the section, not parent.attempt + 1.
  This prevents duplicate attempt numbers when retrying from the same parent report
  more than once.
"""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db, row_to_dict, rows_to_list
from pipeline.judges import orchestrator
from pipeline.retry import regenerate_section, build_constraint_block
from pipeline.sections import (
    compose_cv_markdown,
    write_section_file,
    read_section_file,
)

router = APIRouter(prefix="/review", tags=["review"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class FlagUpdate(BaseModel):
    status:           Optional[str] = None   # active | dismissed | actioned
    user_comment:     Optional[str] = None
    dismissal_reason: Optional[str] = None


class RetryRequest(BaseModel):
    global_comment: Optional[str] = None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_report_or_404(db: sqlite3.Connection, report_id: str) -> dict:
    row = db.execute(
        "SELECT * FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return row_to_dict(row)


def _get_evaluations_with_flags(db: sqlite3.Connection, report_id: str) -> list:
    """Return all evaluations for a report, each with their flags nested."""
    eval_rows = db.execute(
        "SELECT * FROM evaluations WHERE report_id = ? ORDER BY created_at ASC",
        (report_id,),
    ).fetchall()

    result = []
    for eval_row in eval_rows:
        evaluation = row_to_dict(eval_row)
        flag_rows  = db.execute(
            "SELECT * FROM flags WHERE evaluation_id = ? ORDER BY start_pos ASC",
            (evaluation["id"],),
        ).fetchall()
        evaluation["flags"] = rows_to_list(flag_rows)
        result.append(evaluation)

    return result


def _load_section_content_for_application(
    db:      sqlite3.Connection,
    app_id:  str,
    out_dir: Path,
) -> dict:
    """
    Load the current best content for each section of an application.

    Priority per section:
      1. Accepted report file (if accepted_report_id is set)
      2. Latest pending report file by attempt number (fallback)

    Returns dict[section_name -> content].
    """
    section_rows = db.execute(
        """SELECT s.section_name, s.accepted_report_id,
                  r.file_path as latest_file_path
           FROM sections s
           LEFT JOIN reports r ON r.section_id = s.id
           WHERE s.application_id = ?
           ORDER BY s.section_name, r.attempt DESC""",
        (app_id,),
    ).fetchall()

    seen:       set  = set()
    best_paths: dict = {}

    for row in section_rows:
        name = row["section_name"]
        if name in seen:
            continue
        seen.add(name)

        if row["accepted_report_id"]:
            accepted = db.execute(
                "SELECT file_path FROM reports WHERE id = ?",
                (row["accepted_report_id"],),
            ).fetchone()
            if accepted:
                best_paths[name] = accepted["file_path"]
                continue

        if row["latest_file_path"]:
            best_paths[name] = row["latest_file_path"]

    content: dict = {}
    for section_name, file_path in best_paths.items():
        try:
            content[section_name] = read_section_file(Path(file_path))
        except FileNotFoundError:
            pass

    return content


def _next_attempt_for_section(db: sqlite3.Connection, section_id: str) -> int:
    """
    Return the next attempt number for a section — MAX(attempt) + 1.

    Using MAX rather than parent.attempt + 1 prevents duplicate attempt numbers
    when retrying from the same parent report more than once.
    """
    row = db.execute(
        "SELECT MAX(attempt) FROM reports WHERE section_id = ?",
        (section_id,),
    ).fetchone()
    return (row[0] or 0) + 1


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{report_id}")
def get_report(
    report_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Return full report with nested evaluations and flags."""
    report      = _get_report_or_404(db, report_id)
    evaluations = _get_evaluations_with_flags(db, report_id)

    all_flags = [
        flag
        for evaluation in evaluations
        for flag in evaluation["flags"]
    ]

    generated_text: Optional[str] = None
    try:
        generated_text = read_section_file(Path(report["file_path"]))
    except FileNotFoundError:
        pass

    return {
        **report,
        "evaluations":    evaluations,
        "all_flags":      all_flags,
        "generated_text": generated_text,
        "total_flags":    len(all_flags),
        "active_flags":   sum(1 for f in all_flags if f["status"] == "active"),
    }


@router.post("/{report_id}/evaluate")
def evaluate_report(
    report_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """Run the judge orchestrator against a report."""
    report = _get_report_or_404(db, report_id)

    try:
        section_text = read_section_file(Path(report["file_path"]))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Section file not found")

    result = orchestrator.run(
        report_id=report_id,
        section_text=section_text,
        attempt=report["attempt"],
        db=db,
    )

    return {
        "report_id":          report_id,
        "passed":             result.passed,
        "escalated":          result.escalated,
        "escalation_reason":  result.escalation_reason,
        "total_flags":        result.total_flags,
        "tier1_passed":       result.tier1_passed,
        "tier2_passed":       result.tier2_passed,
        "has_accuracy_flags": result.has_accuracy_flags,
    }


@router.patch("/{report_id}/flags/{flag_id}")
def update_flag(
    report_id: str,
    flag_id:   str,
    body:      FlagUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    """Update a flag's status and/or user comment."""
    _get_report_or_404(db, report_id)

    flag_row = db.execute(
        """SELECT f.* FROM flags f
           JOIN evaluations e ON f.evaluation_id = e.id
           WHERE f.id = ? AND e.report_id = ?""",
        (flag_id, report_id),
    ).fetchone()

    if not flag_row:
        raise HTTPException(status_code=404, detail="Flag not found for this report")

    valid_statuses = {"active", "dismissed", "actioned"}
    if body.status and body.status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid flag status '{body.status}'. Must be one of {valid_statuses}",
        )

    updates: dict = {}
    if body.status is not None:
        updates["status"] = body.status
    if body.user_comment is not None:
        updates["user_comment"] = body.user_comment
    if body.dismissal_reason is not None:
        updates["dismissal_reason"] = body.dismissal_reason

    if not updates:
        return row_to_dict(flag_row)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(
        f"UPDATE flags SET {set_clause} WHERE id = ?",
        list(updates.values()) + [flag_id],
    )

    updated = db.execute("SELECT * FROM flags WHERE id = ?", (flag_id,)).fetchone()
    return row_to_dict(updated)


@router.post("/{report_id}/accept")
def accept_report(
    report_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Accept a report as the canonical version for its section.

    Recomposes cv.md by parsing name and contact from the existing cv_markdown
    header rather than querying non-existent columns on the applications table.
    compose_cv_markdown() accepts a raw contact string for this purpose.
    """
    report = _get_report_or_404(db, report_id)
    app_id       = report["application_id"]
    section_name = report["section_name"]
    section_id   = report["section_id"]

    # Mark all other reports for this section as rejected
    db.execute(
        """UPDATE reports
           SET status = 'rejected'
           WHERE section_id = ? AND id != ? AND status != 'rejected'""",
        (section_id, report_id),
    )
    db.execute(
        "UPDATE reports SET status = 'accepted' WHERE id = ?",
        (report_id,),
    )
    db.execute(
        "UPDATE sections SET accepted_report_id = ? WHERE id = ?",
        (report_id, section_id),
    )

    # Load application — only columns that actually exist
    app_row = db.execute(
        "SELECT output_dir, cv_markdown FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()

    if not app_row or not app_row["output_dir"]:
        raise HTTPException(status_code=404, detail="Application output directory not found")

    out_dir = Path(app_row["output_dir"])

    # Parse name and contact from the existing cv_markdown header.
    # Line 0: "# Full Name"  →  strip leading "# "
    # Line 1: "email · phone · location · linkedin"  →  pass raw as contact string
    # compose_cv_markdown() accepts str for contact and passes it through as-is.
    name         = ""
    contact: str = ""
    existing_md  = app_row["cv_markdown"] or ""
    if existing_md:
        md_lines = existing_md.splitlines()
        if md_lines:
            name = md_lines[0].lstrip("# ").strip()
        if len(md_lines) > 1:
            contact = md_lines[1].strip()

    section_content = _load_section_content_for_application(db, app_id, out_dir)
    composed        = compose_cv_markdown(name, contact, section_content)

    (out_dir / "cv.md").write_text(composed, encoding="utf-8")
    db.execute(
        "UPDATE applications SET cv_markdown = ? WHERE id = ?",
        (composed, app_id),
    )

    return {
        "status":       "accepted",
        "report_id":    report_id,
        "section_name": section_name,
        "app_id":       app_id,
    }


@router.post("/{report_id}/retry")
def retry_report(
    report_id: str,
    body:      RetryRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Trigger a retry for a failed report.

    Attempt number is derived from MAX(attempt) for the section + 1, not
    from the parent report's attempt number. This prevents duplicates when
    the same parent report is retried more than once.
    """
    report = _get_report_or_404(db, report_id)
    app_id       = report["application_id"]
    section_name = report["section_name"]
    section_id   = report["section_id"]

    app_row = db.execute(
        "SELECT jd_text, generation_notes, output_dir FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()

    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    jd_text          = app_row["jd_text"] or ""
    generation_notes = app_row["generation_notes"]
    out_dir          = Path(app_row["output_dir"])

    # Load active flags for this report
    active_flag_rows = db.execute(
        """SELECT f.type, f.excerpt, f.message, f.start_pos, f.end_pos
           FROM flags f
           JOIN evaluations e ON f.evaluation_id = e.id
           WHERE e.report_id = ? AND f.status = 'active'
           ORDER BY f.start_pos ASC""",
        (report_id,),
    ).fetchall()

    active_flags = rows_to_list(active_flag_rows)

    try:
        previous_text = read_section_file(Path(report["file_path"]))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Section file not found")

    formatted_prompt = build_constraint_block(active_flags, body.global_comment)

    try:
        new_text, _, _ = regenerate_section(
            section_name=section_name,
            previous_text=previous_text,
            jd_text=jd_text,
            active_flags=active_flags,
            global_comment=body.global_comment,
            generation_notes=generation_notes,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    new_report_id = str(uuid.uuid4())
    new_file_path = write_section_file(out_dir, section_name, new_report_id, new_text)

    # ── Attempt number: MAX for section + 1, not parent + 1 ──────────────────
    # This prevents duplicate attempt numbers when the same parent is retried twice.
    now         = datetime.now().isoformat()
    new_attempt = _next_attempt_for_section(db, section_id)

    db.execute(
        """INSERT INTO reports
           (id, application_id, section_id, parent_report_id,
            section_name, attempt, file_path, status,
            global_comment, formatted_prompt, escalated, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?)""",
        (
            new_report_id, app_id, section_id, report_id,
            section_name, new_attempt, str(new_file_path),
            body.global_comment, formatted_prompt, now,
        ),
    )

    # Mark the retried report as rejected — superseded by the new one
    db.execute(
        "UPDATE reports SET status = 'rejected' WHERE id = ?",
        (report_id,),
    )

    orch_result = orchestrator.run(
        report_id=new_report_id,
        section_text=new_text,
        attempt=new_attempt,
        db=db,
    )

    return {
        "new_report_id":      new_report_id,
        "attempt":            new_attempt,
        "passed":             orch_result.passed,
        "escalated":          orch_result.escalated,
        "escalation_reason":  orch_result.escalation_reason,
        "total_flags":        orch_result.total_flags,
        "has_accuracy_flags": orch_result.has_accuracy_flags,
        "section_name":       section_name,
        "app_id":             app_id,
    }
