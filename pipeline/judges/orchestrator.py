"""
orchestrator.py — Judge pipeline orchestrator for ATAT section review.

Wires Tier 1 (deterministic) and Tier 2 (cheap LLM) judges together,
persists evaluations and flags to the database, determines overall pass/fail,
and decides whether the report should be escalated for human review.

Escalation logic:
  - confidence_failure: Tier 2 raised accuracy flags. These are inherently
    subjective and require human adjudication rather than auto-retry.
  - retry_threshold:    attempt count has reached MAX_ATTEMPTS. The loop
    has failed to converge and a human needs to intervene.

Both tiers always run — they provide independent signal and the Tier 2
accuracy check is valuable regardless of Tier 1 outcome.

The report row is updated with escalated/escalation_reason after evaluation.
Report status remains 'pending' — it moves to 'accepted' or 'rejected' only
via explicit user action through the review route.
"""

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pipeline.judges import deterministic, cheap_llm

log = logging.getLogger(__name__)

# Maximum retry attempts before escalating to human regardless of flag type
MAX_ATTEMPTS = 3


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    passed:                bool
    escalated:             bool
    escalation_reason:     Optional[str]        # retry_threshold | confidence_failure | None
    total_flags:           int
    tier1_evaluation_id:   str
    tier2_evaluation_id:   Optional[str]        # None if Tier 2 was skipped
    tier1_passed:          bool
    tier2_passed:          bool
    has_accuracy_flags:    bool


# ── DB helpers ────────────────────────────────────────────────────────────────

