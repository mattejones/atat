"""
prefilter.py — is this email about a job application at all?

Two layers, so the per-email classifier only sees mail worth its time:

  hints    deterministic: sent by an applicant-tracking system, or names a company
           you've applied to. A hint skips triage and goes straight to classification.
  triage   everything else goes to the model in batches as sender/subject/snippet only
           (no body), prompt in prompts/email_triage.md.
"""

from __future__ import annotations

import json
import logging

from pipeline.config import PROMPTS_PATH
from pipeline.inbox import llm
from pipeline.inbox.gmail_client import EmailMessage
from pipeline.inbox.matcher import company_hit

log = logging.getLogger(__name__)

TRIAGE_BATCH = 25

# Applicant-tracking systems and recruiting platforms that send candidate mail on a
# company's behalf. Matched against the sender domain and its parents.
ATS_DOMAINS = {
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "ashbyhq.com",
    "myworkday.com", "myworkdayjobs.com", "workday.com", "smartrecruiters.com",
    "smartrecruitersmail.com", "icims.com", "workable.com", "workablemail.com",
    "teamtailor.com", "teamtailor-mail.com", "recruitee.com", "bamboohr.com",
    "jobvite.com", "successfactors.com", "successfactors.eu", "taleo.net", "breezy.hr",
    "pinpointhq.com", "personio.de", "personio.com", "jazzhr.com", "applytojob.com",
    "rippling.com", "dover.com", "gem.com", "hirebridge.com", "oraclecloud.com",
    "eightfold.ai", "beamery.com", "hibob.com", "comeet.co", "welcometothejungle.com",
    "otta.com", "calendly.com", "goodtime.io", "modernhire.com", "hackerrank.com",
    "codility.com", "testgorilla.com", "hirevue.com", "coderpad.io",
}


def is_ats_sender(domain: str) -> bool:
    parts = domain.lower().split(".")
    return any(".".join(parts[i:]) in ATS_DOMAINS for i in range(len(parts) - 1))


def hint(msg: EmailMessage, apps: list[dict]) -> str | None:
    """A reason this email is certainly worth classifying, or None."""
    if is_ats_sender(msg.sender_domain):
        return "ats_sender"
    if company_hit(msg, apps):
        return "company_named"
    return None


def build_triage_prompt(batch: list[EmailMessage]) -> tuple[str, str]:
    from pipeline.tailorer import load_text

    system = load_text(PROMPTS_PATH / "email_triage.md")
    lines  = [
        json.dumps({"i": i, "from": m.sender, "subject": m.subject, "snippet": m.snippet[:300]}, ensure_ascii=False)
        for i, m in enumerate(batch)
    ]
    user = "## EMAILS (one JSON object per line)\n" + "\n".join(lines)
    return system, user


def triage(messages: list[EmailMessage]) -> set[str]:
    """Ids of the messages the model judged job-application related."""
    related: set[str] = set()
    for start in range(0, len(messages), TRIAGE_BATCH):
        batch  = messages[start:start + TRIAGE_BATCH]
        system, user = build_triage_prompt(batch)
        data   = llm.call_json(system, user, max_tokens=512, label="triage")
        picked = data.get("job_related", [])
        if not isinstance(picked, list):
            raise RuntimeError(f"Inbox triage returned an unexpected shape: {data!r:.200}")
        for i in picked:
            if isinstance(i, int) and 0 <= i < len(batch):
                related.add(batch[i].id)
    return related
