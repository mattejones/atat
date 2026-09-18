"""Helpers shared by more than one job kind."""

import json
import re
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from pipeline.config import OUTPUT_PATH


def slugify(text: str, max_len: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:max_len].strip("-")


def new_application_slot(db: sqlite3.Connection, company: str, role: str) -> tuple[str, str, Path]:
    """Pick a unique (app_id, app_uuid, out_dir) for a new application and create the dir."""
    app_id   = f"{date.today().isoformat()}_{slugify(company, 30)}_{slugify(role, 40)}"
    app_uuid = str(uuid.uuid4())
    out_dir  = OUTPUT_PATH / app_id
    if out_dir.exists() or db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
        app_id  = f"{app_id}_{str(uuid.uuid4())[:6]}"
        out_dir = OUTPUT_PATH / app_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return app_id, app_uuid, out_dir


def insert_sections(db: sqlite3.Connection, app_id: str, out_dir: Path, section_content: dict, now: str) -> None:
    """Write each section's file and its section + initial pending report rows."""
    from pipeline.sections import SECTION_ORDER, write_section_file

    for section_name in SECTION_ORDER:
        content = section_content.get(section_name, "")
        if not content:
            continue
        section_id = str(uuid.uuid4())
        report_id  = str(uuid.uuid4())
        file_path  = write_section_file(out_dir, section_name, report_id, content)
        db.execute(
            """INSERT INTO sections (id, application_id, section_name, accepted_report_id, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?)""",
            (section_id, app_id, section_name, now, now),
        )
        db.execute(
            """INSERT INTO reports
               (id, application_id, section_id, parent_report_id, section_name, attempt,
                file_path, status, global_comment, formatted_prompt, escalated, created_at)
               VALUES (?, ?, ?, NULL, ?, 1, ?, 'pending', NULL, NULL, 0, ?)""",
            (report_id, app_id, section_id, section_name, str(file_path), now),
        )


def run_meta(**extra) -> str:
    from pipeline.config import (
        ENABLE_CACHING, LLM_MODEL, LLM_PROVIDER, RENDER_PDF, TEMPERATURE, THINKING_BUDGET,
    )
    return json.dumps({
        "model": LLM_MODEL, "provider": LLM_PROVIDER, "temperature": TEMPERATURE,
        "thinking_budget": THINKING_BUDGET, "caching": ENABLE_CACHING,
        "render_pdf": RENDER_PDF, "generated_at": date.today().isoformat(),
        "status": "generated", **extra,
    }, indent=2)


def load_spec(app: dict) -> Optional[dict]:
    raw = app.get("jd_spec")
    return json.loads(raw) if raw else None


def output_dir_for(app: dict) -> Path:
    return Path(app["output_dir"]) if app.get("output_dir") else OUTPUT_PATH / app["id"]


def now_iso() -> str:
    return datetime.now().isoformat()
