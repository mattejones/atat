"""
tools_spec.py — two-phase intake: analyse the ad, review the spec, then generate.

The old flow was single-phase. submit_job() took a job ad plus freehand generation_notes
and produced a CV in one shot. The notes were the only thing steering a strategic decision
(what to lead with, what to suppress, what the reader will disqualify you for), they were
written by a model, nobody validated them, and no downstream judge ever checked whether
the resulting CV actually answered the ad. It was the one unstructured, unvalidated,
load-bearing artefact in an otherwise disciplined pipeline.

Two-phase replaces it:

    analyse_job(jd_text)          -> [async job] extracts a jd_spec, maps it to library evidence,
                                     computes a tier from must-have coverage,
                                     saves the application at status 'analysed'.
                                     NO CV IS GENERATED. NO TOKENS ARE SPENT TAILORING.

    [ human reads render_spec_for_review() output and decides ]

    update_jd_spec(...)           -> optional: correct the extraction
    generate_cv(app_uuid)         -> [async job] tailors against the reviewed spec, splits sections,
                                     runs tier 0, composes cv.md, renders PDF
    run_coverage(app_uuid)        -> [async job] tier 3: does the CV answer the ad?

The generative steps run as background jobs (pipeline.jobs): each returns a job_id at
once, and the result is read back with get_job. The work lives in pipeline/jobs/kinds/.

The point of the split is that the expensive, irreversible step (generation) now happens
AFTER a human has seen the gaps, not before. On a batch of parked applications this also
gives you triage for free: the computed tier tells you where the fit genuinely is, so you
can spend your effort where it converts rather than spreading it evenly across everything.

submit_job() is left intact in tools_intake for backward compatibility.
"""

import json
from datetime import datetime
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import get_connection


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
    draft: bool = False,
) -> dict:
    """
    Phase 1 of two. Extract a structured jd_spec from a job ad and map it against the
    experience library. Creates the application at status 'analysed'. Does NOT generate
    a CV.

    ASYNC: returns immediately with a job_id. When get_job shows it succeeded, the result
    holds the new application's uuid, the spec's computed tier, its gaps, and a markdown
    review document. STOP THERE and put the review document in front of the applicant
    before calling generate_cv. That is the entire point of the two-phase split: the
    human sees the gaps before a generation is spent, not after.

    draft=True: nothing is sent to a model yet. Returns a draft (inputs + prompt preview)
    for review; send it with submit_draft.

    The spec enforces two invariants, both validated on write:
      - every requirement/anti-pattern quote is VERBATIM from the ad (if it can't be
        quoted, it doesn't exist)
      - every evidence reference resolves to a real cv-library heading (a ref that
        doesn't resolve is a hallucination, and the extraction is rejected)

    A requirement with no evidence is a GAP. Gaps are not errors — they are the most
    useful thing this tool produces. They tell you where the fit genuinely isn't, and
    they drive the computed tier.

    The job fails (SpecValidationError) if the extraction is untrustworthy. It is not
    repaired silently: a laundered spec looks authoritative and isn't.
    """
    from pipeline.jobs import service
    return service.create(
        "analyse_job",
        params={"jd_text": jd_text, "company": company, "role": role, "source_url": source_url},
        draft=draft,
    )


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
def generate_cv(app_uuid: str, generation_notes: Optional[str] = None, draft: bool = False) -> dict:
    """
    Phase 2 of two. Generate the CV against a reviewed jd_spec.

    ASYNC: returns immediately with a job_id; the result (cv_markdown, roundtrip
    outcome) arrives via get_job. Generation takes minutes — do other work meanwhile.

    draft=True: nothing is sent to a model yet. Returns a draft whose prompt_preview shows
    exactly what the tailoring model will be given (spec, notes, job ad), so the applicant
    can adjust generation_notes with update_draft before submit_draft sends it.

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
    from pipeline.jobs import service
    return service.create(
        "generate_cv", app_uuid=app_uuid, params={"generation_notes": generation_notes}, draft=draft,
    )


# ── Tier 3 ────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_coverage(app_uuid: str) -> dict:
    """
    Read back the most recent tier 3 coverage result, plus any tier 0 roundtrip failures.

    run_coverage's job result carries the same findings, but this reads whatever is
    latest on disk at any time — e.g. for an application judged in an earlier session.
    Read it rather than re-running run_coverage: re-running spends the tokens again to
    recompute an answer that is already stored.
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
def run_coverage(app_uuid: str, draft: bool = False) -> dict:
    """
    Tier 3. Judge the generated CV against its reviewed jd_spec: is each requirement
    actually addressed, and does anything trip an anti-pattern the ad stated?

    ASYNC: returns immediately with a job_id; the findings arrive via get_job (and stay
    readable afterwards with get_coverage). draft=True returns a draft for review instead.

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
    from pipeline.jobs import service
    return service.create("run_coverage", app_uuid=app_uuid, draft=draft)
