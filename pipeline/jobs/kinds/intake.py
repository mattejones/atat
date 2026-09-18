"""
Intake kinds — the two that create an application.

analyse_job  extract a jd_spec and save the application at 'analysed'. No CV.
submit_job   legacy single-phase path: create the application and generate a CV with
             no spec. Kept for backward compatibility; analyse_job + generate_cv is the
             path the guide recommends.
"""

import json
import logging

from db.database import get_connection
from pipeline.jobs.base import JobContext, JobError, JobHandle, JobKind, Param, Prompt, register
from pipeline.jobs.kinds._common import insert_sections, new_application_slot, now_iso, run_meta

log = logging.getLogger(__name__)


_INTAKE_PARAMS = {
    "jd_text":    Param(str, required=True, help="The job ad text."),
    "company":    Param(str, "Unknown", help="Company name."),
    "role":       Param(str, "Unknown Role", help="Role title."),
    "source_url": Param(str, help="Where the ad was found."),
}


@register
class AnalyseJob(JobKind):
    name        = "analyse_job"
    target      = "none"
    description = "Extract a jd_spec from a job ad, map it to library evidence, and save the application at 'analysed'. No CV is generated."
    params      = _INTAKE_PARAMS

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.jd_spec import build_extraction_prompt
        system, user = build_extraction_prompt(params["jd_text"])
        return Prompt(system, user)

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.config import LLM_MODEL, LLM_PROVIDER
        from pipeline.jd_spec import extract_jd_spec, render_spec_for_review

        jd_text = params["jd_text"]
        spec    = extract_jd_spec(jd_text)
        review  = render_spec_for_review(spec)

        handle.checkpoint()
        with get_connection() as db:
            app_id, app_uuid, out_dir = new_application_slot(db, params["company"], params["role"])
            (out_dir / "jd.txt").write_text(jd_text, encoding="utf-8")
            (out_dir / "jd_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
            (out_dir / "jd_spec.md").write_text(review, encoding="utf-8")

            now = now_iso()
            db.execute(
                """INSERT INTO applications
                   (id, uuid, company, role, source_url, jd_text, tier, status, output_dir,
                    has_pdf, model, provider, jd_spec, jd_spec_updated_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'analysed', ?, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    app_id, app_uuid, params["company"], params["role"], params["source_url"], jd_text,
                    spec["computed_tier"], str(out_dir), LLM_MODEL, LLM_PROVIDER,
                    json.dumps(spec), now, now, now,
                ),
            )
            db.execute(
                """INSERT INTO application_events (application_id, event_type, to_status, detail)
                   VALUES (?, 'status_change', 'analysed', ?)""",
                (app_id, f"JD spec extracted, computed tier {spec['computed_tier']}"),
            )
        handle.attach_application(app_id)

        stats = spec.get("tier_stats", {})
        return {
            "uuid":            app_uuid,
            "app_id":          app_id,
            "status":          "analysed",
            "computed_tier":   spec["computed_tier"],
            "tier_stats":      stats,
            "requirements":    len(spec["requirements"]),
            "anti_patterns":   len(spec["anti_patterns"]),
            "gaps":            stats.get("gaps", []),
            "review_markdown": review,
            "next_step": (
                "Show review_markdown to the applicant. Correct it with update_jd_spec if "
                "needed. Only then call generate_cv."
            ),
        }


@register
class SubmitJob(JobKind):
    name        = "submit_job"
    target      = "none"
    description = "Legacy single-phase intake: create the application and generate a CV with no jd_spec."
    params      = {
        **_INTAKE_PARAMS,
        "tier":             Param(str, help="T1 | T2 | T3 | EX1"),
        "generation_notes": Param(str, help="Freeform guidance for this generation."),
    }

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.tailorer import assemble_user_message, build_system_prompt
        return Prompt(build_system_prompt(), assemble_user_message(params["jd_text"], params["generation_notes"]))

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.config import LLM_MODEL, LLM_PROVIDER, RENDER_PDF
        from pipeline.sections import compose_cv_markdown, split_cv_sections
        from pipeline.tailorer import assemble_user_message, build_system_prompt, call_llm

        jd_text = params["jd_text"]
        notes   = params["generation_notes"]
        try:
            cv_data = call_llm(build_system_prompt(), assemble_user_message(jd_text, notes))
        except Exception as e:
            raise JobError(f"LLM generation failed: {e}") from e

        reasoning       = cv_data.pop("reasoning", "")
        section_content = split_cv_sections(cv_data)
        cv_markdown     = compose_cv_markdown(cv_data.get("name", ""), cv_data.get("contact", {}), section_content)

        handle.checkpoint()
        with get_connection() as db:
            app_id, app_uuid, out_dir = new_application_slot(db, params["company"], params["role"])
            (out_dir / "jd.txt").write_text(jd_text, encoding="utf-8")
            (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
            if reasoning:
                (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")
            (out_dir / "run_meta.json").write_text(
                run_meta(jd_file="mcp", has_reasoning=bool(reasoning)), encoding="utf-8"
            )

            now = now_iso()
            db.execute(
                """INSERT INTO applications
                   (id, uuid, company, role, source_url, jd_text, cv_markdown,
                    tier, status, output_dir, has_pdf, model, provider,
                    generation_notes, reasoning, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    app_id, app_uuid, params["company"], params["role"], params["source_url"], jd_text,
                    cv_markdown, params["tier"], str(out_dir), LLM_MODEL, LLM_PROVIDER, notes,
                    reasoning or None, now, now,
                ),
            )
            db.execute(
                """INSERT INTO application_events (application_id, event_type, to_status, detail)
                   VALUES (?, 'status_change', 'generated', 'CV generated and split into sections via MCP')""",
                (app_id,),
            )
            insert_sections(db, app_id, out_dir, section_content, now)
        handle.attach_application(app_id)

        if RENDER_PDF:
            try:
                from pipeline.render import render_cv
                render_cv(out_dir / "cv.md", out_dir)
            except Exception:
                log.exception("PDF render failed for %s (non-fatal, re-renderable)", app_id)

        return {
            "uuid":          app_uuid,
            "app_id":        app_id,
            "cv_markdown":   cv_markdown,
            "has_reasoning": bool(reasoning),
            "status":        "generated",
        }