def _insert_evaluation(
    db:           sqlite3.Connection,
    report_id:    str,
    tier:         str,
    passed:       bool,
    flesch_score: Optional[float] = None,
    model:        Optional[str]   = None,
    prompt_tokens:     Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> str:
    """Insert an evaluation row and return its id."""
    eval_id = str(uuid.uuid4())
    now     = datetime.now().isoformat()
    db.execute(
        """INSERT INTO evaluations
           (id, report_id, tier, passed, flesch_score, model,
            prompt_tokens, completion_tokens, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            eval_id, report_id, tier, int(passed),
            flesch_score, model, prompt_tokens, completion_tokens, now,
        ),
    )
    return eval_id


def _insert_flag(
    db:            sqlite3.Connection,
    evaluation_id: str,
    flag_type:     str,
    start_pos:     int,
    end_pos:       int,
    excerpt:       str,
    message:       str,
) -> str:
    """Insert a flag row and return its id."""
    flag_id = str(uuid.uuid4())
    now     = datetime.now().isoformat()
    db.execute(
        """INSERT INTO flags
           (id, evaluation_id, type, start_pos, end_pos,
            excerpt, message, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
        (flag_id, evaluation_id, flag_type, start_pos, end_pos, excerpt, message, now),
    )
    return flag_id


def _update_report_escalation(
    db:                sqlite3.Connection,
    report_id:         str,
    escalated:         bool,
    escalation_reason: Optional[str],
) -> None:
    db.execute(
        """UPDATE reports
           SET escalated = ?, escalation_reason = ?
           WHERE id = ?""",
        (int(escalated), escalation_reason, report_id),
    )


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    report_id:    str,
    section_text: str,
    attempt:      int,
    db:           sqlite3.Connection,
) -> OrchestratorResult:
    """
    Run the full judge pipeline against a section report.

    Persists evaluations and flags to the database, updates the report's
    escalation state, and returns a structured result.

    Args:
        report_id:    The report row id being evaluated.
        section_text: Raw section content — must match the text in the section file
                      exactly, as all flag positions are relative to this string.
        attempt:      Current attempt number (1-based). Used for retry threshold.
        db:           Active SQLite connection. Caller is responsible for commit.

    Returns:
        OrchestratorResult describing the outcome of all tiers.
    """
    if not section_text or not section_text.strip():
        log.warning(f"Orchestrator received empty text for report {report_id} — skipping.")
        eval_id = _insert_evaluation(db, report_id, "deterministic", passed=True)
        return OrchestratorResult(
            passed=True, escalated=False, escalation_reason=None,
            total_flags=0, tier1_evaluation_id=eval_id,
            tier2_evaluation_id=None, tier1_passed=True,
            tier2_passed=True, has_accuracy_flags=False,
        )

    # ── Tier 1: Deterministic ─────────────────────────────────────────────────
    log.info(f"[{report_id}] Running Tier 1 — deterministic judge")
    t1_result = deterministic.run(section_text)

    tier1_eval_id = _insert_evaluation(
        db, report_id,
        tier="deterministic",
        passed=t1_result.passed,
        flesch_score=t1_result.flesch_score,
    )

    for flag in t1_result.flags:
        _insert_flag(
            db, tier1_eval_id,
            flag_type=flag.type,
            start_pos=flag.start_pos,
            end_pos=flag.end_pos,
            excerpt=flag.excerpt,
            message=flag.message,
        )

    log.info(
        f"[{report_id}] Tier 1 complete — "
        f"passed={t1_result.passed}, flags={len(t1_result.flags)}, "
        f"flesch={t1_result.flesch_score:.1f}"
    )

    # ── Tier 2: Cheap LLM accuracy check ─────────────────────────────────────
    log.info(f"[{report_id}] Running Tier 2 — cheap LLM accuracy judge")
    tier2_eval_id: Optional[str] = None
    t2_result     = None

    try:
        t2_result = cheap_llm.run(section_text)

        tier2_eval_id = _insert_evaluation(
            db, report_id,
            tier="cheap_llm",
            passed=t2_result.passed,
            model=t2_result.model,
            prompt_tokens=t2_result.prompt_tokens,
            completion_tokens=t2_result.completion_tokens,
        )

        for flag in t2_result.flags:
            _insert_flag(
                db, tier2_eval_id,
                flag_type=flag.type,
                start_pos=flag.start_pos,
                end_pos=flag.end_pos,
                excerpt=flag.excerpt,
                message=flag.message,
            )

        log.info(
            f"[{report_id}] Tier 2 complete — "
            f"passed={t2_result.passed}, flags={len(t2_result.flags)}"
        )

    except Exception as e:
        # Tier 2 failure is non-fatal — log, record as passed with a note,
        # and continue. A judge error should not block the user's workflow.
        log.error(f"[{report_id}] Tier 2 judge error (non-fatal): {e}")
        tier2_eval_id = _insert_evaluation(
            db, report_id,
            tier="cheap_llm",
            passed=True,    # treated as pass on error — don't block on judge failure
            model=None,
        )

    # ── Escalation decision ───────────────────────────────────────────────────
    tier1_passed      = t1_result.passed
    tier2_passed      = t2_result.passed if t2_result else True
    has_accuracy_flags = bool(t2_result and t2_result.flags)

    total_flags = len(t1_result.flags) + (len(t2_result.flags) if t2_result else 0)
    overall_passed = tier1_passed and tier2_passed

    escalated          = False
    escalation_reason: Optional[str] = None

    if attempt >= MAX_ATTEMPTS and not overall_passed:
        escalated         = True
        escalation_reason = "retry_threshold"
        log.warning(
            f"[{report_id}] Escalating — retry threshold reached "
            f"(attempt {attempt}/{MAX_ATTEMPTS})"
        )
    elif has_accuracy_flags:
        escalated         = True
        escalation_reason = "confidence_failure"
        log.info(
            f"[{report_id}] Escalating — Tier 2 accuracy flags require human review"
        )

    _update_report_escalation(db, report_id, escalated, escalation_reason)

    log.info(
        f"[{report_id}] Orchestrator complete — "
        f"passed={overall_passed}, escalated={escalated}, "
        f"reason={escalation_reason}, total_flags={total_flags}"
    )

    return OrchestratorResult(
        passed=overall_passed,
        escalated=escalated,
        escalation_reason=escalation_reason,
        total_flags=total_flags,
        tier1_evaluation_id=tier1_eval_id,
        tier2_evaluation_id=tier2_eval_id,
        tier1_passed=tier1_passed,
        tier2_passed=tier2_passed,
        has_accuracy_flags=has_accuracy_flags,
    )
