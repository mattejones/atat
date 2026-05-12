"""
questions.py — Routes for the Application Questions Handler.

Manages questions, answers, and feedback for a given application.
Questions and answers are stored per-application; tone is read from
the application's qa_tone field.

Endpoints:
  GET    /questions/{app_id}                   — list questions with latest answers
  POST   /questions/{app_id}                   — add a question
  PATCH  /questions/{app_id}/{q_id}            — update question (text/length/research/order)
  DELETE /questions/{app_id}/{q_id}            — remove a question
  POST   /questions/{app_id}/generate          — batch-generate answers
  POST   /questions/{app_id}/{q_id}/regenerate — regenerate a single answer
  PATCH  /questions/{app_id}/{q_id}/answer     — save user-edited answer text
  POST   /questions/{app_id}/{q_id}/feedback   — submit feedback (async processing)

Generation:
  force=False (default) — only generates answers for questions that have none.
  force=True            — regenerates all questions unconditionally.
  question_ids          — optional list to target specific questions only.

Feedback:
  Saved synchronously; Haiku distillation runs as a BackgroundTask.
  The endpoint returns immediately with {"status": "received"}.
"""

import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db, row_to_dict, rows_to_list

log = logging.getLogger(__name__)

router = APIRouter(prefix="/questions", tags=["questions"])

VALID_LENGTHS   = {"short", "paragraph"}
VALID_RATINGS   = {"positive", "negative"}
MAX_QUESTIONS   = 20  # soft ceiling — enforced at creation time with a warning


# ── Pydantic models ───────────────────────────────────────────────────────────

class QuestionCreate(BaseModel):
    question_text:   str
    response_length: str = "short"
    needs_research:  int = 0
    sort_order:      int = 0


class QuestionUpdate(BaseModel):
    question_text:   Optional[str] = None
    response_length: Optional[str] = None
    needs_research:  Optional[int] = None
    sort_order:      Optional[int] = None


class AnswerUpdate(BaseModel):
    user_answer: str


class GenerateRequest(BaseModel):
    force:        bool            = False
    question_ids: Optional[list[str]] = None


