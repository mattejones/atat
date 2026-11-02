"""
Inbox kind — gmail_sync: fetch new Gmail messages, classify them, queue them for review.

Queued by the scheduler (GMAIL_ENABLED), the Inbox page's "Sync now", and the
sync_inbox MCP tool — all through pipeline.inbox.sync.start_sync_job(), which returns
the running job instead of starting a second one. The sync writes only to inbox_emails;
applications change when an email is accepted on the Inbox page.
"""

from pipeline.jobs.base import JobContext, JobError, JobHandle, JobKind, Param, Prompt, register


@register
class GmailSync(JobKind):
    name        = "gmail_sync"
    target      = "none"
    description = "Read new Gmail messages (read-only), classify job-related ones and link them to applications for review in the Inbox."
    params      = {
        "max_messages": Param(int, help="Cap on messages fetched this run (default GMAIL_MAX_PER_SYNC)."),
        "classifier":   Param(str, help="api (classify with INBOX_MODEL) or agent (gather only; an agent classifies over MCP). Default INBOX_CLASSIFIER."),
    }

    def check(self, ctx: JobContext, params: dict) -> None:
        from pipeline.inbox.auth import connection_status

        status = connection_status()
        if not status["connected"]:
            raise JobError(status["detail"] or "Gmail isn't connected. Run: python -m pipeline.inbox.auth")
        if params.get("max_messages") is not None and params["max_messages"] < 1:
            raise JobError("max_messages must be at least 1")
        if params.get("classifier") not in (None, "api", "agent"):
            raise JobError("classifier must be 'api' or 'agent'")

    def preview(self, ctx: JobContext, params: dict) -> Prompt:
        from pipeline.config import PROMPTS_PATH
        from pipeline.tailorer import load_text

        return Prompt(
            system=load_text(PROMPTS_PATH / "email_classification.md"),
            user="(Built per email at run time: the email, plus the applications it could be about.)",
            notes=[
                "Emails are first triaged in batches with prompts/email_triage.md (sender, subject, snippet only).",
                "Nothing is applied to applications — results wait in the Inbox for review.",
            ],
        )

    def run(self, ctx: JobContext, params: dict, handle: JobHandle) -> dict:
        from pipeline.inbox.sync import run_sync

        return run_sync(
            checkpoint=handle.checkpoint,
            max_messages=params.get("max_messages"),
            classifier=params.get("classifier"),
        )
