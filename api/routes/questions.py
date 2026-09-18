"""
questions.py — Routes for the Application Questions Handler.

Manages questions, answers, and feedback for a given application.
Questions and answers are stored per-application; tone is read from
the application's qa_tone field.

Endpoints:
  GET    /questions/{app_uuid}                   — list questions with latest answers
  POST   /questions/{app_uuid}                   — add a question
  PATCH  /questions/{app_uuid}/{q_id}            — update question (text/length/research/order)
  DELETE /questions/{app_uuid}/{q_id}            — remove a question
  POST   /questions/{app_uuid}/generate          — queue batch answer generation (job, 202)
  POST   /questions/{app_uuid}/{q_id}/regenerate — queue a single-answer regeneration (job, 202)
  PATCH  /questions/{app_uuid}/{q_id}/answer     — save user-edited answer text
  POST   /questions/{app_uuid}/{q_id}/feedback   — submit feedback (async processing)

Generation runs as a background job (pipeline/jobs/kinds/documents.py); poll
GET /jobs/{job_id} for the result.
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

from api.routes.jobs import call
from db.database import get_db, row_to_dict
from pipeline.jobs import service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/questions", tags=["questions"])

VALID_LENGTHS   = {"short", "paragraph"}
VALID_RATINGS   = {"positive", "negative"}
MAX_QUESTIONS   = 20


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
    force:        bool                = False
    question_ids: Optional[list[str]] = None


class FeedbackCreate(BaseModel):
    rating:       str
    user_comment: Optional[str] = None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_app_by_uuid(app_uuid: str, db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT * FROM applications WHERE uuid = ?", (app_uuid,)
    ).fetchone()
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
    row = db.execute(
        """SELECT * FROM application_answers
           WHERE question_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (q_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def _effective_answer(answer: Optional[dict]) -> Optional[str]:
    if not answer:
        return None
    return answer.get("user_answer") or answer.get("ai_answer")


def _enrich_questions(questions: list[dict], db: sqlite3.Connection) -> list[dict]:
    for q in questions:
        answer = _latest_answer_for_question(q["id"], db)
        q["answer"]           = answer
        q["effective_answer"] = _effective_answer(answer)
    return questions


# ── Background task ───────────────────────────────────────────────────────────

def _run_feedback_processor(feedback_id: int) -> None:
    try:
        from db.database import get_connection
        from pipeline.feedback_processor import process_feedback

        with get_connection() as db:
            process_feedback(feedback_id, db)
    except Exception:
        log.exception("Background feedback processing failed for id=%s", feedback_id)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{app_uuid}")
def list_questions(app_uuid: str, db: sqlite3.Connection = Depends(get_db)):
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]

    rows = db.execute(
        """SELECT * FROM application_questions
           WHERE application_id = ?
           ORDER BY sort_order ASC, created_at ASC""",
        (app_id,),
    ).fetchall()

    questions = [row_to_dict(r) for r in rows]
    return _enrich_questions(questions, db)


@router.post("/{app_uuid}")
def add_question(
    app_uuid: str,
    body:     QuestionCreate,
    db:       sqlite3.Connection = Depends(get_db),
):
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]

    if body.response_length not in VALID_LENGTHS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid response_length: {body.response_length!r}. Must be one of: {sorted(VALID_LENGTHS)}",
        )

    if not body.question_text.strip():
        raise HTTPException(status_code=422, detail="question_text cannot be empty")

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


@router.patch("/{app_uuid}/{q_id}")
def update_question(
    app_uuid: str,
    q_id:     str,
    body:     QuestionUpdate,
    db:       sqlite3.Connection = Depends(get_db),
):
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
    _get_question_or_404(q_id, app_id, db)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        row = db.execute(
            "SELECT * FROM application_questions WHERE id = ?", (q_id,)
        ).fetchone()
        return row_to_dict(row)

    if "response_length" in updates and updates["response_length"] not in VALID_LENGTHS:
        raise HTTPException(status_code=422, detail=f"Invalid response_length: {updates['response_length']!r}")
    if "question_text" in updates and not updates["question_text"].strip():
        raise HTTPException(status_code=422, detail="question_text cannot be empty")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(
        f"UPDATE application_questions SET {set_clause} WHERE id = ?",
        list(updates.values()) + [q_id],
    )

    row    = db.execute(
        "SELECT * FROM application_questions WHERE id = ?", (q_id,)
    ).fetchone()
    q      = row_to_dict(row)
    answer = _latest_answer_for_question(q_id, db)
    q["answer"]           = answer
    q["effective_answer"] = _effective_answer(answer)
    return q


@router.delete("/{app_uuid}/{q_id}")
def delete_question(
    app_uuid: str,
    q_id:     str,
    db:       sqlite3.Connection = Depends(get_db),
):
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
    _get_question_or_404(q_id, app_id, db)
    db.execute("DELETE FROM application_questions WHERE id = ?", (q_id,))
    return {"status": "deleted", "id": q_id}


@router.post("/{app_uuid}/generate", status_code=202)
def generate_answers(app_uuid: str, body: GenerateRequest):
    """
    Queue batch answer generation as a background job. Returns 202 with the job at once;
    poll GET /jobs/{job_id} — on success its result is {generated, skipped, answers}.
    """
    return call(
        service.create, "generate_answers", app_uuid=app_uuid,
        params={"force": body.force, "question_ids": body.question_ids},
        created_by="human",
    )


@router.post("/{app_uuid}/{q_id}/regenerate", status_code=202)
def regenerate_answer(app_uuid: str, q_id: str, db: sqlite3.Connection = Depends(get_db)):
    """
    Queue a fresh answer for one question (a generate_answers job, forced, for just this
    question). Returns 202 with the job; its result's answers[0] is the new answer.
    """
    app = _get_app_by_uuid(app_uuid, db)
    _get_question_or_404(q_id, app["id"], db)
    return call(
        service.create, "generate_answers", app_uuid=app_uuid,
        params={"force": True, "question_ids": [q_id]},
        created_by="human",
    )


@router.patch("/{app_uuid}/{q_id}/answer")
def update_answer(
    app_uuid: str,
    q_id:     str,
    body:     AnswerUpdate,
    db:       sqlite3.Connection = Depends(get_db),
):
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
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


@router.post("/{app_uuid}/{q_id}/feedback")
def submit_feedback(
    app_uuid:         str,
    q_id:             str,
    body:             FeedbackCreate,
    background_tasks: BackgroundTasks,
    db:               sqlite3.Connection = Depends(get_db),
):
    """Record thumbs-up/down feedback. Returns immediately; Haiku distillation runs as a background task."""
    app    = _get_app_by_uuid(app_uuid, db)
    app_id = app["id"]
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

    now    = datetime.now().isoformat()
    cursor = db.execute(
        """INSERT INTO answer_feedback
           (answer_id, rating, user_comment, processed, created_at)
           VALUES (?, ?, ?, 0, ?)""",
        (answer["id"], body.rating, body.user_comment, now),
    )
    feedback_id = cursor.lastrowid

    background_tasks.add_task(_run_feedback_processor, feedback_id)
    return {"status": "received", "feedback_id": feedback_id}
