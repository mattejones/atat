"""
forecast.py — how much each open application is still worth, and what the pipeline adds up to.

Each open application gets a probability of reaching each next stage (a screen, a second
round, a third round), given where it stands and how long it has been quiet. The model is
one formula, used at every stage:

    P(next step still to come | quiet for d days) = p·G(d) / (p·G(d) + 1 − p)

  p     the base rate: of past applications at this stage, the share that went on to the
        next one (smoothed, so 0 of 2 doesn't read as "never")
  G(d)  of those that did go on, the share that took longer than d days — the empirical
        wait distribution, with a small tail so it never reaches exactly zero

At day 0 that is just the base rate; as the silence outgrows how long real replies took,
it falls towards zero. A receipt counts for nothing: an acknowledged application waits on
the same curve as one that got no email at all.

Everything is learned from settled history (outcomes no longer awaiting a reply or in
progress), so the forecast improves as outcomes accumulate. Offers are not forecast until
there is at least one to learn from.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional

from pipeline.insights import outcomes as oc
from pipeline.insights.report import wilson

# Applications whose outcome is still open, and so get a score.
_OPEN = {"awaiting_reply", "unanswered", "in_progress", "ghosted"}
# Too fresh for a receipt yet, but very much alive — scored too.
_SCORED_EXCLUSIONS = {"no_receipt_yet"}
# Settled outcomes the model learns from.
_SETTLED = {"offer", "rejected_after_engagement", "ghosted", "screened_out", "unanswered"}

MIN_WAITS = 3            # fewer observed waits than this → fall back to the response window
ROUND_POOL_BELOW = 4     # fewer applications at a round than this → use the pooled round rate


def weekly_target() -> Optional[float]:
    from pipeline.config import WEEKLY_SCREEN_TARGET

    return WEEKLY_SCREEN_TARGET or None


# ── The model ─────────────────────────────────────────────────────────────────

@dataclass
class Survival:
    """G(d): of the waits that ended in progress, the share still unfinished after d quiet days."""
    waits:  list[int]
    window: int

    def __call__(self, d: int) -> float:
        if len(self.waits) < MIN_WAITS:
            # Not enough history: trust the response window, with a faint tail beyond it.
            return 1.0 if d <= self.window else 0.1
        # A reply can still come on day d itself (d = 0 is the day you applied).
        longer = sum(1 for w in self.waits if w >= d)
        return (longer + 0.5) / (len(self.waits) + 1)


def still_to_come(p: float, g: float) -> float:
    """Bayes: the chance the next step comes, given it hasn't after a wait whose survival is g."""
    if p <= 0:
        return 0.0
    return p * g / (p * g + (1 - p))


def _smoothed(k: int, n: int) -> float:
    return (k + 1) / (n + 2)


@dataclass
class Model:
    screen_rate:  float                 # p for a first screen, from settled applications
    screen_raw:   tuple[int, int]       # (engaged, settled)
    round_rates:  dict[int, float]      # p(round r+1 | round r)
    round_raw:    dict[int, tuple[int, int]]
    wait_screen:  Survival              # days from applying to first engagement
    wait_round:   Survival              # days between consecutive rounds
    offers_seen:  int
    window:       int

    def next_round_rate(self, r: int) -> float:
        return self.round_rates.get(r, self.round_rates.get(0, 0.5))

    def describe(self) -> dict:
        k, n = self.screen_raw
        ci = wilson(k, n)
        return {
            "screen_rate":     self.screen_rate,
            "screen_observed": {"count": k, "of": n, "low": ci[0] if ci else None, "high": ci[1] if ci else None},
            "round_rates":     {str(r): v for r, v in self.round_rates.items() if r > 0},
            "round_observed":  {str(r): {"count": a, "of": b} for r, (a, b) in self.round_raw.items() if r > 0},
            "wait_to_screen":  {"n": len(self.wait_screen.waits), "median": _median(self.wait_screen.waits)},
            "wait_between_rounds": {"n": len(self.wait_round.waits), "median": _median(self.wait_round.waits)},
            "offers_seen":     self.offers_seen,
            "window_days":     self.window,
        }


