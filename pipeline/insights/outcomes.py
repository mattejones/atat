"""
outcomes.py — what actually happened to each application, derived from its history.

An application's `status` is a single final value: one screened out with no engagement
and one rejected after four interviews both read 'rejected'. The outcome here is
derived on read from the evidence instead, and never stored:

  email evidence   accepted inbox emails only — pending, dismissed and ignored ones are
                   not evidence (a dismissed email was judged wrong on review)
  manual evidence  status changes made by hand (interviewing, case_study, offered,
                   rejected, ghosted) and dates logged by hand (interview, offer_received,
                   rejected) — rounds that happened outside this inbox

Two questions decide the outcome, both from content, never from the sender address
(many tracking systems send everything, invites included, from no-reply):

  engaged   the company moved the application forward: an interview or screening
            invite, an assessment, an offer, or a substantive update
  answered  engaged, or a decision (rejection) arrived

    engaged:      Offer | Rejected after engagement | Ghosted | In progress
    not engaged:  Screened out | Unanswered | Awaiting reply

An acknowledgement (receipt) is proof the submission arrived, not progress: it counts
toward coverage, never toward engagement.

Only in-scope applications get an outcome; the rest carry an exclusion reason so every
application is counted exactly once. See the plan's Cohort section for the rules.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

# ── Tunables ──────────────────────────────────────────────────────────────────
# Response window — silence longer than this ends an application: Unanswered with no
#   engagement, Ghosted after it. Derived from your own data: the day by which 90% of first
#   answers (engagement or rejection) have arrived, never under WINDOW_FLOOR_DAYS. With fewer
#   than WINDOW_MIN_SAMPLE answers it falls back to RESPONSE_WINDOW_DAYS; setting
#   RESPONSE_WINDOW_DAYS in the environment fixes it at that value instead.
# NO_RECEIPT_GRACE_DAYS — an application with no email yet is left out of reports for this
#   long, since its receipt may still be on the way; after it, it's flagged no_receipt.
# SUGGEST_OUTSIDE_DAYS — no email at all after this long suggests it went through another
#   channel (flag suggest_outside_inbox; never set automatically).
_WINDOW_OVERRIDE      = os.getenv("RESPONSE_WINDOW_DAYS")
RESPONSE_WINDOW_DAYS  = int(_WINDOW_OVERRIDE or 21)
WINDOW_FLOOR_DAYS     = 14
WINDOW_PERCENTILE     = 0.9
WINDOW_MIN_SAMPLE     = 10
NO_RECEIPT_GRACE_DAYS = 4
SUGGEST_OUTSIDE_DAYS  = 7
UNDATED_ROUND_GAP_DAYS = 7

OUTCOMES = (
    "offer", "in_progress", "rejected_after_engagement", "ghosted",
    "screened_out", "awaiting_reply", "unanswered",
)
OUTCOME_LABELS = {
    "offer":                     "Offer",
    "in_progress":               "In progress",
    "rejected_after_engagement": "Rejected after engagement",
    "ghosted":                   "Ghosted",
    "screened_out":              "Screened out",
    "awaiting_reply":            "Awaiting reply",
    "unanswered":                "Unanswered",
}
EXCLUSIONS = {
    "not_submitted":   "Not submitted",
    "archived_no_email": "Archived, no emails",
    "outside_inbox":   "Outside this inbox",
    "no_coverage":     "No inbox coverage yet",
    "before_coverage": "Before coverage",
    "no_receipt_yet":  "No receipt yet",
}

# Evidence kinds, weakest first — the order is the stage ladder.
STAGES = ("none", "receipt", "update", "assessment", "interview", "offer")
_ENGAGEMENT = {"update", "assessment", "interview", "offer"}

_EMAIL_KIND = {
    "acknowledgement":   "receipt",
    "progress_update":   "update",
    "assessment":        "assessment",
    "interview_request": "interview",
    "offer":             "offer",
    "rejection":         "rejection",
}
_STATUS_KIND = {
    "interviewing": "interview",
    "case_study":   "assessment",
    "offered":      "offer",
    "rejected":     "rejection",
}
_DATE_KIND = {
    "interview":      "interview",
    "offer_received": "offer",
    "rejected":       "rejection",
}
# Statuses that only make sense once an application has been sent.
_SENT_STATUSES = {"applied", "acknowledged", "interviewing", "case_study", "offered", "rejected", "ghosted"}
_UNSENT_STATUSES = {"analysed", "generated", "reviewing"}


def _day(value) -> Optional[date]:
    """Any stored date/datetime string → a date. None if missing or unparseable."""
    if not value:
        return None
    if isinstance(value, date):
        return value if not isinstance(value, datetime) else value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass
class Evidence:
    kind:   str            # receipt | update | assessment | interview | offer | rejection | email
    on:     Optional[date]
    source: str            # email | manual


@dataclass
class Outcome:
    uuid:             str
    company:          Optional[str]
    role:             Optional[str]
    status:           Optional[str]
    in_scope:         bool
    exclusion:        Optional[str] = None
    outcome:          Optional[str] = None
    engaged:          bool = False
    answered:         bool = False
    furthest_stage:   str = "none"
    evidence_source:  Optional[str] = None          # email | manual | both
    applied_on:       Optional[date] = None
    applied_estimated: bool = False
    first_engagement: Optional[date] = None
    first_interview:  Optional[date] = None
    decision_on:      Optional[date] = None
    last_email_on:    Optional[date] = None
    first_answer:     Optional[date] = None         # first engagement or decision
    last_meaningful:  Optional[date] = None         # applying, or the last real engagement —
                                                    # receipts and other automated mail don't count
    has_receipt:      bool = False
    tier:             Optional[str] = None
    persona:          Optional[str] = None
    work_arrangement: Optional[str] = None
    ats:              Optional[str] = None          # tracking system its emails came through
    email_count:      int = 0
    rounds:           int = 0                       # distinct interview rounds, approximate
    round_dates:      list[date] = field(default_factory=list)   # the day each round started
    last_activity:    Optional[date] = None         # latest email, round or engagement
    flags:            list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in self.__dict__.items()}
        out["round_dates"] = [d.isoformat() for d in self.round_dates]
        out["outcome_label"]   = OUTCOME_LABELS.get(self.outcome) if self.outcome else None
        out["exclusion_label"] = EXCLUSIONS.get(self.exclusion) if self.exclusion else None
        return out


# ── The rules ─────────────────────────────────────────────────────────────────

def derive(
    app:            dict,
    emails:         Iterable[dict],
    events:         Iterable[dict],
    applied_dates:  Iterable[str],
    logged_dates:   Iterable[dict] = (),
    *,
    today:          date,
    coverage_start: Optional[date],
    window_days:    int = RESPONSE_WINDOW_DAYS,
) -> Outcome:
    """
    One application's outcome. Pure: everything it needs is passed in.

    emails        accepted inbox_emails rows for this application (classification, received_at)
    events        its status_change events (to_status, detail, occurred_at)
    applied_dates its application_dates of type 'applied'
    logged_dates  its other application_dates (date_type, date, notes) — interview rounds etc.
    """
    emails = [e for e in emails]
    status = app.get("status")
    out = Outcome(
        uuid=app["uuid"], company=app.get("company"), role=app.get("role"), status=status, in_scope=False,
        tier=app.get("tier"), persona=app.get("persona"), work_arrangement=app.get("work_arrangement"),
        ats=ats_of(e.get("sender_domain") for e in emails),
    )

    evidence: list[Evidence] = []
    booked: list[date] = []          # interview dates booked in emails
    undated: list[date] = []         # when interview emails with no booked date arrived
    logged_rounds: set[date] = set() # interview dates logged by hand
    for e in emails:
        kind = _EMAIL_KIND.get(e.get("classification") or "", "email")
        evidence.append(Evidence(kind, _day(e.get("received_at")), "email"))
        if kind == "interview":
            at = _day(_extracted(e).get("interview_at"))
            if at:
                booked.append(at)
            elif _day(e.get("received_at")):
                undated.append(_day(e.get("received_at")))
    out.email_count   = len(emails)
    out.last_email_on = max((ev.on for ev in evidence if ev.on), default=None)

    ghost_marked = status == "ghosted"
    sent_on: list[date] = []
    for ev in events:
        to, detail, on = ev.get("to_status"), ev.get("detail") or "", _day(ev.get("occurred_at"))
        if to in ("applied",) or ev.get("event_type") == "submission_recorded":
            if on:
                sent_on.append(on)
        # Changes made by accepting an inbox email duplicate that email's evidence.
        if ev.get("event_type") == "status_change" and to in _STATUS_KIND and not detail.startswith("From email:"):
            evidence.append(Evidence(_STATUS_KIND[to], on, "manual"))
    # Dates logged by hand. Ones written by accepting an inbox email duplicate that email.
    for d in logged_dates:
        kind = _DATE_KIND.get(d.get("date_type"))
        if kind and not (d.get("notes") or "").startswith("From email:"):
            evidence.append(Evidence(kind, _day(d.get("date")), "manual"))
        if d.get("date_type") == "interview" and _day(d.get("date")):
            logged_rounds.add(_day(d.get("date")))
    # The current status counts even if the change predates event logging.
    if status in _STATUS_KIND and not any(e.kind == _STATUS_KIND[status] for e in evidence):
        evidence.append(Evidence(_STATUS_KIND[status], _day(app.get("updated_at")), "manual"))

    # ── Applied date ──
    applied = min((d for d in (_day(x) for x in applied_dates) if d), default=None)
    first_email = min((ev.on for ev in evidence if ev.source == "email" and ev.on), default=None)
    if applied and first_email and first_email < applied:
        # A recorded applied date later than the first email about it is a data-entry slip.
        applied = first_email
        out.flags.append("applied_date_after_first_email")
    if applied is None and sent_on:
        applied = min(sent_on)
    if applied is None and (status in _SENT_STATUSES or emails):
        dated = [ev.on for ev in evidence if ev.on]
        applied = min(dated) if dated else _day(app.get("created_at"))
        out.applied_estimated = True
    out.applied_on = applied

    # Silence is measured from this: an automated receipt (or a survey, or a careers-account
    # notice) is not a sign of life, and neither is the day an email was reviewed.
    meaningful = [ev.on for ev in evidence if ev.kind in _ENGAGEMENT | {"rejection"} and ev.on]
    out.last_meaningful = max([d for d in (applied, *meaningful) if d], default=None)

    # ── Cohort ──
    if applied is None or (status in _UNSENT_STATUSES and not emails):
        out.exclusion = "not_submitted"
    elif status == "archived" and not emails:
        # Archiving is tidy-up: with no email attached there's nothing to say what happened.
        out.exclusion = "archived_no_email"
    elif app.get("outside_inbox"):
        out.exclusion = "outside_inbox"
    elif coverage_start is None:
        out.exclusion = "no_coverage"
    elif applied < coverage_start:
        out.exclusion = "before_coverage"
    elif not emails and not any(e.source == "manual" for e in evidence) and (today - applied).days < NO_RECEIPT_GRACE_DAYS:
        out.exclusion = "no_receipt_yet"
    if out.exclusion:
        return out
    out.in_scope = True

    # ── Evidence summary ──
    engagement = [e for e in evidence if e.kind in _ENGAGEMENT]
    rejections = [e for e in evidence if e.kind == "rejection"]
    offers     = [e for e in evidence if e.kind == "offer"]
    interviews = [e for e in evidence if e.kind == "interview"]

    out.engaged  = bool(engagement)
    out.answered = out.engaged or bool(rejections)
    out.furthest_stage = max(
        (e.kind for e in evidence if e.kind in STAGES), key=STAGES.index, default="none",
    )
    sources = {e.source for e in engagement + rejections}
    out.evidence_source = "both" if len(sources) == 2 else (sources.pop() if sources else None)

    first = lambda items: min((e.on for e in items if e.on), default=None)   # noqa: E731
    last  = lambda items: max((e.on for e in items if e.on), default=None)   # noqa: E731
    out.first_engagement = first(engagement)
    out.first_interview  = first(interviews)
    out.has_receipt      = any(e.kind == "receipt" for e in evidence)
    answers = [d for d in (out.first_engagement, first(rejections)) if d]
    out.first_answer     = min(answers) if answers else None
    # Logged rounds were reported one by one, so each counts, except one logged on the
    # same day as an emailed booking (the same round recorded twice).
    dates = sorted(set(round_starts(booked)) | (logged_rounds - set(booked)))
    # An undated invite well before the first dated round was a round of its own (a screening
    # call booked by phone, say); one within a week is taken as the scheduling for that round.
    if undated and (not dates or min(undated) < dates[0] - timedelta(days=UNDATED_ROUND_GAP_DAYS)):
        dates = [min(undated), *dates]
    if not dates and out.first_interview:
        dates = [out.first_interview]
    out.round_dates = dates
    out.rounds      = len(dates)

    activity    = [d for d in (out.last_email_on, last(engagement), applied, *out.round_dates) if d]
    out.last_activity = max(activity) if activity else None
    days_silent = (today - max(activity)).days if activity else 0

    # ── Outcome (checked in this order: an offer outranks a later rejection) ──
    if out.engaged:
        last_rejection = last(rejections)
        if offers:
            out.outcome, out.decision_on = "offer", first(offers)
        elif rejections and (last_rejection is None or out.first_engagement is None or last_rejection >= out.first_engagement):
            out.outcome, out.decision_on = "rejected_after_engagement", last_rejection
        elif ghost_marked or days_silent > window_days:
            out.outcome = "ghosted"
        else:
            out.outcome = "in_progress"
    else:
        if rejections:
            out.outcome, out.decision_on = "screened_out", last(rejections)
        elif ghost_marked or (today - applied).days > window_days:
            out.outcome = "unanswered"
        else:
            out.outcome = "awaiting_reply"

    # ── Flags for review ──
    if not emails and not out.answered:
        days = (today - applied).days
        if days >= NO_RECEIPT_GRACE_DAYS:
            out.flags.append("no_receipt")
        if days > SUGGEST_OUTSIDE_DAYS:
            out.flags.append("suggest_outside_inbox")
    return out


_ATS_NAMES = {
    "greenhouse-mail": "greenhouse", "hire": "lever", "myworkdayjobs": "workday", "myworkday": "workday",
    "smartrecruitersmail": "smartrecruiters", "workablemail": "workable", "teamtailor-mail": "teamtailor",
    "ashbyhq": "ashby",
}


def ats_of(domains: Iterable[Optional[str]]) -> Optional[str]:
    """The applicant-tracking system most of an application's emails came through, if any."""
    from collections import Counter

    from pipeline.inbox.prefilter import ATS_DOMAINS

    names = Counter()
    for domain in domains:
        parts = (domain or "").lower().split(".")
        for i in range(len(parts) - 1):
            suffix = ".".join(parts[i:])
            if suffix in ATS_DOMAINS:
                label = suffix.split(".")[0]
                names[_ATS_NAMES.get(label, label)] += 1
                break
    return names.most_common(1)[0][0] if names else None


