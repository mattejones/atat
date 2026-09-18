"""
Supporting-document kinds — cover letter and application question answers.
"""

import uuid

from db.database import get_connection
from pipeline.jobs.base import JobContext, JobError, JobHandle, JobKind, Param, Prompt, register
from pipeline.jobs.kinds._common import now_iso, output_dir_for


@register
class GenerateCoverLetter(JobKind):
    name        = "generate_cover_letter"
    target      = "application"
    description = "Generate a cover letter (optional company/role web research, then generation). Overwrites any existing generated draft."
    params      = {
        "research_company": Param(bool, True, help="Web-research the company before writing."),
        "research_role":    Param(bool, True, help="Web-research the role before writing."),
        "draft_input":      Param(str, help="A rough draft from the applicant to work from."),
        "key_points":       Param(str, help="Points the letter should hit."),
    }

    def check(self, ctx: JobContext, params: dict) -> None:
        app = ctx.application
        if not (app.get("jd_text") or app.get("cv_markdown")):
            raise JobError("Application has no JD or CV content — cannot generate a cover letter.")

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.cover_letter_generator import build_generation_prompt
        app = ctx.application
        system, user = build_generation_prompt(
            company=app.get("company") or "Unknown",
            role=app.get("role") or "Unknown",
            jd_text=app.get("jd_text") or "",
            cv_markdown=app.get("cv_markdown") or "",
            reasoning=app.get("reasoning"),
            draft_input=params["draft_input"],
            key_points=params["key_points"],
        )
        notes = []
        if params["research_company"] or params["research_role"]:
            notes.append(
                "A RESEARCH BRIEF section is added before generation, from web research "
                "that runs when the job does. It can't be shown here."
            )
        return Prompt(system, user, notes=notes)

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.config import LLM_MODEL
        from pipeline.cover_letter_generator import generate_cover_letter

        app = ctx.application
        try:
            markdown, brief = generate_cover_letter(
                company=app.get("company") or "Unknown",
                role=app.get("role") or "Unknown",
                jd_text=app.get("jd_text") or "",
                cv_markdown=app.get("cv_markdown") or "",
                reasoning=app.get("reasoning"),
                research_company=params["research_company"],
                research_role=params["research_role"],
                draft_input=params["draft_input"],
                key_points=params["key_points"],
            )
        except RuntimeError as e:
            raise JobError(str(e)) from e

        handle.checkpoint()
        out_dir = output_dir_for(app)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cover_letter.md").write_text(markdown, encoding="utf-8")
        if brief and brief.combined:
            (out_dir / "cover_letter_research.md").write_text(brief.combined, encoding="utf-8")

        now = now_iso()
        with get_connection() as db:
            cl = db.execute("SELECT id FROM cover_letters WHERE application_id = ?", (app["id"],)).fetchone()
            if cl:
                cl_id = cl["id"]
            else:
                cl_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO cover_letters (id, application_id, status, created_at, updated_at) VALUES (?, ?, 'draft', ?, ?)",
                    (cl_id, app["id"], now, now),
                )
            db.execute(
                """UPDATE cover_letters
                   SET markdown = ?, status = 'generated', research_company = ?, research_role = ?,
                       draft_input = ?, key_points = ?, research_brief = ?, model = ?,
                       generated_at = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    markdown, int(params["research_company"]), int(params["research_role"]),
                    params["draft_input"], params["key_points"],
                    brief.combined if brief else None, LLM_MODEL, now, now, cl_id,
                ),
            )
            db.execute(
                "INSERT INTO application_events (application_id, event_type, detail) VALUES (?, 'cover_letter_generated', 'Cover letter generated')",
                (app["id"],),
            )
            result = dict(db.execute("SELECT * FROM cover_letters WHERE id = ?", (cl_id,)).fetchone())
        result["has_pdf"] = False
        return result


def _questions_to_generate(app: dict, force: bool, question_ids) -> tuple[list[dict], int]:
    with get_connection() as db:
        rows = db.execute(
            "SELECT * FROM application_questions WHERE application_id = ? ORDER BY sort_order ASC, created_at ASC",
            (app["id"],),
        ).fetchall()
        questions = [dict(r) for r in rows]
        if question_ids:
            wanted    = set(question_ids)
            questions = [q for q in questions if q["id"] in wanted]

        to_generate, skipped = [], 0
        for q in questions:
            has_answer = db.execute(
                "SELECT 1 FROM application_answers WHERE question_id = ? LIMIT 1", (q["id"],)
            ).fetchone()
            if has_answer and not force:
                skipped += 1
            else:
                to_generate.append(q)
    return to_generate, skipped


@register
class GenerateAnswers(JobKind):
    name        = "generate_answers"
    target      = "application"
    description = "Batch-generate answers for an application's questions from its JD, CV, notes and qa_tone."
    params      = {
        "force":        Param(bool, False, help="Regenerate questions that already have an answer."),
        "question_ids": Param(list, help="Restrict to these question ids."),
    }

    def check(self, ctx: JobContext, params: dict) -> None:
        app = ctx.application
        if not (app.get("jd_text") or app.get("cv_markdown")):
            raise JobError("Application has no JD or CV content — cannot generate answers.")

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.question_answerer import build_prompt
        app = ctx.application
        to_generate, skipped = _questions_to_generate(app, params["force"], params["question_ids"])
        system, user = build_prompt(
            app.get("jd_text") or "", app.get("cv_markdown") or "", app.get("notes"),
            app.get("qa_tone") or "professional", to_generate,
        )
        notes = [f"{len(to_generate)} question(s) will be answered, {skipped} skipped (already answered; set force=true to redo)."]
        return Prompt(system, user, notes=notes)

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.question_answerer import _QA_MODEL, generate_answers

        app = ctx.application
        to_generate, skipped = _questions_to_generate(app, params["force"], params["question_ids"])
        if not to_generate:
            return {"generated": 0, "skipped": skipped, "answers": []}

        try:
            answers_map = generate_answers(
                jd_text=app.get("jd_text") or "",
                cv_markdown=app.get("cv_markdown") or "",
                notes=app.get("notes"),
                qa_tone=app.get("qa_tone") or "professional",
                questions=to_generate,
            )
        except RuntimeError as e:
            raise JobError(str(e)) from e

        handle.checkpoint()
        now, saved = now_iso(), []
        with get_connection() as db:
            for q in to_generate:
                answer_text = answers_map.get(q["id"])
                if not answer_text:
                    skipped += 1
                    continue
                answer_id = str(uuid.uuid4())
                db.execute(
                    """INSERT INTO application_answers
                       (id, question_id, application_id, ai_answer, user_answer, model_used, created_at)
                       VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                    (answer_id, q["id"], app["id"], answer_text, _QA_MODEL, now),
                )
                saved.append({
                    "question_id": q["id"], "answer_id": answer_id, "ai_answer": answer_text,
                    "user_answer": None, "effective_answer": answer_text,
                })
        return {"generated": len(saved), "skipped": skipped, "answers": saved}
