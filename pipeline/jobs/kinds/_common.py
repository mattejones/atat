"""Helpers shared by more than one job kind."""

import json
import logging
import re
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from db.database import get_connection
from pipeline.config import OUTPUT_PATH

log = logging.getLogger(__name__)


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


def _auto_judge(app: dict, cv_markdown: str, coverage: bool = True) -> dict:
    """
    Run tiers 1 and 2 on every section of a freshly generated CV, then (if `coverage`) queue tier 3.
    Coverage needs a reviewed jd_spec, so the legacy single-phase intake passes coverage=False.

    Every step is non-fatal: the CV is already saved, and a judge or queue error must never
    lose it. Whatever could not run can still be run by hand (run_judges, run_coverage).
    """
    import dataclasses

    from pipeline.judges import orchestrator
    from pipeline.judges.context import build_context
    from pipeline.sections import read_section_file

    summary = {"judged": 0, "with_flags": 0, "errors": 0, "coverage_queued": False}
    try:
        with get_connection() as db:
            reports = [dict(r) for r in db.execute(
                "SELECT id, section_name, attempt, file_path FROM reports "
                "WHERE application_id = ? AND attempt = 1 ORDER BY created_at", (app["id"],),
            ).fetchall()]
        # One context for the whole CV: the library is read once and its derived sets are shared.
        base = build_context("", jd_text=app.get("jd_text") or "", cv_text=cv_markdown)
    except Exception:
        log.exception("Auto-judging could not start for %s (non-fatal)", app["id"])
        summary["errors"] += 1
        return summary

    for rep in reports:
        try:
            text       = read_section_file(Path(rep["file_path"]))
            evaluation = orchestrator.evaluate(
                text, rep["id"], dataclasses.replace(base, section=rep["section_name"]),
            )
            with get_connection() as db:
                result = orchestrator.persist(db, rep["id"], rep["attempt"], evaluation)
            summary["judged"] += 1
            summary["with_flags"] += 1 if result.total_flags else 0
        except Exception:
            log.exception("Auto-judging section %s failed (non-fatal)", rep["section_name"])
            summary["errors"] += 1

    if coverage:
        try:
            from pipeline.jobs import service
            service.create("run_coverage", app_uuid=app["uuid"], created_by="agent")
            summary["coverage_queued"] = True
        except Exception:
            log.exception("Could not queue coverage for %s (non-fatal)", app["id"])
            summary["errors"] += 1
    return summary
