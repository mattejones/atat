"""
CV kinds — generate a CV against a reviewed jd_spec, and judge it for coverage.

generate_cv   phase 2 of two-phase intake. Tailors against the spec, splits sections,
              runs tier 0 (roundtrip), composes cv.md, renders the PDF.
run_coverage  tier 3: does the CV answer the ad? Escalates, never blocks or retries.
"""

import json
import logging
import uuid
from pathlib import Path

from db.database import get_connection
from pipeline.jobs.base import JobContext, JobError, JobHandle, JobKind, Param, Prompt, register
from pipeline.jobs.kinds._common import insert_sections, load_spec, now_iso, run_meta

log = logging.getLogger(__name__)


@register
class GenerateCV(JobKind):
    name        = "generate_cv"
    target      = "application"
    description = "Generate the CV against the application's reviewed jd_spec."
    params      = {
        "generation_notes": Param(
            str,
            help="What the spec can't express: the reader, sensitivities, what to lead with. "
                 "Notes override the spec where they conflict.",
        ),
    }

    def check(self, ctx: JobContext, params: dict) -> None:
        app = ctx.application
        if not load_spec(app):
            raise JobError(
                f"Application {app['uuid']} has no jd_spec. Run analyse_job first — "
                "generating without a reviewed brief is the failure this pipeline exists "
                "to prevent."
            )
        if app.get("cv_markdown"):
            raise JobError(
                f"Application {app['uuid']} already has a CV. Use regenerate_section to "
                "revise it, rather than overwriting the whole document."
            )

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.tailorer import assemble_user_message, build_system_prompt
        app = ctx.application
        return Prompt(
            build_system_prompt(),
            assemble_user_message(app.get("jd_text") or "", params["generation_notes"], jd_spec=load_spec(app)),
        )

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.config import RENDER_PDF
        from pipeline.judges import roundtrip
        from pipeline.sections import compose_cv_markdown, split_cv_sections
        from pipeline.tailorer import assemble_user_message, build_system_prompt, call_llm

        app      = ctx.application
        spec     = load_spec(app)
        notes    = params["generation_notes"]
        app_id   = app["id"]
        app_uuid = app["uuid"]
        out_dir  = Path(app["output_dir"])

        try:
            cv_data = call_llm(
                build_system_prompt(),
                assemble_user_message(app.get("jd_text") or "", notes, jd_spec=spec),
            )
        except Exception as e:
            raise JobError(f"LLM generation failed: {e}") from e

        reasoning       = cv_data.pop("reasoning", "")
        section_content = split_cv_sections(cv_data)
        cv_markdown     = compose_cv_markdown(cv_data.get("name", ""), cv_data.get("contact", {}), section_content)
        rt              = roundtrip.run(cv_markdown)

        handle.checkpoint()
        with get_connection() as db:
            # The queue can hold a job for a while; don't clobber a CV that appeared meanwhile.
            if db.execute("SELECT cv_markdown FROM applications WHERE id = ?", (app_id,)).fetchone()["cv_markdown"]:
                raise JobError(f"Application {app_uuid} gained a CV while this job was running — not overwriting it.")

            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
            if reasoning:
                (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")
            (out_dir / "run_meta.json").write_text(run_meta(
                has_reasoning=bool(reasoning),
                jd_spec_tier=spec.get("computed_tier"),
                roundtrip_passed=rt.passed,
            ), encoding="utf-8")

            now = now_iso()
            db.execute("DELETE FROM roundtrip_failures WHERE application_id = ?", (app_id,))
            for f in rt.failures:
                db.execute(
                    """INSERT INTO roundtrip_failures (id, application_id, field, expected, actual, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), app_id, f.field, f.expected, f.actual, now),
                )
            db.execute(
                """UPDATE applications
                   SET cv_markdown = ?, reasoning = ?, generation_notes = ?,
                       status = 'generated', updated_at = ?
                   WHERE id = ?""",
                (cv_markdown, reasoning or None, notes, now, app_id),
            )
            db.execute(
                """INSERT INTO application_events (application_id, event_type, from_status, to_status, detail)
                   VALUES (?, 'status_change', 'analysed', 'generated', ?)""",
                (app_id, f"CV generated against reviewed jd_spec (tier {spec.get('computed_tier')})"),
            )
            insert_sections(db, app_id, out_dir, section_content, now)

        # Render outside the write transaction — typst takes a moment, and a failed render
        # is non-fatal (render_cv can re-run it).
        if RENDER_PDF and rt.passed:
            try:
                from pipeline.render import render_cv
                render_cv(out_dir / "cv.md", out_dir, company=app.get("company") or "")
                with get_connection() as db:
                    db.execute("UPDATE applications SET has_pdf = 1 WHERE id = ?", (app_id,))
            except Exception:
                log.exception("PDF render failed for %s (non-fatal, re-renderable)", app_id)

        return {
            "uuid":               app_uuid,
            "app_id":             app_id,
            "status":             "generated",
            "cv_markdown":        cv_markdown,
            "has_reasoning":      bool(reasoning),
            "roundtrip_passed":   rt.passed,
            "roundtrip_failures": [
                {"field": f.field, "expected": f.expected, "actual": f.actual} for f in rt.failures
            ],
            "next_step": "Run run_coverage() for tier 3, and run_judges() per section for tiers 1 and 2.",
        }


def render_coverage_md(result, spec: dict) -> str:
    """Render a coverage result as a skimmable markdown review document."""
    lines = [
        f"# Coverage review — {'PASS' if result.passed else 'ESCALATED'}",
        "",
        f"**{result.summary}**",
        "",
        "## Requirements",
        "",
    ]
    must = {r.get("id"): r.get("must_have") for r in spec.get("requirements", [])}
    mark = {"covered": "OK  ", "partial": "PART", "absent": "GAP "}

    for f in result.findings:
        if f.kind != "coverage":
            continue
        flag = "MUST" if must.get(f.ref_id) else "nice"
        lines.append(f"- `{mark.get(f.status, '?')}` **{f.ref_id}** [{flag}] {f.quote}")
        if f.excerpt:
            lines.append(f"    - CV: {f.excerpt}")
        if f.reason:
            lines.append(f"    - {f.reason}")
    lines.append("")

    violations = [f for f in result.findings if f.kind == "anti_pattern"]
    lines.append("## Anti-pattern violations")
    lines.append("")
    if violations:
        for f in violations:
            lines.append(f"- **{f.ref_id}** {f.quote}")
            if f.excerpt:
                lines.append(f"    - CV: {f.excerpt}")
            if f.reason:
                lines.append(f"    - {f.reason}")
    else:
        lines.append("_none_")
    lines.append("")
    return "\n".join(lines)


@register
class RunCoverage(JobKind):
    name        = "run_coverage"
    target      = "application"
    description = "Tier 3: judge the generated CV against its reviewed jd_spec."
    params      = {}

    def check(self, ctx: JobContext, params: dict) -> None:
        app = ctx.application
        if not load_spec(app):
            raise JobError(f"Application {app['uuid']} has no jd_spec — run analyse_job first.")
        if not app.get("cv_markdown"):
            raise JobError(f"Application {app['uuid']} has no CV yet — run generate_cv first.")

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.judges import coverage
        app = ctx.application
        return Prompt(*coverage.build_prompt(app["cv_markdown"], load_spec(app)))

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.judges import coverage

        app    = ctx.application
        spec   = load_spec(app)
        result = coverage.run(app["cv_markdown"], spec)

        handle.checkpoint()
        eval_id = str(uuid.uuid4())
        now     = now_iso()
        with get_connection() as db:
            db.execute(
                """INSERT INTO coverage_evaluations
                   (id, application_id, passed, covered_count, partial_count, absent_count,
                    violation_count, model, prompt_tokens, completion_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    eval_id, app["id"], int(result.passed), result.covered_count,
                    result.partial_count, result.absent_count, result.violation_count,
                    result.model, result.prompt_tokens, result.completion_tokens, now,
                ),
            )
            for f in result.findings:
                db.execute(
                    """INSERT INTO coverage_findings
                       (id, evaluation_id, kind, ref_id, quote, status, excerpt, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), eval_id, f.kind, f.ref_id, f.quote, f.status, f.excerpt, f.reason, now),
                )

        out_dir = Path(app["output_dir"]) if app.get("output_dir") else None
        if out_dir and out_dir.exists():
            (out_dir / "coverage.md").write_text(render_coverage_md(result, spec), encoding="utf-8")
            (out_dir / "coverage.json").write_text(json.dumps({
                "passed":   result.passed,
                "summary":  result.summary,
                "findings": [
                    {"kind": f.kind, "id": f.ref_id, "quote": f.quote,
                     "status": f.status, "excerpt": f.excerpt, "reason": f.reason}
                    for f in result.findings
                ],
            }, indent=2), encoding="utf-8")

        return {
            "uuid":     app["uuid"],
            "passed":   result.passed,
            "summary":  result.summary,
            "coverage": [
                {"id": f.ref_id, "status": f.status, "quote": f.quote, "excerpt": f.excerpt, "reason": f.reason}
                for f in result.findings if f.kind == "coverage"
            ],
            "violations": [
                {"id": f.ref_id, "quote": f.quote, "excerpt": f.excerpt, "reason": f.reason}
                for f in result.findings if f.kind == "anti_pattern"
            ],
        }
