"""
tools_jobs.py — check on, review, edit, submit, and cancel generation jobs.

Every generative tool (analyse_job, generate_cv, run_coverage, run_judges,
regenerate_section, generate_cover_letter, generate_answers, submit_job) returns
straight away with a job_id instead of blocking until a model call finishes. The work
runs in the background; these tools are how the agent comes back to it.

With draft=True, a generative tool stops before anything is sent to a model: the job
sits at status 'draft' with its inputs and a preview of the prompt, for the applicant
to review. update_draft edits it; submit_draft sends it.
"""

from typing import Optional

import anyio

from mcp_server.app import mcp
from pipeline.jobs import service

# Keep a wait comfortably inside typical MCP client request timeouts.
MAX_WAIT_S = 50


@mcp.tool()
def get_job(job_id: str, include_prompt: bool = False) -> dict:
    """
    Status of a generation job, plus its result once it has succeeded or its error if it
    failed. Cheap — call it whenever you're ready for the result; don't loop on it.

    Drafts include editable_params (what can be changed and how) and prompt_preview
    (the prompt as it would be sent now, cv-library content elided). Submitted jobs
    include the prompt as it was at submission. The system prompt is summarised unless
    include_prompt=true.
    """
    return service.get(job_id, include_prompt=include_prompt)


@mcp.tool()
def list_jobs(
    status: Optional[str] = None,
    app_uuid: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    """
    Generation jobs, newest first — summary rows only (use get_job for result/prompt).

    status: draft | queued | running | succeeded | failed | cancelled
    kind: analyse_job | submit_job | generate_cv | run_coverage | run_judges |
          regenerate_section | generate_cover_letter | generate_answers

    list_jobs(status="draft") is the review queue: requests waiting on the applicant.
    """
    return service.list_jobs(status=status, app_uuid=app_uuid, kind=kind, limit=limit)


@mcp.tool()
def update_draft(job_id: str, params: dict, expected_version: Optional[int] = None) -> dict:
    """
    Edit a draft's inputs before it is submitted. params is merged into the existing
    params; set a key to null to reset it to its default. See the draft's
    editable_params for what each kind accepts.

    Pass expected_version (the draft's `version`) to refuse the edit if someone else —
    e.g. the applicant in the web UI — changed the draft since you last read it.

    Returns the updated draft with a fresh prompt_preview.
    """
    return service.update_draft(job_id, params, expected_version=expected_version)


@mcp.tool()
def submit_draft(job_id: str, expected_version: Optional[int] = None) -> dict:
    """
    Send a reviewed draft for generation. Returns immediately (status 'queued'); check
    back later with get_job.

    Only do this once the applicant has seen the draft and said to go ahead. Pass
    expected_version (the `version` they reviewed) so a later edit can't slip through
    unseen.
    """
    return service.submit(job_id, submitted_by="agent", expected_version=expected_version)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """
    Discard a draft, withdraw a queued job, or stop a running one.

    A running job's model call can't be interrupted: it finishes and its tokens are
    spent, but the output is thrown away rather than saved. The job then shows
    status 'cancelled'.
    """
    return service.cancel(job_id)


@mcp.tool()
def retry_job(job_id: str, draft: bool = False) -> dict:
    """
    Start a new job with the same kind, target and inputs as a failed or cancelled one.
    draft=True creates it as a draft so the inputs can be adjusted before it runs.
    Returns the NEW job (a new job_id).
    """
    return service.retry(job_id, draft=draft, created_by="agent")


@mcp.tool()
async def wait_for_job(job_id: str, timeout_s: int = 30) -> dict:
    """
    Wait for a queued or running job to finish, up to timeout_s seconds (max 50), then
    return it as get_job would. Returns early the moment the job finishes.

    Only use this when you have nothing else to do in the meantime. If there is other
    work — the next application, drafting the cover letter — do that and call get_job
    later instead. If it times out the job is still going; the returned status says so,
    and nothing is lost by calling again.

    A draft returns immediately: it won't progress until someone submits it.
    """
    timeout = max(0, min(int(timeout_s), MAX_WAIT_S))
    # Sync DB/threading wait, run off the event loop so other tool calls aren't blocked.
    return await anyio.to_thread.run_sync(service.wait, job_id, timeout)