class FeedbackCreate(BaseModel):
    rating:       str
    user_comment: Optional[str] = None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_application_or_404(app_id: str, db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row_to_dict(row)


def _get_question_or_404(q_id: str, app_id: str, db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT * FROM application_questions WHERE id = ? AND application_id = ?",
        (q_id, app_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")
    return row_to_dict(row)


def _latest_answer_for_question(q_id: str, db: sqlite3.Connection) -> Optional[dict]:
    """Return the most recently created answer for a question, or None."""
    row = db.execute(
        """SELECT * FROM application_answers
           WHERE question_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (q_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def _effective_answer(answer: Optional[dict]) -> Optional[str]:
    """Return the user-edited answer if present, otherwise the AI answer."""
    if not answer:
        return None
    return answer.get("user_answer") or answer.get("ai_answer")


def _enrich_questions(questions: list[dict], db: sqlite3.Connection) -> list[dict]:
    """Attach the latest answer to each question dict."""
    for q in questions:
        answer = _latest_answer_for_question(q["id"], db)
        q["answer"]           = answer
        q["effective_answer"] = _effective_answer(answer)
    return questions


# ── Background task ───────────────────────────────────────────────────────────

def _run_feedback_processor(feedback_id: int) -> None:
    """
    Background task: distil feedback into a prompt signal via Haiku.
    Opens its own DB connection — safe to run after the request closes.
    """
    try:
        from db.database import get_connection
        from pipeline.feedback_processor import process_feedback

        with get_connection() as db:
            process_feedback(feedback_id, db)
    except Exception:
        log.exception("Background feedback processing failed for id=%s", feedback_id)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{app_id}")
def list_questions(app_id: str, db: sqlite3.Connection = Depends(get_db)):
    """Return all questions for an application, each with their latest answer."""
    _get_application_or_404(app_id, db)

    rows = db.execute(
        """SELECT * FROM application_questions
           WHERE application_id = ?
           ORDER BY sort_order ASC, created_at ASC""",
        (app_id,),
    ).fetchall()

    questions = [row_to_dict(r) for r in rows]
    return _enrich_questions(questions, db)


@router.post("/{app_id}")
def add_question(
    app_id: str,
    body:   QuestionCreate,
    db:     sqlite3.Connection = Depends(get_db),
):
    _get_application_or_404(app_id, db)

    if body.response_length not in VALID_LENGTHS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid response_length: {body.response_length!r}. Must be one of: {sorted(VALID_LENGTHS)}",
        )

    if not body.question_text.strip():
        raise HTTPException(status_code=422, detail="question_text cannot be empty")

    # Soft ceiling check
    count = db.execute(
        "SELECT COUNT(*) FROM application_questions WHERE application_id = ?",
        (app_id,),
    ).fetchone()[0]
    if count >= MAX_QUESTIONS:
        log.warning("Application %s has %d questions — approaching limit", app_id, count)

    q_id = str(uuid.uuid4())
    now  = datetime.now().isoformat()

    db.execute(
        """INSERT INTO application_questions
           (id, application_id, question_text, response_length, needs_research, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (q_id, app_id, body.question_text.strip(), body.response_length,
         body.needs_research, body.sort_order, now),
    )

    row = db.execute(
        "SELECT * FROM application_questions WHERE id = ?", (q_id,)
    ).fetchone()
    q = row_to_dict(row)
    q["answer"]           = None
    q["effective_answer"] = None
    return q


@router.patch("/{app_id}/{q_id}")
def update_question(
    app_id: str,
    q_id:   str,
    body:   QuestionUpdate,
    db:     sqlite3.Connection = Depends(get_db),
):
    _get_question_or_404(q_id, app_id, db)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        row = db.execute(
            "SELECT * FROM application_questions WHERE id = ?", (q_id,)
        ).fetchone()
        return row_to_dict(row)

    if "response_length" in updates and updates["response_length"] not in VALID_LENGTHS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid response_length: {updates['response_length']!r}",
        )

    if "question_text" in updates and not updates["question_text"].strip():
        raise HTTPException(status_code=422, detail="question_text cannot be empty")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(
        f"UPDATE application_questions SET {set_clause} WHERE id = ?",
        list(updates.values()) + [q_id],
    )

    row = db.execute(
        "SELECT * FROM application_questions WHERE id = ?", (q_id,)
    ).fetchone()
    q = row_to_dict(row)
    answer = _latest_answer_for_question(q_id, db)
    q["answer"]           = answer
    q["effective_answer"] = _effective_answer(answer)
    return q


@router.delete("/{app_id}/{q_id}")
def delete_question(
    app_id: str,
    q_id:   str,
    db:     sqlite3.Connection = Depends(get_db),
):
    _get_question_or_404(q_id, app_id, db)
    db.execute("DELETE FROM application_questions WHERE id = ?", (q_id,))
    return {"status": "deleted", "id": q_id}


@router.post("/{app_id}/generate")
def generate_answers(
    app_id: str,
    body:   GenerateRequest,
    db:     sqlite3.Connection = Depends(get_db),
):
    """
    Batch-generate answers for this application's questions.

    force=False: only generates for questions that don't already have an answer.
    force=True:  regenerates all selected questions unconditionally.
    question_ids: if provided, limits generation to those question IDs only.
    """
    app = _get_application_or_404(app_id, db)

    rows = db.execute(
        """SELECT * FROM application_questions
           WHERE application_id = ?
           ORDER BY sort_order ASC, created_at ASC""",
        (app_id,),
    ).fetchall()
    all_questions = [row_to_dict(r) for r in rows]

    if not all_questions:
        return {"generated": 0, "skipped": 0, "answers": []}

    # Filter to requested question_ids if provided
    if body.question_ids:
        id_set        = set(body.question_ids)
        all_questions = [q for q in all_questions if q["id"] in id_set]

    # Unless force=True, skip questions that already have an answer
    to_generate: list[dict] = []
    skipped = 0
    for q in all_questions:
        if not body.force and _latest_answer_for_question(q["id"], db) is not None:
            skipped += 1
        else:
            to_generate.append(q)

    if not to_generate:
        return {"generated": 0, "skipped": skipped, "answers": []}

    # Load generation context from application
    jd_text     = app.get("jd_text") or ""
    cv_markdown = app.get("cv_markdown") or ""
    notes       = app.get("notes")
    qa_tone     = app.get("qa_tone") or "professional"

    if not jd_text and not cv_markdown:
        raise HTTPException(
            status_code=422,
            detail="Application has no JD or CV content — cannot generate answers.",
        )

    from pipeline.question_answerer import generate_answers as _generate

    try:
        answers_map = _generate(
            jd_text=jd_text,
            cv_markdown=cv_markdown,
            notes=notes,
            qa_tone=qa_tone,
            questions=to_generate,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    now = datetime.now().isoformat()
    saved: list[dict] = []

    for q in to_generate:
        answer_text = answers_map.get(q["id"])
        if not answer_text:
            log.warning("No answer returned for question %s — skipping insert", q["id"])
            skipped += 1
            continue

        answer_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO application_answers
               (id, question_id, application_id, ai_answer, user_answer, model_used, created_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (answer_id, q["id"], app_id, answer_text, "claude-sonnet-4-6", now),
        )
        saved.append({
            "question_id":    q["id"],
            "answer_id":      answer_id,
            "ai_answer":      answer_text,
            "user_answer":    None,
            "effective_answer": answer_text,
        })

    return {
        "generated": len(saved),
        "skipped":   skipped,
        "answers":   saved,
    }


@router.post("/{app_id}/{q_id}/regenerate")
def regenerate_answer(
    app_id: str,
    q_id:   str,
    db:     sqlite3.Connection = Depends(get_db),
):
    """Regenerate the answer for a single question, ignoring any existing answer."""
    app = _get_application_or_404(app_id, db)
    q   = _get_question_or_404(q_id, app_id, db)

    jd_text     = app.get("jd_text") or ""
    cv_markdown = app.get("cv_markdown") or ""
    notes       = app.get("notes")
    qa_tone     = app.get("qa_tone") or "professional"

    from pipeline.question_answerer import generate_answers as _generate

    try:
        answers_map = _generate(
            jd_text=jd_text,
            cv_markdown=cv_markdown,
            notes=notes,
            qa_tone=qa_tone,
            questions=[q],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    answer_text = answers_map.get(q_id)
    if not answer_text:
        raise HTTPException(status_code=500, detail="Model did not return an answer for this question.")

    answer_id = str(uuid.uuid4())
    now       = datetime.now().isoformat()

    db.execute(
        """INSERT INTO application_answers
           (id, question_id, application_id, ai_answer, user_answer, model_used, created_at)
           VALUES (?, ?, ?, ?, NULL, ?, ?)""",
        (answer_id, q_id, app_id, answer_text, "claude-sonnet-4-6", now),
    )

    return {
        "question_id":      q_id,
        "answer_id":        answer_id,
        "ai_answer":        answer_text,
        "user_answer":      None,
        "effective_answer": answer_text,
    }


@router.patch("/{app_id}/{q_id}/answer")
def update_answer(
    app_id: str,
    q_id:   str,
    body:   AnswerUpdate,
    db:     sqlite3.Connection = Depends(get_db),
):
    """Save a user-edited answer. Updates the most recent answer row."""
    _get_question_or_404(q_id, app_id, db)

    answer = _latest_answer_for_question(q_id, db)
    if not answer:
        raise HTTPException(
            status_code=404,
            detail="No answer exists for this question yet — generate one first.",
        )

    db.execute(
        "UPDATE application_answers SET user_answer = ? WHERE id = ?",
        (body.user_answer, answer["id"]),
    )

    return {
        "answer_id":        answer["id"],
        "question_id":      q_id,
        "user_answer":      body.user_answer,
        "effective_answer": body.user_answer,
    }


@router.post("/{app_id}/{q_id}/feedback")
def submit_feedback(
    app_id:           str,
    q_id:             str,
    body:             FeedbackCreate,
    background_tasks: BackgroundTasks,
    db:               sqlite3.Connection = Depends(get_db),
):
    """
    Record thumbs-up/down feedback on the latest answer for this question.
    Returns immediately; Haiku distillation runs as a background task.
    """
    _get_question_or_404(q_id, app_id, db)

    if body.rating not in VALID_RATINGS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid rating: {body.rating!r}. Must be one of: {sorted(VALID_RATINGS)}",
        )

    answer = _latest_answer_for_question(q_id, db)
    if not answer:
        raise HTTPException(
            status_code=404,
            detail="No answer exists for this question — generate one before submitting feedback.",
        )

    now = datetime.now().isoformat()
    cursor = db.execute(
        """INSERT INTO answer_feedback
           (answer_id, rating, user_comment, processed, created_at)
           VALUES (?, ?, ?, 0, ?)""",
        (answer["id"], body.rating, body.user_comment, now),
    )
    feedback_id = cursor.lastrowid

    background_tasks.add_task(_run_feedback_processor, feedback_id)

    return {"status": "received", "feedback_id": feedback_id}
