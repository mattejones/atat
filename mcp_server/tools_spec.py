"""
tools_spec.py — two-phase intake: analyse the ad, review the spec, then generate.

The old flow was single-phase. submit_job() took a job ad plus freehand generation_notes
and produced a CV in one shot. The notes were the only thing steering a strategic decision
(what to lead with, what to suppress, what the reader will disqualify you for), they were
written by a model, nobody validated them, and no downstream judge ever checked whether
the resulting CV actually answered the ad. It was the one unstructured, unvalidated,
load-bearing artefact in an otherwise disciplined pipeline.

Two-phase replaces it:

    analyse_job(jd_text)          -> extracts a jd_spec, maps it to library evidence,
                                     computes a tier from must-have coverage,
                                     saves the application at status 'analysed'.
                                     NO CV IS GENERATED. NO TOKENS ARE SPENT TAILORING.

    [ human reads render_spec_for_review() output and decides ]

    update_jd_spec(...)           -> optional: correct the extraction
    generate_cv(app_uuid)         -> tailors against the reviewed spec, splits sections,
                                     runs tier 0, composes cv.md, renders PDF
    run_coverage(app_uuid)        -> tier 3: does the CV answer the ad?

The point of the split is that the expensive, irreversible step (generation) now happens
AFTER a human has seen the gaps, not before. On a batch of parked applications this also
gives you triage for free: the computed tier tells you where the fit genuinely is, so you
can spend your effort where it converts rather than spreading it evenly across everything.

submit_job() is left intact in tools_intake for backward compatibility.
"""

import json
import re
import uuid
from datetime import date, datetime
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import enrich_app, get_connection, row_to_dict
from pipeline.config import (
    ENABLE_CACHING, LLM_MODEL, LLM_PROVIDER, OUTPUT_PATH, RENDER_PDF,
    TEMPERATURE, THINKING_BUDGET,
)


def _slugify(text: str, max_len: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:max_len].strip("-")


def _load_spec(row) -> Optional[dict]:
    raw = row["jd_spec"] if "jd_spec" in row.keys() else None
    return json.loads(raw) if raw else None


def _get_app(db, app_uuid: str):
    row = db.execute("SELECT * FROM applications WHERE uuid = ?", (app_uuid,)).fetchone()
    if not row:
        raise ValueError(f"No application found with uuid {app_uuid!r}")
    return row


# ── Phase 1: analyse ──────────────────────────────────────────────────────────

