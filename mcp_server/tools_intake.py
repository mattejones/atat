"""
tools_intake.py — turning a job ad into an application.

scrape_job_url mirrors api/routes/scrape.py (reuses its HTML-to-text
extraction directly). If it fails or the text looks too thin, the calling
agent should fall back to its own browser tool and pass the extracted text
straight to submit_job — this module deliberately has no browser fallback
of its own.

submit_job is the legacy single-phase intake, now run as a background job
(pipeline/jobs/kinds/intake.py). analyse_job + generate_cv in tools_spec is the
preferred path.
"""

from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import enrich_app, get_connection, row_to_dict


MAX_LIST_LIMIT = 100


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
    draft: bool = False,
) -> dict:
    """
    LEGACY single-phase intake — prefer analyse_job then generate_cv, which puts a
    reviewed brief in front of the generation. This path generates with no jd_spec.

    Creates the application record, calls the tailoring LLM, splits the CV into
    sections, writes section/report rows for the judge pipeline, composes cv.md, and
    renders a PDF if RENDER_PDF is enabled.

    ASYNC: returns immediately with a job_id; the result (the new application's uuid,
    cv_markdown) arrives via get_job. draft=True returns a draft for review instead.

    generation_notes: freeform guidance for this generation (e.g. "emphasize
    the platform migration work, downplay people management"). Call
    get_recent_notes() first to see what guidance was used on similar past
    applications before writing this.
    """
    from pipeline.jobs import service
    return service.create(
        "submit_job",
        params={
            "jd_text": jd_text, "company": company, "role": role, "source_url": source_url,
            "tier": tier, "generation_notes": generation_notes,
        },
        draft=draft,
    )
