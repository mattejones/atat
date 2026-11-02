"""
insights.py — application outcomes derived from history (pipeline/insights/outcomes.py).

Endpoints:
  GET  /insights/report            — everything the Insights page shows: summary, funnel,
                                     breakdowns, timing, in-scope and excluded applications
  GET  /insights/funnel            — the funnel, or ?group_by= for a breakdown
                                     (tier | persona | work_arrangement | month | ats | location | company)
  GET  /insights/timing            — days from applying to each milestone
  GET  /insights/forecast          — pipeline health: each open application's chances, the
                                     expected screens/rounds to come, per-application rates
  GET  /insights/outcomes          — summary counts + one row per application
                                     (?outcome=, ?include_excluded=true, ?flag=)
  GET  /insights/outcomes/{uuid}   — one application's outcome

Outcomes are computed on every request from accepted emails and status changes; nothing
is stored. The outside-inbox flag is set with PATCH /applications/{uuid}.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from db.database import get_connection
from pipeline.insights import outcomes as oc
from pipeline.insights import forecast, report

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/report")
def get_report():
    with get_connection() as db:
        return report.build(db)


@router.get("/funnel")
def get_funnel(group_by: Optional[str] = None):
    if group_by and group_by not in report.DIMENSIONS:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(report.DIMENSIONS)}")
    with get_connection() as db:
        if group_by:
            return report.grouped(db, group_by)
        outcomes, _ = oc.load_with_window(db)
    return report.funnel(outcomes)


@router.get("/timing")
def get_timing():
    with get_connection() as db:
        outcomes, window = oc.load_with_window(db)
    return {"timing": report.timing(outcomes), "response_window": window}


@router.get("/forecast")
def get_forecast():
    with get_connection() as db:
        return forecast.load(db)


@router.get("/outcomes")
def list_outcomes(outcome: Optional[str] = None, include_excluded: bool = False, flag: Optional[str] = None):
    if outcome and outcome not in oc.OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {', '.join(oc.OUTCOMES)}")
    with get_connection() as db:
        rows, window = oc.load_with_window(db)
        start = oc.coverage_start(db)
    summary = oc.summarise(rows, start, window)
    picked = [
        r for r in rows
        if (r.in_scope or include_excluded)
        and (outcome is None or r.outcome == outcome)
        and (flag is None or flag in r.flags)
    ]
    picked.sort(key=lambda r: (r.applied_on is None, r.applied_on), reverse=True)
    return {"summary": summary, "applications": [r.as_dict() for r in picked]}


@router.get("/outcomes/{app_uuid}")
def get_outcome(app_uuid: str):
    with get_connection() as db:
        found = oc.outcome_for(db, app_uuid)
        if found:
            found["score"] = forecast.score_for(db, app_uuid)
    if not found:
        raise HTTPException(status_code=404, detail="Application not found")
    return found