@mcp.tool()
def analyse_job(
    jd_text: str,
    company: str = "Unknown",
    role: str = "Unknown Role",
    source_url: Optional[str] = None,
) -> dict:
    """
    Phase 1 of two. Extract a structured jd_spec from a job ad and map it against the
    experience library. Creates the application at status 'analysed'. Does NOT generate
    a CV.

    Returns the spec, a computed tier, and a markdown review document. STOP HERE and put
    the review document in front of the applicant before calling generate_cv. That is the
    entire point of the two-phase split: the human sees the gaps before a generation is
    spent, not after.

    The spec enforces two invariants, both validated on write:
      - every requirement/anti-pattern quote is VERBATIM from the ad (if it can't be
        quoted, it doesn't exist)
      - every evidence reference resolves to a real cv-library heading (a ref that
        doesn't resolve is a hallucination, and the extraction is rejected)

    A requirement with no evidence is a GAP. Gaps are not errors — they are the most
    useful thing this tool produces. They tell you where the fit genuinely isn't, and
    they drive the computed tier.

    Raises SpecValidationError if the extraction is untrustworthy. It is not repaired
    silently: a laundered spec looks authoritative and isn't.
    """
    from pipeline.jd_spec import extract_jd_spec, render_spec_for_review

    if not jd_text.strip():
        raise ValueError("jd_text cannot be empty")

    spec = extract_jd_spec(jd_text)

    with get_connection() as db:
        today        = date.today().isoformat()
        app_id       = f"{today}_{_slugify(company, 30)}_{_slugify(role, 40)}"
        app_uuid     = str(uuid.uuid4())

        out_dir = OUTPUT_PATH / app_id
        if out_dir.exists() or db.execute(
            "SELECT 1 FROM applications WHERE id = ?", (app_id,)
        ).fetchone():
            app_id  = f"{app_id}_{str(uuid.uuid4())[:6]}"
            out_dir = OUTPUT_PATH / app_id

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "jd.txt").write_text(jd_text, encoding="utf-8")
        (out_dir / "jd_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        (out_dir / "jd_spec.md").write_text(render_spec_for_review(spec), encoding="utf-8")

        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO applications
               (id, uuid, company, role, source_url, jd_text, tier, status, output_dir,
                has_pdf, model, provider, jd_spec, jd_spec_updated_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'analysed', ?, 0, ?, ?, ?, ?, ?, ?)""",
            (
                app_id, app_uuid, company, role, source_url, jd_text,
                spec["computed_tier"], str(out_dir), LLM_MODEL, LLM_PROVIDER,
                json.dumps(spec), now, now, now,
            ),
        )
        db.execute(
            """INSERT INTO application_events (application_id, event_type, to_status, detail)
               VALUES (?, 'status_change', 'analysed', ?)""",
            (app_id, f"JD spec extracted, computed tier {spec['computed_tier']}"),
        )

    stats = spec.get("tier_stats", {})
    return {
        "uuid":          app_uuid,
        "app_id":        app_id,
        "status":        "analysed",
        "computed_tier": spec["computed_tier"],
        "tier_stats":    stats,
        "requirements":  len(spec["requirements"]),
        "anti_patterns": len(spec["anti_patterns"]),
        "gaps":          stats.get("gaps", []),
        "review_markdown": render_spec_for_review(spec),
        "next_step": (
            "Show review_markdown to the applicant. Correct it with update_jd_spec if "
            "needed. Only then call generate_cv."
        ),
    }


# ── Spec read / edit ──────────────────────────────────────────────────────────

@mcp.tool()
def get_jd_spec(app_uuid: str) -> dict:
    """
    Return the jd_spec for an application, plus the markdown review document.
    Returns spec=None if the application predates the spec pipeline or was created
    through the legacy submit_job path.
    """
    from pipeline.jd_spec import render_spec_for_review

    with get_connection() as db:
        row  = _get_app(db, app_uuid)
        spec = _load_spec(row)

    if not spec:
        return {
            "uuid": app_uuid,
            "spec": None,
            "note": "No jd_spec on this application (legacy submit_job, or not yet analysed).",
        }

    return {
        "uuid":             app_uuid,
        "spec":             spec,
        "computed_tier":    spec.get("computed_tier"),
        "review_markdown":  render_spec_for_review(spec),
    }


@mcp.tool()
def update_jd_spec(app_uuid: str, spec: dict) -> dict:
    """
    Replace an application's jd_spec with a corrected version, then recompute the tier.

    Use this after the applicant reviews the extraction: to drop an invented requirement,
    downgrade an inflated must_have, add an anti-pattern the model missed, or correct an
    evidence mapping.

    The same two invariants are re-validated on write. You cannot save a spec whose quotes
    aren't in the ad or whose evidence doesn't resolve — including one a human edited.
    The check exists to keep the spec trustworthy as a judge rubric, and a human is just
    as capable as a model of breaking that.
    """
    from pipeline.jd_spec import (
        build_library_index, compute_tier, render_spec_for_review, validate_spec,
    )

    with get_connection() as db:
        row     = _get_app(db, app_uuid)
        jd_text = row["jd_text"] or ""

        errors = validate_spec(spec, jd_text, build_library_index())
        if errors:
            raise ValueError(
                "Edited spec failed validation:\n  - " + "\n  - ".join(errors)
            )

        tier, stats = compute_tier(spec)
        spec["computed_tier"] = tier
        spec["tier_stats"]    = stats
        spec["extracted_at"]  = datetime.now().isoformat()

        now = datetime.now().isoformat()
        db.execute(
            "UPDATE applications SET jd_spec = ?, jd_spec_updated_at = ?, tier = ? WHERE uuid = ?",
            (json.dumps(spec), now, tier, app_uuid),
        )
        db.execute(
            """INSERT INTO application_events (application_id, event_type, detail)
               VALUES (?, 'note_added', ?)""",
            (row["id"], f"jd_spec edited by applicant, tier recomputed to {tier}"),
        )

        out_dir = row["output_dir"]
        if out_dir:
            from pathlib import Path
            d = Path(out_dir)
            if d.exists():
                (d / "jd_spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
                (d / "jd_spec.md").write_text(render_spec_for_review(spec), encoding="utf-8")

    return {
        "uuid":            app_uuid,
        "computed_tier":   tier,
        "tier_stats":      stats,
        "review_markdown": render_spec_for_review(spec),
    }


# ── Phase 2: generate ─────────────────────────────────────────────────────────

@mcp.tool()
def generate_cv(app_uuid: str, generation_notes: Optional[str] = None) -> dict:
    """
    Phase 2 of two. Generate the CV against a reviewed jd_spec.

    Requires the application to be at status 'analysed' with a spec present. Refuses to
    run without one — generating against no brief is exactly the failure mode the spec
    exists to prevent, and silently falling back to it would defeat the purpose.

    generation_notes are still supported and still useful. They carry what a spec cannot
    express: the reader's background, standing sensitivities, a former colleague who knows
    exactly what you did and will spot a stretch. Notes override the spec where they
    conflict. Call get_recent_notes() first to see what guidance past applications used.

    Runs tier 0 (roundtrip) before rendering. If cv.md does not survive parse_cv intact,
    the PDF would be silently wrong, so failures are surfaced rather than swallowed.

    Follow with run_coverage() for tier 3, and list_sections/run_judges for tiers 1 and 2.
    """
    from pathlib import Path

    from pipeline.judges import roundtrip
    from pipeline.sections import (
        SECTION_ORDER, compose_cv_markdown, split_cv_sections, write_section_file,
    )
    from pipeline.tailorer import assemble_user_message, build_system_prompt, call_llm

    with get_connection() as db:
        row  = _get_app(db, app_uuid)
        spec = _load_spec(row)

        if not spec:
            raise ValueError(
                f"Application {app_uuid} has no jd_spec. Run analyse_job first — "
                "generating without a reviewed brief is the failure this pipeline exists "
                "to prevent."
            )
        if row["cv_markdown"]:
            raise ValueError(
                f"Application {app_uuid} already has a CV. Use regenerate_section to "
                "revise it, rather than overwriting the whole document."
            )

        app_id  = row["id"]
        jd_text = row["jd_text"] or ""
        out_dir = Path(row["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            system  = build_system_prompt()
            user    = assemble_user_message(jd_text, generation_notes, jd_spec=spec)
            cv_data = call_llm(system, user)
        except Exception as e:
            raise ValueError(f"LLM generation failed: {e}")

        reasoning = cv_data.pop("reasoning", "")
        name      = cv_data.get("name", "")
        contact   = cv_data.get("contact", {})

        section_content = split_cv_sections(cv_data)
        cv_markdown     = compose_cv_markdown(name, contact, section_content)

        (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
        if reasoning:
            (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")

        # ── Tier 0: roundtrip ─────────────────────────────────────────────────
        rt = roundtrip.run(cv_markdown)
        now = datetime.now().isoformat()

        db.execute("DELETE FROM roundtrip_failures WHERE application_id = ?", (app_id,))
        for f in rt.failures:
            db.execute(
                """INSERT INTO roundtrip_failures
                   (id, application_id, field, expected, actual, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), app_id, f.field, f.expected, f.actual, now),
            )

        (out_dir / "run_meta.json").write_text(json.dumps({
            "model": LLM_MODEL, "provider": LLM_PROVIDER, "temperature": TEMPERATURE,
            "thinking_budget": THINKING_BUDGET, "caching": ENABLE_CACHING,
            "render_pdf": RENDER_PDF, "generated_at": date.today().isoformat(),
            "status": "generated", "has_reasoning": bool(reasoning),
            "jd_spec_tier": spec.get("computed_tier"),
            "roundtrip_passed": rt.passed,
        }, indent=2), encoding="utf-8")

        db.execute(
            """UPDATE applications
               SET cv_markdown = ?, reasoning = ?, generation_notes = ?,
                   status = 'generated', updated_at = ?
               WHERE uuid = ?""",
            (cv_markdown, reasoning or None, generation_notes, now, app_uuid),
        )
        db.execute(
            """INSERT INTO application_events (application_id, event_type, from_status, to_status, detail)
               VALUES (?, 'status_change', 'analysed', 'generated', ?)""",
            (app_id, f"CV generated against reviewed jd_spec (tier {spec.get('computed_tier')})"),
        )

        for section_name in SECTION_ORDER:
            content = section_content.get(section_name, "")
            if not content:
                continue
            section_id = str(uuid.uuid4())
            report_id  = str(uuid.uuid4())
            file_path  = write_section_file(out_dir, section_name, report_id, content)
            db.execute(
                """INSERT INTO sections (id, application_id, section_name, accepted_report_id, created_at, updated_at)
                   VALUES (?, ?, ?, NULL, ?, ?)""",
                (section_id, app_id, section_name, now, now),
            )
            db.execute(
                """INSERT INTO reports
                   (id, application_id, section_id, parent_report_id, section_name, attempt,
                    file_path, status, global_comment, formatted_prompt, escalated, created_at)
                   VALUES (?, ?, ?, NULL, ?, 1, ?, 'pending', NULL, NULL, 0, ?)""",
                (report_id, app_id, section_id, section_name, str(file_path), now),
            )

        if RENDER_PDF and rt.passed:
            try:
                from pipeline.render import render_cv
                render_cv(out_dir / "cv.md", out_dir, company=row["company"] or "")
                db.execute("UPDATE applications SET has_pdf = 1 WHERE id = ?", (app_id,))
            except Exception:
                pass  # non-fatal — re-renderable

    return {
        "uuid":             app_uuid,
        "app_id":           app_id,
        "status":           "generated",
        "cv_markdown":      cv_markdown,
        "has_reasoning":    bool(reasoning),
        "roundtrip_passed": rt.passed,
        "roundtrip_failures": [
            {"field": f.field, "expected": f.expected, "actual": f.actual}
            for f in rt.failures
        ],
        "next_step": "Run run_coverage() for tier 3, and run_judges() per section for tiers 1 and 2.",
    }


