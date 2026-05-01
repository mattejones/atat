"""
generate.py — Route for triggering CV generation from a pasted JD.
Writes to both the database and the filesystem output folder.
"""

import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.database import get_db
from pipeline.config import (
    OUTPUT_PATH, LLM_MODEL, LLM_PROVIDER,
    TEMPERATURE, THINKING_BUDGET, ENABLE_CACHING, RENDER_PDF,
)
from pipeline.tailorer import build_system_prompt, assemble_user_message, call_llm, extract_reasoning_and_cv

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    jd_text:          str
    company:          str            = "Unknown"
    role:             str            = "Unknown Role"
    source_url:       Optional[str]  = None
    tier:             Optional[str]  = None
    generation_notes: Optional[str]  = None


class GenerateResponse(BaseModel):
    app_id:      str
    cv_markdown: str
    has_reasoning: bool = False
    status:      str = "generated"


@router.post("", response_model=GenerateResponse)
def generate_cv(
    request: GenerateRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    if not request.jd_text.strip():
        raise HTTPException(status_code=400, detail="JD text cannot be empty")

    today        = date.today().isoformat()
    company_slug = request.company.lower().replace(" ", "-")[:30]
    role_slug    = request.role.lower().replace(" ", "-")[:40]
    app_id       = f"{today}_{company_slug}_{role_slug}"

    out_dir = OUTPUT_PATH / app_id
    if out_dir.exists() or db.execute(
        "SELECT 1 FROM applications WHERE id = ?", (app_id,)
    ).fetchone():
        suffix  = str(uuid.uuid4())[:6]
        app_id  = f"{app_id}_{suffix}"
        out_dir = OUTPUT_PATH / app_id

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "jd.txt").write_text(request.jd_text, encoding="utf-8")

    try:
        system     = build_system_prompt()
        user       = assemble_user_message(request.jd_text, request.generation_notes)
        raw_output = call_llm(system, user)
        reasoning, cv_markdown = extract_reasoning_and_cv(raw_output)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {e}")

    (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
    if reasoning:
        (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")

    meta = {
        "jd_file": "pasted", "model": LLM_MODEL, "provider": LLM_PROVIDER,
        "temperature": TEMPERATURE, "thinking_budget": THINKING_BUDGET,
        "caching": ENABLE_CACHING, "render_pdf": RENDER_PDF,
        "generated_at": today, "status": "generated",
        "has_reasoning": bool(reasoning),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    now = datetime.now().isoformat()
    db.execute(
        """INSERT INTO applications
           (id, company, role, source_url, jd_text, cv_markdown,
            tier, status, output_dir, has_pdf, model, provider,
            generation_notes, reasoning, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?, 0, ?, ?, ?, ?, ?, ?)""",
        (
            app_id, request.company, request.role, request.source_url,
            request.jd_text, cv_markdown, request.tier,
            str(out_dir), LLM_MODEL, LLM_PROVIDER,
            request.generation_notes, reasoning or None, now, now,
        )
    )
    db.execute(
        """INSERT INTO application_events
           (application_id, event_type, to_status, detail)
           VALUES (?, 'status_change', 'generated', 'CV generated')""",
        (app_id,)
    )

    return GenerateResponse(
        app_id=app_id,
        cv_markdown=cv_markdown,
        has_reasoning=bool(reasoning),
    )
