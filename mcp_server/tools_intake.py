"""
tools_intake.py — turning a job ad into an application.

scrape_job_url mirrors api/routes/scrape.py (reuses its HTML-to-text
extraction directly). If it fails or the text looks too thin, the calling
agent should fall back to its own browser tool and pass the extracted text
straight to submit_job — this module deliberately has no browser fallback
of its own.

submit_job mirrors api/routes/generate.py: runs the tailoring LLM call,
splits the CV into sections, writes section/report rows, composes cv.md,
and (if RENDER_PDF) renders the PDF — all in one step, same as the web UI's
"Generate" button.
"""

import json
import re
import uuid
from datetime import date, datetime
from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import enrich_app, get_connection, row_to_dict
from pipeline.config import (
    ENABLE_CACHING, LLM_MODEL, LLM_PROVIDER, OUTPUT_PATH, RENDER_PDF,
    TEMPERATURE, THINKING_BUDGET,
)


MAX_LIST_LIMIT = 100


def _slugify(text: str, max_len: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:max_len].strip("-")


@mcp.tool()
def find_by_source_url(url: str) -> Optional[dict]:
    """
    Look up an application by exact source_url match. Returns None if none
    exists yet.

    Call this before submit_job whenever a job ad's URL might have been seen
    before — e.g. when working a queue (like Todoist) that could hand you the
    same URL twice across restarts. Prefer this over search_applications for
    dedupe checks: search_applications is a fuzzy substring match and can miss
    or over-match; this is an exact match on the one field that uniquely
    identifies a job ad.
    """
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM applications WHERE source_url = ? ORDER BY created_at DESC LIMIT 1",
            (url,),
        ).fetchone()
        return enrich_app(row_to_dict(row)) if row else None


@mcp.tool()
def get_recent_notes(limit: int = 10) -> list[dict]:
    """
    Return the most recent distinct generation_notes across all applications
    — the "notes added when generating a CV" field, not the freeform
    post-hoc `notes` field (get that per-application via get_application).

    Call this before submit_job to see what guidance you (or a past session)
    gave on similar past generations — e.g. "always downplay the people-
    management angle for IC roles" — and fold anything still relevant into
    the new call's generation_notes. Deduplicated by exact text; each entry
    also shows which company/role it was used for.
    """
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT generation_notes AS text, company, role
            FROM applications
            WHERE generation_notes IS NOT NULL
              AND trim(generation_notes) != ''
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit * 3,),  # over-fetch — dedup below can collapse multiple rows into one
        ).fetchall()

    seen: set = set()
    result: list = []
    for row in rows:
        text = row["text"].strip()
        if text not in seen:
            seen.add(text)
            result.append({"text": text, "company": row["company"], "role": row["role"]})
        if len(result) >= limit:
            break
    return result


@mcp.tool()
def scrape_job_url(url: str) -> dict:
    """
    Fetch a URL and extract clean job-description text from the page.

    Best-effort: strips scripts/nav/footer and collapses whitespace via regex,
    no JS rendering. Raises if the page can't be fetched or yields under 100
    chars of text (paywalled/JS-rendered boards will fail this) — in that case
    use a browser tool to extract the JD text yourself and pass it to submit_job.
    """
    import httpx
    from api.routes.scrape import _extract_text

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Could not fetch URL (HTTP {e.response.status_code}): {url}")
    except httpx.RequestError as e:
        raise ValueError(f"Could not reach URL: {url}. Error: {e}")

    title, text = _extract_text(response.text)
    if len(text.strip()) < 100:
        raise ValueError(
            "Could not extract meaningful text from this URL (likely JS-rendered or "
            "behind a login wall) — use a browser tool to pull the JD text manually, "
            "then pass it to submit_job."
        )

    return {"url": url, "title": title, "text": text.strip()}


