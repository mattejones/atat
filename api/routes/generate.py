"""
generate.py — Route for triggering CV generation from a pasted JD.

Single-phase intake (the submit_job job kind): creates the application, generates the
CV, splits it into sections and renders the PDF — in the background. Returns 202 with a
job at once; poll GET /jobs/{job_id}, whose result carries the new application's uuid.

The work itself lives in pipeline/jobs/kinds/intake.py, shared with the MCP server.
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from api.routes.jobs import call
from pipeline.jobs import service

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    jd_text:          str
    company:          str           = "Unknown"
    role:             str           = "Unknown Role"
    source_url:       Optional[str] = None
    tier:             Optional[str] = None
    generation_notes: Optional[str] = None
    draft:            bool          = False


@router.post("", status_code=202)
def generate_cv(request: GenerateRequest):
    params = request.model_dump(exclude={"draft"})
    return call(service.create, "submit_job", params=params, draft=request.draft, created_by="human")
