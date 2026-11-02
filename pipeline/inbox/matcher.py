"""
matcher.py — which applications could this email be about?

Deterministic and cheap. It doesn't decide the match — the classifier does — it narrows
the field so the model chooses from a short, relevant list and can only return a uuid
that exists. Signals, strongest first:

  thread    another email in this Gmail thread is already linked to an application
  sender    the company name appears in the sender's display name or domain
  subject   the company name appears in the subject
  body      the company name appears in the body
  role      the role title appears in the subject or body
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from pipeline.inbox.gmail_client import EmailMessage

SHORTLIST_SIZE = 8
STRONG_SCORE   = 40      # at or above: a confident deterministic hit

# Statuses an email can't sensibly move — still matchable, just listed last.
_CLOSED = ("archived", "excluded")

_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "llp", "plc", "gmbh", "ag", "sa", "bv",
    "co", "corp", "corporation", "company", "group", "holdings", "the", "uk", "global",
}

_WEIGHTS = {"thread": 100, "sender": 40, "subject": 30, "body": 15, "role": 15}

# Company names that are also everyday words or first names ("Friday", "Mercury", "Sam",
# "Summit") would otherwise match any email that happens to use the word. For those,
# a mention only counts with a hiring cue next to it ("at Mercury", "Mercury Talent",
# "Friday's team"), and a sender only counts if the display name is the company itself
# ("Mercury", "Mercury Recruiting") — not a person who shares the name.
# Heuristic: a one-word name of 3 letters or fewer, or one of these words.
_COMMON_WORDS = {
    "clay", "perk", "glean", "ramp", "bolt", "loom", "wise", "mint", "plum", "bloom",
    "spark", "scale", "lumen", "atlas", "nova", "flow", "base", "core", "level", "pulse",
    "sonar", "relay", "shift", "stack", "craft", "forge", "hive", "kite", "nest", "pine",
    "sage", "swift", "vault", "wave", "yield", "zest", "apollo", "lever", "gusto", "brick",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august", "september",
    "october", "november", "december", "wonderful", "kernel", "primer", "partly",
    "campfire", "pigment", "notion", "linear", "mercury", "stripe", "square", "anchor",
    "bridge", "beacon", "signal", "canvas", "compass", "harvest", "summit", "vector",
    "matter", "pilot", "rocket", "sprout", "origin", "element", "current", "method",
    "general", "modern", "united", "simple", "common", "public", "remote", "render",
    "segment", "attentive", "amplitude", "kraken", "benefit",
    "health", "people", "service", "services", "digital", "global", "capital", "partner",
    "partners", "studio", "labs", "cloud", "data", "systems", "solutions", "ventures",
    "insight", "insights", "journey", "motion", "impact", "balance", "harbor", "harbour",
    "orbit", "circle", "spring", "autumn", "winter", "summer", "morning", "evening",
    "update", "welcome", "hello", "thanks", "support", "customer", "success", "network",
    "connect", "united", "focus", "unity", "vista", "horizon", "frontier", "pioneer",
}
_CUE_WORDS = {
    "team", "talent", "recruiting", "recruitment", "recruiter", "careers", "career", "hiring",
    "people", "hr", "jobs", "interview", "interviews", "application", "applications",
}
_CUE_BEFORE = r"(?:at|join|joining|from|with|to)\s+"
_CUE_AFTER  = r"(?:['’]s)?\s+(?:" + "|".join(sorted(_CUE_WORDS)) + r")\b"


def is_ambiguous(normalised: str) -> bool:
    words = normalised.split()
    return len(words) == 1 and (len(words[0]) <= 3 or words[0] in _COMMON_WORDS)


def _cued_mention(text: str, name: str) -> bool:
    """name (as written, case-sensitive) with a hiring cue before or after it."""
    if not text or not name:
        return False
    n = re.escape(name)
    return bool(
        re.search(rf"(?i:{_CUE_BEFORE}){n}\b", text)
        or re.search(rf"\b{n}(?i:{_CUE_AFTER})", text)
    )


def _sender_is_company(sender_name: str, company: str) -> bool:
    """Display name is the company, optionally with cue words: 'Mercury', 'Mercury Talent Team'."""
    words = re.sub(r"[^a-z0-9&]+", " ", sender_name.lower()).split()
    return company in words and set(words) - {company} <= _CUE_WORDS | {"the", "at", "via", "by"}


@dataclass
class Candidate:
    app:     dict                       # applications row + applied_date
    score:   int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def uuid(self) -> str:
        return self.app["uuid"]


def normalise_company(name: str) -> str:
    words = re.sub(r"[^a-z0-9&]+", " ", (name or "").lower()).split()
    kept  = [w for w in words if w not in _SUFFIXES]
    return " ".join(kept or words)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _mentions(haystack: str, needle: str) -> bool:
    """Whole-word match of a normalised name in free text."""
    if not needle or len(needle) < 2:
        return False
    hay = " " + re.sub(r"[^a-z0-9&]+", " ", haystack.lower()) + " "
    return f" {needle} " in hay


def load_applications(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT a.id, a.uuid, a.company, a.role, a.status, a.created_at, a.updated_at,
                  (SELECT MAX(date) FROM application_dates d
                   WHERE d.application_id = a.id AND d.date_type = 'applied') AS applied_date
           FROM applications a
           ORDER BY COALESCE(a.updated_at, a.created_at) DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def thread_application(db: sqlite3.Connection, thread_id: str) -> Optional[str]:
    """applications.id already linked from this thread — accepted rows first, then pending."""
    row = db.execute(
        """SELECT application_id FROM inbox_emails
           WHERE gmail_thread_id = ? AND application_id IS NOT NULL
             AND review_status IN ('accepted', 'pending')
           ORDER BY review_status = 'accepted' DESC, received_at DESC
           LIMIT 1""",
        (thread_id,),
    ).fetchone()
    return row["application_id"] if row else None


def score(msg: EmailMessage, app: dict, thread_app_id: Optional[str] = None) -> Candidate:
    cand      = Candidate(app)
    raw_name  = (app.get("company") or "").strip()
    company   = normalise_company(raw_name)
    squashed  = _compact(company)
    ambiguous = is_ambiguous(company)

    def hit(reason: str) -> None:
        cand.score += _WEIGHTS[reason]
        cand.reasons.append(reason)

    def mentioned(text: str) -> bool:
        if ambiguous:
            # Case-sensitive, as the company writes it ("Mercury", not "mercury"), with a cue.
            display = raw_name.split("(")[0].strip()
            return _cued_mention(text, display)
        return _mentions(text, company)

    if thread_app_id and thread_app_id == app["id"]:
        hit("thread")
    domain_labels = msg.sender_domain.split(".")
    sender_named  = _sender_is_company(msg.sender_name, company) if ambiguous else _mentions(msg.sender_name, company)
    if company and (
        sender_named
        or (len(squashed) >= 3 and any(squashed == _compact(label) for label in domain_labels))
    ):
        hit("sender")
    if company and mentioned(msg.subject):
        hit("subject")
    elif company and (mentioned(msg.body) or mentioned(msg.snippet)):
        hit("body")
    role = re.sub(r"[^a-z0-9&]+", " ", (app.get("role") or "").lower()).strip()
    if role and len(role) >= 4 and (_mentions(msg.subject, role) or _mentions(msg.body, role)):
        hit("role")
    return cand


def shortlist(msg: EmailMessage, apps: list[dict], thread_app_id: Optional[str] = None) -> list[Candidate]:
    """Scored candidates, best first, at most SHORTLIST_SIZE. May be empty."""
    scored = [score(msg, a, thread_app_id) for a in apps]
    scored = [c for c in scored if c.score > 0]
    scored.sort(key=lambda c: (c.score, c.app["status"] not in _CLOSED), reverse=True)
    return scored[:SHORTLIST_SIZE]


def company_hit(msg: EmailMessage, apps: list[dict]) -> bool:
    """Cheap pre-triage signal: does the email name any tracked company?"""
    return any(score(msg, a).score >= _WEIGHTS["subject"] for a in apps)