def _median(values: list[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def fit(outcomes: list[oc.Outcome], window: int) -> Model:
    settled = [o for o in outcomes if o.in_scope and o.outcome in _SETTLED]
    engaged = [o for o in settled if o.engaged]

    # Waits come from every application that progressed, settled or not: a live
    # application's first screen is just as real a data point.
    progressed = [o for o in outcomes if o.in_scope and o.engaged and o.first_engagement and o.applied_on]
    wait_screen = [(o.first_engagement - o.applied_on).days for o in progressed if o.first_engagement >= o.applied_on]
    gaps = [
        (b - a).days
        for o in outcomes if o.in_scope
        for a, b in zip(o.round_dates, o.round_dates[1:])
    ]

    round_raw: dict[int, tuple[int, int]] = {}
    top = max((o.rounds for o in engaged), default=0)
    for r in range(1, top + 1):
        at_r = [o for o in engaged if o.rounds >= r]
        round_raw[r] = (sum(1 for o in at_r if o.rounds >= r + 1), len(at_r))
    pooled_k = sum(k for k, _ in round_raw.values())
    pooled_n = sum(n for _, n in round_raw.values())
    round_raw[0] = (pooled_k, pooled_n)
    round_rates = {
        r: _smoothed(k, n) if (n >= ROUND_POOL_BELOW or r == 0) else _smoothed(pooled_k, pooled_n)
        for r, (k, n) in round_raw.items()
    }

    return Model(
        screen_rate = _smoothed(len(engaged), len(settled)),
        screen_raw  = (len(engaged), len(settled)),
        round_rates = round_rates,
        round_raw   = round_raw,
        wait_screen = Survival(wait_screen, window),
        wait_round  = Survival(gaps, window),
        offers_seen = sum(1 for o in outcomes if o.in_scope and o.outcome == "offer"),
        window      = window,
    )


# ── Scoring ───────────────────────────────────────────────────────────────────

def _is_scored(o: oc.Outcome) -> bool:
    return (o.in_scope and o.outcome in _OPEN) or (o.exclusion in _SCORED_EXCLUSIONS)


def score(o: oc.Outcome, model: Model, today: date) -> Optional[dict]:
    """
    Chances for one application: P(screen), P(second round), P(third round). Stages it has
    already reached are 1. None for closed or excluded applications.
    """
    if not _is_scored(o):
        return None

    if not o.engaged:
        quiet = max(0, (today - o.applied_on).days) if o.applied_on else 0
        p_screen = still_to_come(model.screen_rate, model.wait_screen(quiet))
        p_second = p_screen * model.next_round_rate(1)
        p_third  = p_second * model.next_round_rate(2)
        state    = f"{'receipt only' if o.has_receipt else 'no reply yet'}, {quiet} days since applying"
        waiting_for = "screen"
    else:
        r      = max(o.rounds, 1)
        since  = o.last_activity or o.first_engagement or o.applied_on
        quiet  = max(0, (today - since).days) if since else 0
        p_next = still_to_come(model.next_round_rate(r), model.wait_round(quiet))
        chain  = {r + 1: p_next}
        for k in range(r + 2, 4):
            chain[k] = chain[k - 1] * model.next_round_rate(k - 1)
        p_screen = 1.0
        p_second = 1.0 if r >= 2 else chain.get(2, 0.0)
        p_third  = 1.0 if r >= 3 else chain.get(3, 0.0)
        state    = f"round {r} done, quiet for {quiet} days"
        waiting_for = f"round {r + 1}"

    return {
        "uuid":        o.uuid,
        "company":     o.company,
        "role":        o.role,
        "outcome":     o.outcome,
        "outcome_label": oc.OUTCOME_LABELS.get(o.outcome) if o.outcome else "Just applied",
        "state":       state,
        "quiet_days":  quiet,
        "waiting_for": waiting_for,
        "p_screen":    round(p_screen, 3),
        "p_second":    round(p_second, 3),
        "p_third":     round(p_third, 3),
        # The chance of the *next* step for this application, for ranking.
        "p_next":      round(p_screen if not o.engaged else p_next, 3),
    }


# ── The whole picture ─────────────────────────────────────────────────────────

def _recent_rate(outcomes: list[oc.Outcome], today: date, days: int = 28) -> float:
    """Applications sent per week over the last `days` days (in scope or just sent)."""
    sent = [o for o in outcomes
            if o.applied_on and 0 <= (today - o.applied_on).days < days
            and (o.in_scope or o.exclusion in _SCORED_EXCLUSIONS)]
    return len(sent) / (days / 7)


def build(outcomes: list[oc.Outcome], window: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    model = fit(outcomes, window["days"])
    scores = [s for s in (score(o, model, today) for o in outcomes) if s]
    scores.sort(key=lambda s: -s["p_next"])

    fresh      = [s for s in scores if s["waiting_for"] == "screen"]
    engaged    = [s for s in scores if s["waiting_for"] != "screen"]
    # Past the response window a reply is a long shot: kept, but not part of the headline.
    live       = [s for s in fresh if s["quiet_days"] <= model.window]
    cold       = [s for s in fresh if s["quiet_days"] > model.window]
    # An interviewed application that has gone quiet past the window is stalled, not in
    # process: worth a couple of percent, but it shouldn't be counted as live.
    in_process = [s for s in engaged if s["outcome"] == "in_progress"]
    stalled    = [s for s in engaged if s["outcome"] != "in_progress"]
    q1       = model.next_round_rate(1)
    q2       = model.next_round_rate(2)
    per_app  = {
        "screen": model.screen_rate,
        "second": model.screen_rate * q1,
        "third":  model.screen_rate * q1 * q2,
        "offer":  None,          # not forecast until there is an offer to learn from
    }

    # New events still to come from applications already sent, and where they come from.
    def expect(group: list[dict]) -> dict:
        return {
            "screens":       round(sum(s["p_screen"] for s in group if s["p_screen"] < 1), 2),
            "second_rounds": round(sum(s["p_second"] for s in group if s["p_second"] < 1), 2),
            "third_rounds":  round(sum(s["p_third"] for s in group if s["p_third"] < 1), 2),
        }

    expected = expect(scores)
    expected_from = {
        "awaiting_screen":      expect(fresh),
        "awaiting_screen_live": expect(live),
        "awaiting_screen_cold": expect(cold),
        "in_process":           expect(in_process),
        "stalled":              expect(stalled),
    }
    # The headline: screens expected from every open application that hasn't screened yet,
    # then the second rounds those screens imply at the historical round-1→2 rate, plus the
    # ones already screened and still live. Stalled applications are left out — they're a
    # long shot, reported separately rather than padding the number to plan around.
    screens_to_come = expected_from["awaiting_screen"]["screens"]
    inferred_second = round(screens_to_come * q1, 2)
    headline = {
        "screens":                   screens_to_come,
        "second_rounds_from_screens": inferred_second,
        "second_rounds_in_process":  expected_from["in_process"]["second_rounds"],
        "second_rounds":             round(inferred_second + expected_from["in_process"]["second_rounds"], 2),
        "second_round_rate":         round(q1, 3),
    }

    target = weekly_target()
    pace   = _recent_rate(outcomes, today)
    plan   = None
    if target:
        needed = target / model.screen_rate if model.screen_rate else None
        plan = {
            "weekly_screen_target":  target,
            "applications_needed":   round(needed, 1) if needed else None,
            "recent_weekly_rate":    round(pace, 1),
            "expected_weekly_screens": round(pace * model.screen_rate, 2),
        }

    return {
        "as_of":        today.isoformat(),
        "model":        model.describe(),
        "per_application": per_app,
        "per_ten":      {k: (round(v * 10, 1) if v is not None else None) for k, v in per_app.items()},
        "pipeline":     {
            "open":            len(scores),
            "awaiting_screen": len(fresh),
            "in_process":      len(in_process),
            "stalled":         len(stalled),
            "live":            len(live),
            "cold":             len(cold),
            "headline":        headline,
            "expected":        expected,
            "expected_from":   expected_from,
        },
        "recent_weekly_rate": round(pace, 1),
        "plan":         plan,
        "applications": scores,
        "decay":        [
            {"day": d, "p_screen": round(still_to_come(model.screen_rate, model.wait_screen(d)), 3)}
            for d in (0, 3, 7, 14, 21, 28, 35, 45, 60)
        ],
    }


def load(db: sqlite3.Connection, today: Optional[date] = None) -> dict:
    outcomes, window = oc.load_with_window(db, today=today)
    return build(outcomes, window, today)


def score_for(db: sqlite3.Connection, app_uuid: str, today: Optional[date] = None) -> Optional[dict]:
    today = today or date.today()
    outcomes, window = oc.load_with_window(db, today=today)
    model = fit(outcomes, window["days"])
    found = next((o for o in outcomes if o.uuid == app_uuid), None)
    return score(found, model, today) if found else None