# ── Tier 3 ────────────────────────────────────────────────────────────────────

def _render_coverage_md(result, spec: dict) -> str:
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


@mcp.tool()
def get_coverage(app_uuid: str) -> dict:
    """
    Read back the most recent tier 3 coverage result, plus any tier 0 roundtrip failures.

    This exists because generate_cv and run_coverage both make frontier/judge calls that
    routinely exceed the MCP client's response window. The work completes and commits
    server-side, but the return value is lost. Without a getter, a timeout silently
    destroys the result of a call that actually succeeded — and the caller cannot tell
    the difference between "judge found nothing" and "judge's answer never arrived".

    Always call this after a run_coverage or generate_cv timeout rather than re-running.
    Re-running spends the tokens again to recompute an answer that is already on disk.
    """
    with get_connection() as db:
        row = _get_app(db, app_uuid)

        ev = db.execute(
            """SELECT * FROM coverage_evaluations
               WHERE application_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()

        rt = db.execute(
            "SELECT field, expected, actual FROM roundtrip_failures WHERE application_id = ?",
            (row["id"],),
        ).fetchall()

        roundtrip = [
            {"field": r["field"], "expected": r["expected"], "actual": r["actual"]}
            for r in rt
        ]

        if not ev:
            return {
                "uuid": app_uuid,
                "coverage": None,
                "roundtrip_passed": not roundtrip,
                "roundtrip_failures": roundtrip,
                "note": "No coverage evaluation yet — run run_coverage.",
            }

        findings = db.execute(
            """SELECT kind, ref_id, quote, status, excerpt, reason
               FROM coverage_findings WHERE evaluation_id = ?
               ORDER BY kind DESC, ref_id""",
            (ev["id"],),
        ).fetchall()

    return {
        "uuid":    app_uuid,
        "passed":  bool(ev["passed"]),
        "summary": (
            f"{ev['covered_count']} covered, {ev['partial_count']} partial, "
            f"{ev['absent_count']} absent, {ev['violation_count']} violation(s)"
        ),
        "evaluated_at": ev["created_at"],
        "coverage": [
            {"id": f["ref_id"], "status": f["status"], "quote": f["quote"],
             "excerpt": f["excerpt"], "reason": f["reason"]}
            for f in findings if f["kind"] == "coverage"
        ],
        "violations": [
            {"id": f["ref_id"], "quote": f["quote"],
             "excerpt": f["excerpt"], "reason": f["reason"]}
            for f in findings if f["kind"] == "anti_pattern"
        ],
        "roundtrip_passed":   not roundtrip,
        "roundtrip_failures": roundtrip,
    }


@mcp.tool()
def run_coverage(app_uuid: str) -> dict:
    """
    Tier 3. Judge the generated CV against its reviewed jd_spec: is each requirement
    actually addressed, and does anything trip an anti-pattern the ad stated?

    This is the only judge that looks FORWARDS, at the job ad. Tiers 1 and 2 both look
    backwards (prose mechanics; claims against the library), which means a CV can be
    clean, accurate, fully supported — and aimed at the wrong target — while passing
    everything. That is the observed default, not a hypothetical.

    ESCALATES, does not block. An absent must-have or an anti-pattern violation is
    reported for a human to act on. It does not trigger an automatic retry, because
    retrying against the same spec cannot conjure evidence the library does not hold; it
    only produces more confident-sounding evasion. A gap is information about fit, not a
    defect.
    """
    from pipeline.judges import coverage

    with get_connection() as db:
        row  = _get_app(db, app_uuid)
        spec = _load_spec(row)
        if not spec:
            raise ValueError(f"Application {app_uuid} has no jd_spec — run analyse_job first.")
        if not row["cv_markdown"]:
            raise ValueError(f"Application {app_uuid} has no CV yet — run generate_cv first.")

        result = coverage.run(row["cv_markdown"], spec)

        eval_id = str(uuid.uuid4())
        now     = datetime.now().isoformat()
        db.execute(
            """INSERT INTO coverage_evaluations
               (id, application_id, passed, covered_count, partial_count, absent_count,
                violation_count, model, prompt_tokens, completion_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eval_id, row["id"], int(result.passed), result.covered_count,
                result.partial_count, result.absent_count, result.violation_count,
                result.model, result.prompt_tokens, result.completion_tokens, now,
            ),
        )
        for f in result.findings:
            db.execute(
                """INSERT INTO coverage_findings
                   (id, evaluation_id, kind, ref_id, quote, status, excerpt, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), eval_id, f.kind, f.ref_id, f.quote,
                    f.status, f.excerpt, f.reason, now,
                ),
            )

        # Persist to disk as well as the DB. This call reliably outlives the MCP response
        # window, so the return value is often never seen. An artefact on disk survives a
        # timeout; a return value does not.
        out_dir = row["output_dir"]
        if out_dir:
            from pathlib import Path
            d = Path(out_dir)
            if d.exists():
                (d / "coverage.md").write_text(
                    _render_coverage_md(result, spec), encoding="utf-8"
                )
                (d / "coverage.json").write_text(json.dumps({
                    "passed":  result.passed,
                    "summary": result.summary,
                    "findings": [
                        {"kind": f.kind, "id": f.ref_id, "quote": f.quote,
                         "status": f.status, "excerpt": f.excerpt, "reason": f.reason}
                        for f in result.findings
                    ],
                }, indent=2), encoding="utf-8")

    return {
        "uuid":     app_uuid,
        "passed":   result.passed,
        "summary":  result.summary,
        "coverage": [
            {"id": f.ref_id, "status": f.status, "quote": f.quote,
             "excerpt": f.excerpt, "reason": f.reason}
            for f in result.findings if f.kind == "coverage"
        ],
        "violations": [
            {"id": f.ref_id, "quote": f.quote, "excerpt": f.excerpt, "reason": f.reason}
            for f in result.findings if f.kind == "anti_pattern"
        ],
    }
