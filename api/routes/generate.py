"""
generate.py — Route for triggering CV generation from a pasted JD.
Writes to both the database and the filesystem output folder.

call_llm() now returns a structured dict via tool use — no text parsing.
reasoning is extracted from cv_data['reasoning'] before Markdown conversion.

Phase 2 addition: After generation, the CV is split into canonical sections.
Each section gets a section row and an initial report row in the database.
Section files are written to output/{app_id}/sections/{section_name}/{report_id}.md
cv.md is composed from section content via compose_cv_markdown().
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
from pipeline.tailorer import (
    build_system_prompt,
    assemble_user_message,
    call_llm,
)
from pipeline.sections import (
    split_cv_sections,
    compose_cv_markdown,
    write_section_file,
    SECTION_ORDER,
)

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    jd_text:          str
    company:          str           = "Unknown"
    role:             str           = "Unknown Role"
    source_url:       Optional[str] = None
    tier:             Optional[str] = None
    generation_notes: Optional[str] = None


class GenerateResponse(BaseModel):
    app_id:        str
    cv_markdown:   str
    has_reasoning: bool = False
    status:        str  = "generated"


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

    # ── LLM call ──────────────────────────────────────────────────────────────
    try:
        system  = build_system_prompt()
        user    = assemble_user_message(request.jd_text, request.generation_notes)
        cv_data = call_llm(system, user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {e}")

    reasoning = cv_data.pop("reasoning", "")
    name      = cv_data.get("name", "")
    contact   = cv_data.get("contact", {})

    # ── Section splitting ─────────────────────────────────────────────────────
    try:
        section_content = split_cv_sections(cv_data)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=f"Section splitting failed: {e}")

    # ── Compose full cv.md from section content ───────────────────────────────
    cv_markdown = compose_cv_markdown(name, contact, section_content)

    (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
    if reasoning:
        (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")

    meta = {
        "jd_file":         "pasted",
        "model":           LLM_MODEL,
        "provider":        LLM_PROVIDER,
        "temperature":     TEMPERATURE,
        "thinking_budget": THINKING_BUDGET,
        "caching":         ENABLE_CACHING,
        "render_pdf":      RENDER_PDF,
        "generated_at":    today,
        "status":          "generated",
        "has_reasoning":   bool(reasoning),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    now = datetime.now().isoformat()

    # ── Insert application row first (sections FK depends on this) ────────────
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
           VALUES (?, 'status_change', 'generated', 'CV generated and split into sections')""",
        (app_id,)
    )

    # ── Write section files and insert section/report rows ────────────────────
    for section_name in SECTION_ORDER:
        content = section_content.get(section_name, "")
        if not content:
            continue

        section_id = str(uuid.uuid4())
        report_id  = str(uuid.uuid4())

        file_path = write_section_file(out_dir, section_name, report_id, content)

        db.execute(
            """INSERT INTO sections
               (id, application_id, section_name, accepted_report_id, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?)""",
            (section_id, app_id, section_name, now, now),
        )

        db.execute(
            """INSERT INTO reports
               (id, application_id, section_id, parent_report_id,
                section_name, attempt, file_path, status,
                global_comment, formatted_prompt, escalated, created_at)
               VALUES (?, ?, ?, NULL, ?, 1, ?, 'pending', NULL, NULL, 0, ?)""",
            (report_id, app_id, section_id, section_name, str(file_path), now),
        )

    if RENDER_PDF:
        try:
            from pipeline.render import render_cv
            render_cv(out_dir / "cv.md", out_dir)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"PDF rendering failed: {e}")

    return GenerateResponse(
        app_id=app_id,
        cv_markdown=cv_markdown,
        has_reasoning=bool(reasoning),
    )
