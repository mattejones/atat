"""
tools_submission.py — assembling and recording the final application step.

No route equivalent — these are new. get_application_bundle hands an agent
everything it needs to drive a browser tool through an actual application
form (CV PDF, cover letter, contact info, Q&A). record_submission logs what
happened afterwards, using the existing applications/application_dates/
application_events tables — no schema change required.
"""

from datetime import date as _date
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


@mcp.tool()
def get_application_bundle(app_uuid: str) -> dict:
    """
    Return everything needed to actually submit this application: CV PDF path
    (render it first via the web UI or the accept-report flow if missing),
    cover letter markdown/PDF path, name/contact parsed from the CV,
    effective answers to any application questions, and role metadata
    (source_url, salary expectations, location/work arrangement).

    Intended to be handed to a browser-driving tool to fill out an ATS form.
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        out_dir = output_dir_for(app)
        name, contact = _extract_name_contact(app.get("cv_markdown"))

        cv_pdf = out_dir / "cv.pdf"
        cover_letter_pdf = out_dir / "cover_letter.pdf"

        cl_row = db.execute(
            "SELECT markdown FROM cover_letters WHERE application_id = ?", (app["id"],)
        ).fetchone()

        q_rows = db.execute(
            "SELECT id, question_text, response_length FROM application_questions WHERE application_id = ? ORDER BY sort_order ASC, created_at ASC",
            (app["id"],),
        ).fetchall()
        questions = []
        for q in q_rows:
            q = row_to_dict(q)
            answer_row = db.execute(
                "SELECT ai_answer, user_answer FROM application_answers WHERE question_id = ? ORDER BY created_at DESC LIMIT 1",
                (q["id"],),
            ).fetchone()
            effective = None
            if answer_row:
                effective = answer_row["user_answer"] or answer_row["ai_answer"]
            questions.append({
                "question_id": q["id"],
                "question_text": q["question_text"],
                "response_length": q["response_length"],
                "answer": effective,
            })

        return {
            "uuid": app_uuid,
            "company": app.get("company"),
            "role": app.get("role"),
            "source_url": app.get("source_url"),
            "name": name,
            "contact": contact,
            "cv_pdf_path": str(cv_pdf) if cv_pdf.exists() else None,
            "cv_markdown": app.get("cv_markdown"),
            "cover_letter_markdown": cl_row["markdown"] if cl_row else None,
            "cover_letter_pdf_path": str(cover_letter_pdf) if cover_letter_pdf.exists() else None,
            "location": app.get("location"),
            "work_arrangement": app.get("work_arrangement"),
            "salary_min": app.get("salary_min"),
            "salary_max": app.get("salary_max"),
            "salary_currency": app.get("salary_currency"),
            "questions": questions,
            "ready": bool(cv_pdf.exists()),
        }


@mcp.tool()
def record_submission(
    app_uuid: str,
    portal: Optional[str] = None,
    confirmation: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Record that an application was actually submitted (e.g. after a browser
    tool filled out and submitted an ATS form). Sets status to 'applied',
    backfills today's applied date if none is logged, and writes an audit
    event distinguishable from manually-logged applications.

    portal: e.g. "Greenhouse", "Workday", "company careers page".
    confirmation: confirmation number/reference shown after submitting, if any.
    """
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)

        detail_parts = []
        if portal:
            detail_parts.append(f"portal: {portal}")
        if confirmation:
            detail_parts.append(f"confirmation: {confirmation}")
        if notes:
            detail_parts.append(notes)
        detail = "Application submitted via agent" + (f" ({'; '.join(detail_parts)})" if detail_parts else "")

        if app["status"] != "applied":
            db.execute(
                "UPDATE applications SET status = 'applied' WHERE id = ?", (app["id"],)
            )
            db.execute(
                """INSERT INTO application_events (application_id, event_type, from_status, to_status, detail)
                   VALUES (?, 'status_change', ?, 'applied', ?)""",
                (app["id"], app["status"], detail),
            )
        else:
            db.execute(
                "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'submission_recorded', ?)",
                (app["id"], detail),
            )

        existing_date = db.execute(
            "SELECT 1 FROM application_dates WHERE application_id = ? AND date_type = 'applied'",
            (app["id"],),
        ).fetchone()
        if not existing_date:
            db.execute(
                "INSERT INTO application_dates (application_id, date_type, date, notes) VALUES (?, 'applied', ?, ?)",
                (app["id"], _date.today().isoformat(), f"via {portal}" if portal else None),
            )

        return {"status": "recorded", "app_uuid": app_uuid, "portal": portal, "confirmation": confirmation}