@mcp.tool()
def submit_job(
    jd_text: str,
    company: str = "Unknown",
    role: str = "Unknown Role",
    source_url: Optional[str] = None,
    tier: Optional[str] = None,
    generation_notes: Optional[str] = None,
) -> dict:
    """
    Generate a tailored CV from a job description — creates the application
    record, calls the tailoring LLM, splits the CV into sections, writes
    section/report rows for the judge pipeline, composes cv.md, and renders
    a PDF if RENDER_PDF is enabled.

    generation_notes: freeform guidance for this generation (e.g. "emphasize
    the platform migration work, downplay people management"). Call
    get_recent_notes() first to see what guidance was used on similar past
    applications before writing this.

    Returns the new application's uuid, cv_markdown, and whether reasoning
    was captured. Follow up with list_sections/get_report/run_judges to
    review before accepting, or accept_report per section once satisfied.
    """
    from mcp_server.helpers import get_connection
    from pipeline.sections import SECTION_ORDER, compose_cv_markdown, split_cv_sections, write_section_file
    from pipeline.tailorer import assemble_user_message, build_system_prompt, call_llm

    if not jd_text.strip():
        raise ValueError("jd_text cannot be empty")

    with get_connection() as db:
        today = date.today().isoformat()
        company_slug = _slugify(company, 30)
        role_slug = _slugify(role, 40)
        app_id = f"{today}_{company_slug}_{role_slug}"
        app_uuid = str(uuid.uuid4())

        out_dir = OUTPUT_PATH / app_id
        if out_dir.exists() or db.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
            suffix = str(uuid.uuid4())[:6]
            app_id = f"{app_id}_{suffix}"
            out_dir = OUTPUT_PATH / app_id

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "jd.txt").write_text(jd_text, encoding="utf-8")

        try:
            system = build_system_prompt()
            user = assemble_user_message(jd_text, generation_notes)
            cv_data = call_llm(system, user)
        except Exception as e:
            raise ValueError(f"LLM generation failed: {e}")

        reasoning = cv_data.pop("reasoning", "")
        name = cv_data.get("name", "")
        contact = cv_data.get("contact", {})

        try:
            section_content = split_cv_sections(cv_data)
        except ValueError as e:
            raise ValueError(f"Section splitting failed: {e}")

        cv_markdown = compose_cv_markdown(name, contact, section_content)
        (out_dir / "cv.md").write_text(cv_markdown, encoding="utf-8")
        if reasoning:
            (out_dir / "reasoning.md").write_text(reasoning, encoding="utf-8")

        meta = {
            "jd_file": "mcp", "model": LLM_MODEL, "provider": LLM_PROVIDER,
            "temperature": TEMPERATURE, "thinking_budget": THINKING_BUDGET,
            "caching": ENABLE_CACHING, "render_pdf": RENDER_PDF,
            "generated_at": today, "status": "generated", "has_reasoning": bool(reasoning),
        }
        (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        now = datetime.now().isoformat()
        db.execute(
            """INSERT INTO applications
               (id, uuid, company, role, source_url, jd_text, cv_markdown,
                tier, status, output_dir, has_pdf, model, provider,
                generation_notes, reasoning, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?, 0, ?, ?, ?, ?, ?, ?)""",
            (
                app_id, app_uuid, company, role, source_url, jd_text, cv_markdown, tier,
                str(out_dir), LLM_MODEL, LLM_PROVIDER, generation_notes, reasoning or None, now, now,
            ),
        )
        db.execute(
            """INSERT INTO application_events (application_id, event_type, to_status, detail)
               VALUES (?, 'status_change', 'generated', 'CV generated and split into sections via MCP')""",
            (app_id,),
        )

        for section_name in SECTION_ORDER:
            content = section_content.get(section_name, "")
            if not content:
                continue
            section_id = str(uuid.uuid4())
            report_id = str(uuid.uuid4())
            file_path = write_section_file(out_dir, section_name, report_id, content)
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

        if RENDER_PDF:
            try:
                from pipeline.render import render_cv
                render_cv(out_dir / "cv.md", out_dir)
            except Exception:
                pass  # non-fatal — PDF can be re-rendered later

        return {
            "uuid": app_uuid,
            "app_id": app_id,
            "cv_markdown": cv_markdown,
            "has_reasoning": bool(reasoning),
            "status": "generated",
        }
