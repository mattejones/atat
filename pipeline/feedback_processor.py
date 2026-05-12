"""
feedback_processor.py — Async Haiku pipeline for distilling answer feedback
into reusable prompt signals.

Called from a FastAPI BackgroundTask after the /feedback endpoint returns.
Reads a raw answer_feedback row, calls Haiku with the question/answer/rating
context, and writes the distilled signal back to the DB.

The distilled signal is a short, prompt-injectable instruction (1–2 sentences)
that captures what the user liked or disliked. These signals are surfaced in
the Prompts UI under the question_answering prompt, giving the user visibility
and control before they choose to incorporate them.

If this task is dropped (e.g. server restart mid-run), the feedback row will
have processed=0 and created_at older than a few minutes. A startup recovery
check should re-queue such rows.
"""

import logging
import sqlite3

from pipeline.config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_FEEDBACK_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You distil user feedback on job application answers into short, \
actionable prompt signals.

Given a question, the AI's answer, and the user's feedback (positive or negative \
with optional comment), produce a single concise instruction (1–2 sentences, max 30 words) \
that captures the insight.

The instruction should be written as a direct rule, e.g.:
- "Lead with a concrete example before stating the general principle."
- "Avoid generic phrases like 'passionate about' — they read as hollow."
- "Answers for this type of question should reference specific metrics or outcomes."

Return ONLY the instruction text. No preamble, no explanation, no quotation marks."""


def _call_haiku(question: str, answer: str, rating: str, comment: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    sentiment = "positive (the user liked this answer)" if rating == "positive" \
        else "negative (the user was not satisfied with this answer)"

    comment_line = f"\nUser comment: {comment.strip()}" if comment and comment.strip() else ""

    user_message = (
        f"Question: {question}\n\n"
        f"Answer: {answer}\n\n"
        f"Feedback: {sentiment}{comment_line}"
    )

    message = client.messages.create(
        model=_FEEDBACK_MODEL,
        max_tokens=128,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()


def process_feedback(feedback_id: int, db: sqlite3.Connection) -> None:
    """
    Distil a single feedback row into a prompt signal.

    Fetches the feedback, question, and answer from the DB, calls Haiku,
    and writes the result back. Sets processed=1 on success.

    Safe to call multiple times on the same feedback_id — idempotent
    (will just overwrite processed_signal if already set).
    """
    try:
        row = db.execute(
            """
            SELECT
                af.id,
                af.rating,
                af.user_comment,
                aq.question_text,
                COALESCE(aa.user_answer, aa.ai_answer) AS effective_answer
            FROM answer_feedback af
            JOIN application_answers  aa ON af.answer_id   = aa.id
            JOIN application_questions aq ON aa.question_id = aq.id
            WHERE af.id = ?
            """,
            (feedback_id,),
        ).fetchone()

        if not row:
            log.warning("Feedback row not found: id=%s", feedback_id)
            return

        signal = _call_haiku(
            question=row["question_text"],
            answer=row["effective_answer"] or "",
            rating=row["rating"],
            comment=row["user_comment"] or "",
        )

        if not signal:
            log.warning("Haiku returned empty signal for feedback id=%s", feedback_id)
            return

        db.execute(
            "UPDATE answer_feedback SET processed_signal = ?, processed = 1 WHERE id = ?",
            (signal, feedback_id),
        )
        log.info("Feedback processed: id=%s, signal=%r", feedback_id, signal[:60])

    except Exception:
        log.exception("Feedback processing failed for id=%s", feedback_id)
