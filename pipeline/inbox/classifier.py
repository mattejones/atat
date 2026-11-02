"""
classifier.py — per-email model call: what kind of email is this, and which application?

The model sees the email (sender, subject, trimmed body) and the candidate applications
from the matcher, and may only answer with one of those uuids or null — so it can't
invent a link. When the matcher found no confident candidate, a compact list of other
open applications is added so a clearly-named company can still be matched.

System prompt: prompts/email_classification.md (editable on the Prompts page).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from pipeline.config import INBOX_MODEL, PROMPTS_PATH
from pipeline.inbox import llm
from pipeline.inbox.gmail_client import EmailMessage
from pipeline.inbox.matcher import Candidate
from pipeline.inbox.proposals import CLASSIFICATIONS

OTHERS_LIMIT = 25        # fallback list size when nothing matched deterministically
BODY_LIMIT   = 2500      # characters of cleaned body sent to the model
_OPEN_STATUSES = ("applied", "acknowledged", "interviewing", "case_study", "offered", "ghosted", "generated", "reviewing", "analysed")


@dataclass
class Classification:
    classification:   str
    application_uuid: Optional[str]
    confidence:       float
    evidence:         str = ""
    summary:          str = ""
    interview_at:     Optional[str] = None
    extracted:        dict = field(default_factory=dict)
    model:            str = INBOX_MODEL


def _app_line(app: dict, reasons: Optional[list[str]] = None) -> str:
    line = (
        f"- {app['uuid']} | {app.get('company')} — {app.get('role')} | "
        f"{app.get('status')}, applied {app.get('applied_date') or '?'}"
    )
    return line + (f" | matched_on={'+'.join(reasons)}" if reasons else "")


def other_open_apps(apps: list[dict], candidates: list[Candidate]) -> list[dict]:
    """
    The most recently active open applications, only when the matcher found nothing at
    all — so the model can still link an email that names a company in a way the
    matcher missed. Costs a few hundred tokens, so it's skipped whenever there's a lead.
    """
    if candidates:
        return []
    return [a for a in apps if a.get("status") in _OPEN_STATUSES][:OTHERS_LIMIT]


# ── Body cleanup ──────────────────────────────────────────────────────────────
# Quoted history, signatures and footers are most of a typical email's length and none
# of its meaning. Cut them before sending the body to the model.

_QUOTE_START = re.compile(
    r"^(?:On\b.{0,200}\bwrote:\s*$"                   # Gmail / Apple reply header
    r"|-{2,}\s*Original Message\s*-{2,}"              # Outlook
    r"|_{10,}\s*$"                                    # Outlook separator line
    r"|From:\s.+\n(?:.*\n){0,3}?(?:Sent|Date):\s)",   # forwarded / Outlook header block
    re.IGNORECASE | re.MULTILINE,
)
_SIGNATURE = re.compile(r"^--\s*$", re.MULTILINE)
_FOOTER    = re.compile(
    r"^.*\b(?:unsubscribe|privacy (?:policy|notice)|you (?:are )?receiv(?:ed|ing) this"
    r"|this (?:e-?mail|message) was sent|manage (?:your )?(?:email )?preferences"
    r"|confidential(?:ity)? notice|do not reply to this)\b",
    re.IGNORECASE | re.MULTILINE,
)
_KEEP_BEFORE_FOOTER = 300   # never cut a footer match this close to the top


def clean_body(text: str, limit: int = BODY_LIMIT) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    for pattern in (_QUOTE_START, _SIGNATURE):
        m = pattern.search(text)
        if m and m.start() > 0:
            text = text[:m.start()]
    m = next((m for m in _FOOTER.finditer(text) if m.start() >= _KEEP_BEFORE_FOOTER), None)
    if m:
        text = text[:m.start()]
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def build_prompt(msg: EmailMessage, candidates: list[Candidate], others: list[dict]) -> tuple[str, str]:
    from pipeline.tailorer import load_text

    system = load_text(PROMPTS_PATH / "email_classification.md")
    cand_block  = "\n".join(_app_line(c.app, c.reasons) for c in candidates) or "(none matched on company, sender or thread)"
    other_block = "\n".join(_app_line(a) for a in others)
    user = f"""## EMAIL
From: {msg.sender}
Date: {msg.received_at}
Subject: {msg.subject}

{clean_body(msg.body) or msg.snippet}

---

## CANDIDATE APPLICATIONS (uuid | company — role | status, applied date | why it matched)
{cand_block}
"""
    if other_block:
        user += f"""
## OTHER OPEN APPLICATIONS (no deterministic match — pick one only if the email clearly names it)
{other_block}
"""
    return system, user


def _parse(data: dict, allowed_uuids: set[str]) -> Classification:
    cls = data.get("classification")
    if cls not in CLASSIFICATIONS:
        raise RuntimeError(f"Inbox classifier returned unknown classification {cls!r}")
    uuid = data.get("application_uuid")
    if uuid not in allowed_uuids:
        uuid = None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    interview_at = data.get("interview_at") or None
    if interview_at is not None and not isinstance(interview_at, str):
        interview_at = None
    extracted = {k: data.get(k) for k in ("company", "role", "interview_at", "summary") if data.get(k)}
    return Classification(
        classification   = cls,
        application_uuid = uuid,
        confidence       = conf,
        evidence         = str(data.get("evidence") or "")[:500],
        summary          = str(data.get("summary") or "")[:300],
        interview_at     = interview_at,
        extracted        = extracted,
    )


def classify(msg: EmailMessage, candidates: list[Candidate], others: list[dict]) -> Classification:
    system, user = build_prompt(msg, candidates, others)
    data = llm.call_json(system, user, max_tokens=600, label="classify")
    allowed = {c.uuid for c in candidates} | {a["uuid"] for a in others}
    return _parse(data, allowed)


def to_json(c: Classification) -> str:
    return json.dumps(c.extracted, ensure_ascii=False)
