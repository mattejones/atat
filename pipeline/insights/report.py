"""
report.py — funnel, breakdowns and timing over derived outcomes.

Everything here is computed from pipeline/insights/outcomes.py on each request. Rates
come with a 95% range (Wilson score interval), because at these sample sizes a single
percentage overstates what's known; groups under SMALL_GROUP applications are marked
too small to read rather than hidden.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Callable, Optional

from pipeline.insights import outcomes as oc

SMALL_GROUP = 8
DIMENSIONS = ("tier", "persona", "work_arrangement", "month", "ats", "location", "company")


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """95% Wilson score interval for k successes out of n, as fractions. None when n is 0."""
    if n <= 0:
        return None
    p = k / n
    denom  = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _rate(k: int, n: int) -> dict:
    ci = wilson(k, n)
    return {
        "count": k, "of": n,
        "rate": (k / n) if n else None,
        "low": ci[0] if ci else None, "high": ci[1] if ci else None,
    }


# ── Funnel ────────────────────────────────────────────────────────────────────

_STEPS: list[tuple[str, str, Callable[[oc.Outcome], bool]]] = [
    ("in_scope",     "Applied (in scope)", lambda o: True),
    ("answered",     "Answered",           lambda o: o.answered),
    ("engaged",      "Engaged",            lambda o: o.engaged),
    ("interviewed",  "Interviewed",        lambda o: o.furthest_stage in ("interview", "offer")),
    ("second_round", "Second round",       lambda o: o.rounds >= 2),
    ("third_round",  "Third round",        lambda o: o.rounds >= 3),
    ("offer",        "Offer",              lambda o: o.outcome == "offer"),
]


def funnel(outcomes: list[oc.Outcome]) -> dict:
    scope = [o for o in outcomes if o.in_scope]
    n = len(scope)
    steps, previous = [], n
    for key, label, test in _STEPS:
        k = sum(1 for o in scope if test(o))
        steps.append({
            "key": key, "label": label,
            "of_applied":    _rate(k, n),
            "from_previous": _rate(k, previous) if key != "in_scope" else None,
        })
        previous = k
    return {
        "steps": steps,
        # Receipts show the inbox caught the application; they are coverage, not a step.
        "receipts": _rate(sum(1 for o in scope if o.has_receipt), n),
    }


# ── Breakdowns ────────────────────────────────────────────────────────────────

def _group_value(o: oc.Outcome, dim: str, extra: dict) -> str:
    if dim == "month":
        return o.applied_on.strftime("%Y-%m") if o.applied_on else "(unknown)"
    if dim == "ats":
        return o.ats or "(none / direct)"
    if dim in ("location", "company"):
        return (extra.get(o.uuid, {}).get(dim) or "(unset)").strip() or "(unset)"
    return getattr(o, dim) or "(unset)"


def breakdown(outcomes: list[oc.Outcome], dim: str, extra: Optional[dict] = None) -> list[dict]:
    """Answer, engagement and interview rates per group of in-scope applications."""
    if dim not in DIMENSIONS:
        raise ValueError(f"dimension must be one of {', '.join(DIMENSIONS)}")
    groups: dict[str, list[oc.Outcome]] = defaultdict(list)
    for o in outcomes:
        if o.in_scope:
            groups[_group_value(o, dim, extra or {})].append(o)
    rows = []
    for value, members in groups.items():
        n = len(members)
        by_outcome = {k: sum(1 for o in members if o.outcome == k) for k in oc.OUTCOMES}
        rows.append({
            "group":        value,
            "applications": n,
            "small":        n < SMALL_GROUP,
            "answered":     _rate(sum(1 for o in members if o.answered), n),
            "engaged":      _rate(sum(1 for o in members if o.engaged), n),
            "interviewed":  _rate(sum(1 for o in members if o.furthest_stage in ("interview", "offer")), n),
            "second_round": _rate(sum(1 for o in members if o.rounds >= 2), n),
            "by_outcome":   by_outcome,
        })
    if dim == "month":
        rows.sort(key=lambda r: r["group"])
    else:
        rows.sort(key=lambda r: (-r["applications"], r["group"]))
    return rows


# ── Timing ────────────────────────────────────────────────────────────────────

def _spread(days: list[int]) -> dict:
    if not days:
        return {"n": 0, "median": None, "p90": None}
    return {"n": len(days), "median": round(median(days)), "p90": oc._percentile(days, 0.9)}


def _days(o: oc.Outcome, to: Optional[date]) -> Optional[int]:
    if not (to and o.applied_on):
        return None
    d = (to - o.applied_on).days
    return d if d >= 0 else None


def timing(outcomes: list[oc.Outcome]) -> list[dict]:
    """Days from applying to each milestone, for in-scope applications that reached it."""
    scope = [o for o in outcomes if o.in_scope]

    def measure(key, label, pick, among=None):
        pool = [o for o in scope if among is None or o.outcome in among]
        days = [d for d in (_days(o, pick(o)) for o in pool) if d is not None]
        return {"key": key, "label": label, **_spread(days)}

    return [
        measure("first_answer",     "First answer (engagement or rejection)", lambda o: o.first_answer),
        measure("first_engagement", "First engagement",                       lambda o: o.first_engagement),
        measure("first_interview",  "First interview",                        lambda o: o.first_interview),
        measure("screened_out",     "Rejection, no engagement",               lambda o: o.decision_on, {"screened_out"}),
        measure("rejected_after",   "Rejection after engagement",             lambda o: o.decision_on, {"rejected_after_engagement"}),
    ]


# ── Everything, for the Insights page ─────────────────────────────────────────

def _extra(db: sqlite3.Connection) -> dict:
    return {r["uuid"]: {"location": r["location"], "company": r["company"]}
            for r in db.execute("SELECT uuid, location, company FROM applications").fetchall()}


def build(db: sqlite3.Connection, *, dims: tuple[str, ...] = ("tier", "persona", "work_arrangement", "month", "ats"),
          today: Optional[date] = None) -> dict:
    outcomes, window = oc.load_with_window(db, today=today)
    coverage = oc.coverage_start(db)
    extra    = _extra(db) if any(d in ("location", "company") for d in dims) else {}
    in_scope = sorted((o for o in outcomes if o.in_scope), key=lambda o: (o.applied_on is None, o.applied_on), reverse=True)
    excluded = sorted((o for o in outcomes if not o.in_scope), key=lambda o: (o.exclusion or "", o.company or ""))
    summary  = oc.summarise(outcomes, coverage, window)
    summary.pop("labels", None)
    return {
        "summary":      summary,
        "funnel":       funnel(outcomes),
        "breakdowns":   {d: breakdown(outcomes, d, extra) for d in dims},
        "timing":       timing(outcomes),
        "applications": [o.as_dict() for o in in_scope],
        "excluded":     [o.as_dict() for o in excluded],
        "small_group":  SMALL_GROUP,
        "labels":       {"outcomes": oc.OUTCOME_LABELS, "exclusions": oc.EXCLUSIONS},
    }


def grouped(db: sqlite3.Connection, dim: str, today: Optional[date] = None) -> list[dict]:
    outcomes, _ = oc.load_with_window(db, today=today)
    return breakdown(outcomes, dim, _extra(db) if dim in ("location", "company") else None)
