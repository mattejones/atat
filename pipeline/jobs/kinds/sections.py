"""
Section kinds — per-section judging and retry, addressed by report id.

run_judges          tiers 1 (deterministic) and 2 (cheap LLM accuracy) on one report.
regenerate_section  rewrite a section against its active flags plus an optional comment,
                    chain a new report, and judge the new text.
"""

import uuid
from pathlib import Path

from db.database import get_connection
from pipeline.jobs.base import JobContext, JobError, JobHandle, JobKind, Param, Prompt, register
from pipeline.jobs.kinds._common import now_iso


def _section_text(report: dict) -> str:
    from pipeline.sections import read_section_file
    try:
        return read_section_file(Path(report["file_path"]))
    except FileNotFoundError:
        raise JobError("Section file not found on disk")


def _active_flags(report_id: str) -> list[dict]:
    with get_connection() as db:
        rows = db.execute(
            """SELECT f.type, f.excerpt, f.message, f.start_pos, f.end_pos
               FROM flags f JOIN evaluations e ON f.evaluation_id = e.id
               WHERE e.report_id = ? AND f.status = 'active' ORDER BY f.start_pos ASC""",
            (report_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _orchestrator_summary(result) -> dict:
    return {
        "passed":             result.passed,
        "escalated":          result.escalated,
        "escalation_reason":  result.escalation_reason,
        "total_flags":        result.total_flags,
        "tier1_passed":       result.tier1_passed,
        "tier2_passed":       result.tier2_passed,
        "has_accuracy_flags": result.has_accuracy_flags,
    }


@register
class RunJudges(JobKind):
    name        = "run_judges"
    target      = "report"
    description = "Run tier 1 (deterministic) and tier 2 (LLM accuracy) judges on a section report."
    params      = {}

    def check(self, ctx: JobContext, params: dict) -> None:
        _section_text(ctx.report)

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.judges import cheap_llm
        system, user = cheap_llm.build_prompt(_section_text(ctx.report))
        return Prompt(system, user, notes=["Tier 1 is deterministic and sends nothing to a model; this is the tier 2 prompt."])

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.judges import orchestrator

        report     = ctx.report
        evaluation = orchestrator.evaluate(_section_text(report), report["id"])

        handle.checkpoint()
        with get_connection() as db:
            result = orchestrator.persist(db, report["id"], report["attempt"], evaluation)
        return {"report_id": report["id"], **_orchestrator_summary(result)}


@register
class RegenerateSection(JobKind):
    name        = "regenerate_section"
    target      = "report"
    description = "Rewrite a section against its active judge flags plus an optional comment, then judge the new text."
    params      = {
        "global_comment": Param(str, help="Freeform instruction for this retry, on top of the active flags."),
    }

    def check(self, ctx: JobContext, params: dict) -> None:
        _section_text(ctx.report)

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.retry import build_retry_prompt
        report, app = ctx.report, ctx.application
        system, user = build_retry_prompt(
            section_name=report["section_name"],
            previous_text=_section_text(report),
            jd_text=app.get("jd_text") or "",
            active_flags=_active_flags(report["id"]),
            global_comment=params["global_comment"],
            generation_notes=app.get("generation_notes"),
        )
        return Prompt(system, user, notes=["The new text is judged (tiers 1 and 2) after it is written."])

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.judges import orchestrator
        from pipeline.retry import build_constraint_block
        from pipeline.retry import regenerate_section as regenerate_text
        from pipeline.sections import write_section_file

        report, app    = ctx.report, ctx.application
        report_id      = report["id"]
        section_name   = report["section_name"]
        global_comment = params["global_comment"]
        active_flags   = _active_flags(report_id)

        try:
            new_text, _, _ = regenerate_text(
                section_name=section_name,
                previous_text=_section_text(report),
                jd_text=app.get("jd_text") or "",
                active_flags=active_flags,
                global_comment=global_comment,
                generation_notes=app.get("generation_notes"),
            )
        except RuntimeError as e:
            raise JobError(str(e)) from e

        handle.checkpoint()
        new_report_id = str(uuid.uuid4())
        with get_connection() as db:
            row = db.execute("SELECT MAX(attempt) FROM reports WHERE section_id = ?", (report["section_id"],)).fetchone()
            new_attempt   = (row[0] or 0) + 1
            new_file_path = write_section_file(Path(app["output_dir"]), section_name, new_report_id, new_text)
            db.execute(
                """INSERT INTO reports
                   (id, application_id, section_id, parent_report_id, section_name, attempt,
                    file_path, status, global_comment, formatted_prompt, escalated, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?)""",
                (
                    new_report_id, app["id"], report["section_id"], report_id, section_name, new_attempt,
                    str(new_file_path), global_comment, build_constraint_block(active_flags, global_comment), now_iso(),
                ),
            )
            db.execute("UPDATE reports SET status = 'rejected' WHERE id = ?", (report_id,))

        # The new report is committed; judge it with no connection open, then persist.
        evaluation = orchestrator.evaluate(new_text, new_report_id)
        with get_connection() as db:
            result = orchestrator.persist(db, new_report_id, new_attempt, evaluation)

        summary = _orchestrator_summary(result)
        summary.pop("tier1_passed")
        summary.pop("tier2_passed")
        return {
            "new_report_id": new_report_id,
            "attempt":       new_attempt,
            **summary,
            "section_name":  section_name,
            "app_id":        app["id"],
        }