def _percentile(values: list[int], q: float) -> int:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, -(-int(q * 100) * len(ordered) // 100))     # ceil(q * n)
    return ordered[min(rank, len(ordered)) - 1]


def response_window(outcomes: list["Outcome"]) -> dict:
    """The silence, in days, after which an application is treated as over (see Tunables)."""
    sample = [
        (o.first_answer - o.applied_on).days for o in outcomes
        if o.in_scope and o.first_answer and o.applied_on and o.first_answer >= o.applied_on
    ]
    if _WINDOW_OVERRIDE:
        return {"days": RESPONSE_WINDOW_DAYS, "basis": "setting", "sample": len(sample), "p90": None}
    if len(sample) < WINDOW_MIN_SAMPLE:
        return {"days": RESPONSE_WINDOW_DAYS, "basis": "default", "sample": len(sample), "p90": None}
    p90 = _percentile(sample, WINDOW_PERCENTILE)
    return {"days": max(WINDOW_FLOOR_DAYS, p90), "basis": "data", "sample": len(sample), "p90": p90}


def _extracted(email: dict) -> dict:
    raw = email.get("extracted_json")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def count_rounds(dates: Iterable[date]) -> int:
    """
    Distinct interview rounds from dates booked in emails. Dates within a day of each other
    are one round (a reschedule, a confirmation and its reminder, a two-part day).

    A lower bound, and approximate: an invite with no booked date isn't counted (log the
    round by hand instead), and prep calls or debriefs with their own date count as rounds.
    """
    return len(round_starts(dates))


def round_starts(dates: Iterable[date]) -> list[date]:
    """The first day of each round, merging dates within a day of each other (see count_rounds)."""
    starts, last = [], None
    for d in sorted(set(dates)):
        if last is None or (d - last).days > 1:
            starts.append(d)
        last = d
    return starts


# ── Loading ───────────────────────────────────────────────────────────────────

def coverage_start(db: sqlite3.Connection) -> Optional[date]:
    """The earliest date the Gmail sync has read — every fetched message keeps a row."""
    override = os.getenv("INSIGHTS_COVERAGE_START")
    if override:
        return _day(override)
    row = db.execute("SELECT MIN(received_at) FROM inbox_emails").fetchone()
    return _day(row[0]) if row else None


def load_outcomes(
    db:     sqlite3.Connection,
    *,
    uuids:  Optional[list[str]] = None,
    today:  Optional[date] = None,
) -> list[Outcome]:
    """Outcomes for every application (or just `uuids`), using the data-driven window."""
    outcomes, _ = load_with_window(db, today=today)
    if uuids:
        wanted = set(uuids)
        outcomes = [o for o in outcomes if o.uuid in wanted]
    return outcomes


def load_with_window(db: sqlite3.Connection, *, today: Optional[date] = None) -> tuple[list[Outcome], dict]:
    """All outcomes plus the response window they were derived with."""
    today = today or date.today()
    apps = [dict(r) for r in db.execute("SELECT * FROM applications").fetchall()]
    if not apps:
        return [], response_window([])
    ids = [a["id"] for a in apps]
    marks = ",".join("?" * len(ids))

    emails: dict[str, list[dict]] = {}
    for r in db.execute(
        f"""SELECT application_id, classification, received_at, extracted_json, sender_domain FROM inbox_emails
            WHERE review_status = 'accepted' AND application_id IN ({marks})""", ids,
    ).fetchall():
        emails.setdefault(r["application_id"], []).append(dict(r))

    events: dict[str, list[dict]] = {}
    for r in db.execute(
        f"""SELECT application_id, event_type, to_status, detail, occurred_at FROM application_events
            WHERE event_type IN ('status_change', 'submission_recorded') AND application_id IN ({marks})
            ORDER BY occurred_at""", ids,
    ).fetchall():
        events.setdefault(r["application_id"], []).append(dict(r))

    applied: dict[str, list[str]] = {}
    logged:  dict[str, list[dict]] = {}
    for r in db.execute(
        f"SELECT application_id, date_type, date, notes FROM application_dates WHERE application_id IN ({marks})", ids,
    ).fetchall():
        if r["date_type"] == "applied":
            applied.setdefault(r["application_id"], []).append(r["date"])
        else:
            logged.setdefault(r["application_id"], []).append(dict(r))

    start = coverage_start(db)

    def run(window: int) -> list[Outcome]:
        return [
            derive(a, emails.get(a["id"], []), events.get(a["id"], []), applied.get(a["id"], []),
                   logged.get(a["id"], []), today=today, coverage_start=start, window_days=window)
            for a in apps
        ]

    # When the first answer arrived doesn't depend on the window, so one pass with the
    # default gives the sample the window is computed from.
    outcomes = run(RESPONSE_WINDOW_DAYS)
    window   = response_window(outcomes)
    if window["days"] != RESPONSE_WINDOW_DAYS:
        outcomes = run(window["days"])
    return outcomes, window


def outcome_for(db: sqlite3.Connection, app_uuid: str, today: Optional[date] = None) -> Optional[dict]:
    found = load_outcomes(db, uuids=[app_uuid], today=today)
    return found[0].as_dict() if found else None


def summarise(outcomes: list[Outcome], coverage: Optional[date], window: Optional[dict] = None) -> dict:
    """Counts that always add up: every application is in one outcome or one exclusion."""
    by_outcome   = {k: 0 for k in OUTCOMES}
    by_exclusion = {k: 0 for k in EXCLUSIONS}
    for o in outcomes:
        if o.in_scope:
            by_outcome[o.outcome] += 1
        else:
            by_exclusion[o.exclusion] += 1
    in_scope = sum(by_outcome.values())
    return {
        "total":                len(outcomes),
        "in_scope":             in_scope,
        "answered":             sum(1 for o in outcomes if o.in_scope and o.answered),
        "engaged":              sum(1 for o in outcomes if o.in_scope and o.engaged),
        "interviewed":          sum(1 for o in outcomes if o.in_scope and o.furthest_stage in ("interview", "offer")),
        "second_round":         sum(1 for o in outcomes if o.in_scope and o.rounds >= 2),
        "third_round":          sum(1 for o in outcomes if o.in_scope and o.rounds >= 3),
        "by_outcome":           by_outcome,
        "by_exclusion":         by_exclusion,
        "flagged":              {f: sum(1 for o in outcomes if f in o.flags) for f in ("no_receipt", "suggest_outside_inbox")},
        "coverage_start":       coverage.isoformat() if coverage else None,
        "response_window_days": (window or {}).get("days", RESPONSE_WINDOW_DAYS),
        "response_window":      window,
        "labels":               {"outcomes": OUTCOME_LABELS, "exclusions": EXCLUSIONS},
    }
