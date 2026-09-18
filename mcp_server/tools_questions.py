"""
tools_questions.py — application question/answer MCP tools.

Mirrors api/routes/questions.py. Answer feedback is distilled into a reusable
prompt signal via pipeline.feedback_processor — synchronously here (no
BackgroundTasks concept over MCP; the call is a single cheap Haiku round trip).
"""

from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import get_app_by_uuid, get_connection, row_to_dict

VALID_LENGTHS = {"short", "paragraph"}
VALID_RATINGS = {"positive", "negative"}


def _get_question_or_raise(q_id: str, app_id: str, db) -> dict:
    row = db.execute(
        "SELECT * FROM application_questions WHERE id = ? AND application_id = ?", (q_id, app_id)
    ).fetchone()
    if not row:
        raise ValueError(f"No question found with id={q_id!r} for this application")
    return row_to_dict(row)


def _latest_answer(q_id: str, db) -> Optional[dict]:
    row = db.execute(
        "SELECT * FROM application_answers WHERE question_id = ? ORDER BY created_at DESC LIMIT 1", (q_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def _effective_answer(answer: Optional[dict]) -> Optional[str]:
    if not answer:
        return None
    return answer.get("user_answer") or answer.get("ai_answer")


@mcp.tool()
def list_questions(app_uuid: str) -> list[dict]:
    """List application questions in order, each with its latest answer (ai/user/effective)."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        rows = db.execute(
            "SELECT * FROM application_questions WHERE application_id = ? ORDER BY sort_order ASC, created_at ASC",
            (app["id"],),
        ).fetchall()
        questions = [row_to_dict(r) for r in rows]
        for q in questions:
            answer = _latest_answer(q["id"], db)
            q["answer"] = answer
            q["effective_answer"] = _effective_answer(answer)
        return questions


@mcp.tool()
def add_question(
    app_uuid: str,
    question_text: str,
    response_length: str = "short",
    needs_research: bool = False,
    sort_order: int = 0,
) -> dict:
    """Add an application question. response_length: short | paragraph."""
    import uuid as _uuid
    from datetime import datetime

    if response_length not in VALID_LENGTHS:
        raise ValueError(f"Invalid response_length: {response_length!r}. Must be one of {sorted(VALID_LENGTHS)}")
    if not question_text.strip():
        raise ValueError("question_text cannot be empty")

    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        q_id = str(_uuid.uuid4())
        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO application_questions
               (id, application_id, question_text, response_length, needs_research, sort_order, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (q_id, app["id"], question_text.strip(), response_length, int(needs_research), sort_order, now),
        )
        row = db.execute("SELECT * FROM application_questions WHERE id = ?", (q_id,)).fetchone()
        q = row_to_dict(row)
        q["answer"] = None
        q["effective_answer"] = None
        return q


@mcp.tool()
def delete_question(app_uuid: str, question_id: str) -> dict:
    """Remove an application question."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        _get_question_or_raise(question_id, app["id"], db)
        db.execute("DELETE FROM application_questions WHERE id = ?", (question_id,))
        return {"status": "deleted", "id": question_id}


@mcp.tool()
def generate_answers(
    app_uuid: str,
    force: bool = False,
    question_ids: Optional[list[str]] = None,
    draft: bool = False,
) -> dict:
    """
    Batch-generate answers for an application's questions using its JD, CV,
    notes, and qa_tone.

    ASYNC: returns immediately with a job_id; the answers arrive via get_job (and stay
    readable with list_questions). draft=True returns a draft for review instead.

    force=False (default): only generates for questions with no existing answer.
    force=True: regenerates all targeted questions unconditionally.
    question_ids: optionally restrict to a subset of question ids.
    """
    from pipeline.jobs import service
    return service.create(
        "generate_answers",
        app_uuid=app_uuid,
        params={"force": force, "question_ids": question_ids},
        draft=draft,
    )


@mcp.tool()
def update_answer(app_uuid: str, question_id: str, user_answer: str) -> dict:
    """Save a user-edited answer for a question. The effective answer becomes user_answer."""
    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        _get_question_or_raise(question_id, app["id"], db)
        answer = _latest_answer(question_id, db)
        if not answer:
            raise ValueError("No answer exists for this question yet — generate one first.")
        db.execute("UPDATE application_answers SET user_answer = ? WHERE id = ?", (user_answer, answer["id"]))
        return {
            "answer_id": answer["id"], "question_id": question_id,
            "user_answer": user_answer, "effective_answer": user_answer,
        }


@mcp.tool()
def submit_answer_feedback(app_uuid: str, question_id: str, rating: str, user_comment: Optional[str] = None) -> dict:
    """
    Record thumbs-up/down feedback on a question's current answer.

    rating: positive | negative.

    Feedback is immediately distilled into a short, reusable prompt signal
    (via a Haiku call) and stored on the feedback row — surface these later
    with get_prompt_signals() to bias future generation.
    """
    if rating not in VALID_RATINGS:
        raise ValueError(f"Invalid rating: {rating!r}. Must be one of {sorted(VALID_RATINGS)}")

    with get_connection() as db:
        app = get_app_by_uuid(app_uuid, db)
        _get_question_or_raise(question_id, app["id"], db)
        answer = _latest_answer(question_id, db)
        if not answer:
            raise ValueError("No answer exists for this question — generate one before submitting feedback.")

        from datetime import datetime
        now = datetime.now().isoformat()
        cursor = db.execute(
            "INSERT INTO answer_feedback (answer_id, rating, user_comment, processed, created_at) VALUES (?, ?, ?, 0, ?)",
            (answer["id"], rating, user_comment, now),
        )
        feedback_id = cursor.lastrowid

        from pipeline.feedback_processor import process_feedback
        process_feedback(feedback_id, db)

        row = db.execute("SELECT * FROM answer_feedback WHERE id = ?", (feedback_id,)).fetchone()
        return row_to_dict(row)
