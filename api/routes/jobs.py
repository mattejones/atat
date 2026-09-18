"""
jobs.py — generation jobs over HTTP: create, review drafts, submit, cancel, check.

The same jobs the MCP server's generative tools create (see pipeline/jobs). A job created
here is recorded as created/submitted by a human; one the agent drafted over MCP can be
reviewed, edited and submitted here by the applicant.

Endpoints:
  GET    /jobs                   — list, newest first (?status=draft is the review queue)
  GET    /jobs/kinds             — every job kind with its editable params
  GET    /jobs/{job_id}          — one job; drafts include a live prompt preview
  POST   /jobs                   — create a job, or a draft with "draft": true      (202)
  PATCH  /jobs/{job_id}          — edit a draft's params (optional expected_version)
  POST   /jobs/{job_id}/submit   — send a draft for generation                     (202)
  POST   /jobs/{job_id}/cancel   — discard a draft / withdraw or stop a job
  POST   /jobs/{job_id}/retry    — new job from a finished one's params            (202)

Every mutating call returns the job straight away; generation runs in the background.
Poll GET /jobs/{job_id} until status is succeeded | failed | cancelled.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.jobs import service
from pipeline.jobs.base import JobError, JobNotFound

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    kind:      str
    app_uuid:  Optional[str]  = None
    report_id: Optional[str]  = None
    params:    Optional[dict] = None
    draft:     bool           = False


class DraftUpdate(BaseModel):
    params:           dict
    expected_version: Optional[int] = None


class SubmitRequest(BaseModel):
    expected_version: Optional[int] = None


class RetryRequest(BaseModel):
    draft: bool = False


def call(fn, *args, **kwargs):
    """Run a service call, mapping job errors to HTTP: not found 404, anything else 409."""
    try:
        return fn(*args, **kwargs)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except JobError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("")
def list_jobs(
    status:   Optional[str] = None,
    app_uuid: Optional[str] = None,
    kind:     Optional[str] = None,
    limit:    int           = 50,
):
    return call(service.list_jobs, status=status, app_uuid=app_uuid, kind=kind, limit=limit)


@router.get("/kinds")
def list_kinds():
    return service.describe_kinds()


@router.get("/{job_id}")
def get_job(job_id: str, include_prompt: bool = False):
    return call(service.get, job_id, include_prompt=include_prompt)


@router.post("", status_code=202)
def create_job(body: JobCreate):
    return call(
        service.create, body.kind,
        app_uuid=body.app_uuid, report_id=body.report_id, params=body.params,
        draft=body.draft, created_by="human",
    )


@router.patch("/{job_id}")
def update_draft(job_id: str, body: DraftUpdate):
    return call(service.update_draft, job_id, body.params, expected_version=body.expected_version)


@router.post("/{job_id}/submit", status_code=202)
def submit_draft(job_id: str, body: Optional[SubmitRequest] = None):
    return call(
        service.submit, job_id, submitted_by="human",
        expected_version=body.expected_version if body else None,
    )


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    return call(service.cancel, job_id)


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: str, body: Optional[RetryRequest] = None):
    return call(service.retry, job_id, draft=bool(body and body.draft), created_by="human")
