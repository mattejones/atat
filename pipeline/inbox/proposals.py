"""
proposals.py — turn a classification into the change to suggest, never a step backwards.

The ranking mirrors STATUS_FLOW in web/src/lib/statuses.ts: an acknowledgement that
arrives after you're already interviewing is linked to the application but doesn't
propose moving it back to 'acknowledged'. A rejection or offer is always allowed from a
live application. A ghosted application is revived by any real email.
"""

from __future__ import annotations

from typing import Optional

CLASSIFICATIONS = (
    "acknowledgement", "rejection", "interview_request", "assessment", "offer",
    "progress_update", "recruiter_outreach", "other_job", "not_job",
)

TARGET_STATUS = {
    "acknowledgement":   "acknowledged",
    "rejection":         "rejected",
    "interview_request": "interviewing",
    "assessment":        "case_study",
    "offer":             "offered",
}

_RANK = {
    "analysed": 0, "generated": 0, "reviewing": 0,
    "applied": 1, "acknowledged": 2, "interviewing": 3, "case_study": 3, "offered": 4,
}
_FROZEN = {"archived", "excluded", "rejected"}


def status_allowed(current: Optional[str], target: Optional[str]) -> bool:
    if not target or target == current:
        return False
    if current in _FROZEN:
        return False
    if current == "ghosted" or current is None:
        return True
    if target == "rejected":
        return True
    if current == "offered":
        return False
    return _RANK.get(target, 0) >= _RANK.get(current, 0)


def propose(
    classification: Optional[str],
    app: Optional[dict],
    received_date: str,
    interview_at: Optional[str] = None,
) -> dict:
    """
    {proposed_status, proposed_date_type, proposed_date} for an email linked to `app`
    (an applications row with applied_date). All None when nothing should change.
    """
    out = {"proposed_status": None, "proposed_date_type": None, "proposed_date": None}
    if not app or classification not in TARGET_STATUS:
        return out

    target = TARGET_STATUS[classification]
    if status_allowed(app.get("status"), target):
        out["proposed_status"] = target

    if app.get("status") in _FROZEN and classification != "rejection":
        return out
    if classification == "rejection" and app.get("status") != "rejected":
        out["proposed_date_type"], out["proposed_date"] = "rejected", received_date
    elif classification == "offer":
        out["proposed_date_type"], out["proposed_date"] = "offer_received", received_date
    elif classification == "interview_request" and interview_at:
        out["proposed_date_type"], out["proposed_date"] = "interview", interview_at
    elif classification == "acknowledgement" and not app.get("applied_date"):
        # A "thanks for applying" is the best evidence of when you applied.
        out["proposed_date_type"], out["proposed_date"] = "applied", received_date
    return out
