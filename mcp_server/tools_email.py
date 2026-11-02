"""
tools_email.py — the Gmail inbox review queue over MCP.

sync_inbox queues a gmail_sync job (read-only Gmail fetch + classification); the other
tools read and act on the resulting queue. See the "Inbox" section of get_guide().
Accepting is the only action that changes an application — do it on the applicant's say-so.
"""

from typing import Optional

from mcp_server.app import mcp
from pipeline.inbox import review


@mcp.tool()
def sync_inbox(max_messages: Optional[int] = None, classifier: Optional[str] = None) -> dict:
    """
    Fetch new Gmail messages. Returns a job (see get_job / wait_for_job); if a sync is
    already queued or running, returns that one. Fails if Gmail isn't connected — the
    applicant runs `python -m pipeline.inbox.auth`.

    classifier (default: the INBOX_CLASSIFIER setting):
      "api"   ATAT classifies with its own model during the sync (API tokens); job-related
              emails land straight in the review queue.
      "agent" ATAT only gathers mail (no model calls) as 'untriaged' — then YOU classify it
              with get_untriaged_emails + classify_inbox_emails.

    The result counts fetched / queued / ignored / failed / untriaged / remaining messages,
    plus llm_calls and tokens used.
    """
    from pipeline.inbox.sync import start_sync_job

    return start_sync_job(created_by="agent", max_messages=max_messages, classifier=classifier)


@mcp.tool()
def get_untriaged_emails(limit: int = 25, include_body: bool = True, include_instructions: bool = True) -> dict:
    """
    A batch of fetched emails waiting for you to classify (agent mode), hinted ones first.

    Each email has email_id, from, subject, received_at, hint (ats_sender | company_named |
    null — null is usually not job-related), snippet, body (cleaned, trimmed) and
    candidates: the applications the matcher thinks it could be about (uuid, company,
    role, status, applied_date, matched_on).

    instructions holds the classification rules and the verdict format — read them on
    the first call, then pass include_instructions=false to save tokens. remaining is how
    many are left after this batch. Send verdicts back with classify_inbox_emails.
    limit: up to 50.
    """
    from pipeline.inbox import agent

    return agent.untriaged(limit=limit, include_body=include_body, include_instructions=include_instructions)


@mcp.tool()
def classify_inbox_emails(results: list[dict]) -> dict:
    """
    Submit your verdicts for emails from get_untriaged_emails (or to re-classify a pending
    one). Each item:
      {"email_id": int, "classification": acknowledgement | rejection | interview_request |
       assessment | offer | progress_update | recruiter_outreach | other_job | not_job,
       "application_uuid": str | null, "confidence": 0-1, "evidence": "verbatim quote",
       "interview_at": ISO datetime | null, "summary": "one sentence"}

    not_job (and other_job with no application) are filed as ignored and their body is
    discarded. The rest join the review queue with a proposed status/date change that never
    moves an application backwards. Nothing is applied until the applicant accepts.
    Invalid items are returned in errors; the rest still apply.
    """
    from pipeline.inbox import agent

    return agent.classify(results)


@mcp.tool()
def list_inbox(
    review_status: Optional[str] = "pending",
    app_uuid: Optional[str] = None,
    limit: int = 50,
    classification: Optional[str] = None,
) -> dict:
    """
    Classified emails, newest first, with the Gmail connection/sync status.

    review_status: pending (the review queue, default) | untriaged (waiting for an agent
                   to classify — use get_untriaged_emails) | accepted | dismissed |
                   auto_ignored | all
    app_uuid: only emails linked to this application.
    classification: only emails of this type (e.g. other_job).

    Each email has: id, sender, subject, received_at, classification, confidence,
    app_uuid/app_company/app_role/app_status (the linked application, may be null),
    match_reason, evidence, proposed_status, proposed_date_type, proposed_date,
    snippet and body_excerpt.
    """
    from pipeline.inbox.auth import connection_status
    from pipeline.inbox.sync import sync_state

    status = None if review_status == "all" else review_status
    return {
        "gmail":  {**connection_status(), "sync": sync_state()},
        "emails": review.list_emails(review_status=status, application_uuid=app_uuid, limit=limit, classification=classification),
    }


@mcp.tool()
def accept_inbox_email(
    email_id: int,
    application_uuid: Optional[str] = None,
    apply_status: bool = True,
    apply_date: bool = True,
) -> dict:
    """
    Accept an inbox email: link it to its application and apply the proposed status and
    date (each can be turned off). Pass application_uuid to link it to a different
    application — the proposal is recomputed for that one. A status that would now go
    backwards is skipped and reported in applied.skipped. Only on the applicant's say-so.
    """
    return review.accept(email_id, application_uuid=application_uuid, apply_status=apply_status, apply_date=apply_date)


@mcp.tool()
def relink_inbox_email(email_id: int, application_uuid: Optional[str] = None) -> dict:
    """
    Link an inbox email to a different application (or none, with application_uuid
    omitted), recomputing its proposed change. Doesn't apply anything; the email stays
    pending. Also rescues a dismissed or auto-ignored email back into the queue.
    """
    return review.relink(email_id, application_uuid)


@mcp.tool()
def dismiss_inbox_email(email_id: int) -> dict:
    """Remove an email from the review queue without changing any application."""
    return review.dismiss(email_id)


@mcp.tool()
def reclassify_accepted_emails(results: list[dict]) -> dict:
    """
    Correct the classification of emails that were already accepted — e.g. to move an
    old other_job email that was really a shortlist or a request for information to
    progress_update. Find candidates with list_inbox(review_status="accepted",
    classification="other_job"). Each item: {"email_id": int, "classification": str,
    "evidence"?: str, "summary"?: str}.

    Only the classification changes (the previous one is kept); the application's status
    and dates are not touched. Outcome reports pick the change up immediately.
    """
    from pipeline.inbox import agent

    return agent.reclassify_accepted(results)
