"""
tools_insights.py — pattern-mining MCP tools.

No route equivalents — these mine tables that already exist but that nothing
in the app currently aggregates: answer_feedback's distilled prompt signals,
exclusion_feedback's filter suggestions, and judge flag history across every
report ever generated. The point is to let an agent ground new generations in
what has actually worked or failed before, rather than generating blind.
"""

from typing import Optional

from mcp_server.app import mcp
from mcp_server.helpers import get_connection, rows_to_list

STAT_GROUP_COLUMNS = {"tier", "work_arrangement", "location", "company"}

# Default statuses for get_reference_cvs — only ones with actual evidence a
# human screened the CV favorably. "applied"/"acknowledged" mean "submitted,
# nothing else known" — not the same signal, and were previously lumped in
# by default, which let weak-signal CVs crowd out genuinely strong ones.
# Ordinal strength for ranking results: higher = stronger evidence of success.
REFERENCE_STATUS_RANK = {
    "offered": 4,
    "case_study": 3,
    "interviewing": 2,
    "acknowledged": 1,
    "applied": 0,
}
DEFAULT_REFERENCE_STATUSES = ["interviewing", "case_study", "offered"]
MAX_REFERENCE_CVS = 10

# All of these tools return rows to an LLM's context, not to a UI with
# virtualized scrolling — every list-shaped tool here caps how many rows it
# will ever return, regardless of what a caller asks for.
MAX_LIST_LIMIT = 100


@mcp.tool()
def get_prompt_signals(limit: int = 30) -> list[dict]:
    """
    Return distilled prompt signals from past answer feedback — short,
    actionable rules like "lead with a concrete example before the general
    principle" or "avoid generic phrases like 'passionate about'". Use these
    to bias tone/content before generating new question answers or CV copy.

    limit: capped at 100.
    """
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT af.rating, af.processed_signal, aq.question_text,
                   a.company, a.role, af.created_at
            FROM answer_feedback af
            JOIN application_answers aa ON af.answer_id = aa.id
            JOIN application_questions aq ON aa.question_id = aq.id
            JOIN applications a ON aa.application_id = a.id
            WHERE af.processed = 1 AND af.processed_signal IS NOT NULL
            ORDER BY af.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def get_exclusion_patterns(limit: int = 50) -> list[dict]:
    """
    Aggregate EX1-tier exclusion reasons and filter suggestions — recurring
    reasons a role was excluded, useful for deciding whether to even bother
    generating a CV for a borderline job ad. Most common first.

    limit: capped at 100.
    """
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT ef.filter_suggestion, COUNT(*) as count,
                   GROUP_CONCAT(ef.reason, ' | ') as sample_reasons
            FROM exclusion_feedback ef
            WHERE ef.filter_suggestion IS NOT NULL AND trim(ef.filter_suggestion) != ''
            GROUP BY ef.filter_suggestion
            ORDER BY count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def get_success_stats(group_by: str = "tier", limit: int = 50) -> list[dict]:
    """
    Break down application outcomes by a grouping dimension, largest group
    first.

    group_by: tier | work_arrangement | location | company. Note: `company`
    and `location` can have as many distinct values as you have
    applications — `limit` (default 50, capped at 100) caps how many
    groups come back, not how many applications are counted.
    Returns, per group value: total applications and counts at each status.
    """
    if group_by not in STAT_GROUP_COLUMNS:
        raise ValueError(f"Invalid group_by: {group_by!r}. Must be one of {sorted(STAT_GROUP_COLUMNS)}")
    limit = max(1, min(limit, MAX_LIST_LIMIT))

    with get_connection() as db:
        rows = db.execute(
            f"SELECT {group_by} as group_value, status, COUNT(*) as count "
            f"FROM applications WHERE {group_by} IS NOT NULL "
            f"GROUP BY {group_by}, status"
        ).fetchall()

    stats: dict = {}
    for row in rows:
        key = row["group_value"] or "(unset)"
        stats.setdefault(key, {"group_value": key, "total": 0})
        stats[key][row["status"]] = row["count"]
        stats[key]["total"] += row["count"]

    return sorted(stats.values(), key=lambda s: -s["total"])[:limit]


@mcp.tool()
def get_reference_cvs(
    statuses: Optional[list[str]] = None,
    limit: int = 5,
    tier: Optional[str] = None,
) -> list[dict]:
    """
    Return full CV content for applications where a human actually screened
    it favorably — real evidence of what works, not just what got sent. Use
    these as few-shot examples before generating a new CV, rather than
    starting from the cv-library alone every time: real accepted phrasing,
    structure, and emphasis beats reconstructing it from scratch.

    statuses: default is interviewing/case_study/offered only — actual
    screening signal. "applied"/"acknowledged" mean "submitted, nothing
    else known" and are deliberately excluded by default; pass them
    explicitly (e.g. statuses=["applied","interviewing","offered"]) if you
    want that weaker-signal fallback too, e.g. because there aren't enough
    interviewing+ examples yet for this persona/tier.
    tier: optionally restrict to one tier (T1/T2/T3/EX1) — useful since a
    T1 target role probably wants T1-caliber examples, not T3 ones.
    limit: capped at 10 — this returns full cv_markdown per application, so
    keep it tight. Use list_applications/search_applications first if you
    need to browse and pick specific ones instead.

    Ranked strongest outcome first (offered > case_study > interviewing >
    acknowledged > applied), most recent first within the same outcome —
    not just most recent overall, so a strong result from months ago still
    outranks a merely-applied one from yesterday.
    Returns [{uuid, company, role, tier, status, cv_markdown}, ...].
    """
    limit = max(1, min(limit, MAX_REFERENCE_CVS))
    statuses = statuses or DEFAULT_REFERENCE_STATUSES
    placeholders = ",".join("?" * len(statuses))
    params: list = list(statuses)

    tier_clause = ""
    if tier:
        tier_clause = " AND tier = ?"
        params.append(tier)

    with get_connection() as db:
        rows = db.execute(
            f"""
            SELECT uuid, company, role, tier, status, cv_markdown, created_at
            FROM applications
            WHERE status IN ({placeholders}) {tier_clause}
              AND cv_markdown IS NOT NULL AND trim(cv_markdown) != ''
            """,
            params,
        ).fetchall()

    # Strongest outcome first, then most recent within the same outcome —
    # both components sort ascending naturally in the same direction, so a
    # single reverse=True on the tuple key does the right thing for both.
    ranked = sorted(
        rows_to_list(rows),
        key=lambda r: (REFERENCE_STATUS_RANK.get(r["status"], -1), r["created_at"]),
        reverse=True,
    )
    for r in ranked:
        r.pop("created_at", None)
    return ranked[:limit]


@mcp.tool()
def search_applications(query: str, limit: int = 20) -> list[dict]:
    """
    Search past applications by free text across company, role, notes,
    JD text, and generation reasoning. Case-insensitive substring match.
    Use this before generating a new CV to find "have I applied somewhere
    like this before, and what did I learn." Doesn't return jd_text/
    reasoning themselves (only searches them) — call get_application(uuid)
    for full content on a specific match.

    limit: capped at 100.
    """
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    like = f"%{query}%"
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT id, uuid, company, role, status, tier, notes, created_at
            FROM applications
            WHERE company LIKE ? OR role LIKE ? OR notes LIKE ?
               OR jd_text LIKE ? OR reasoning LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (like, like, like, like, like, limit),
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def get_flag_history(section_name: Optional[str] = None) -> list[dict]:
    """
    Aggregate judge flags raised across every section report ever generated —
    which flag types recur, and how often. Optionally scope to one section
    (profile | experience | skills | education | certifications). Use this to
    spot systemic prompt problems, e.g. "ai_texture flags keep firing on the
    experience section" — signal for tuning the generation prompt, not just
    this one retry.
    """
    query = """
        SELECT f.type, r.section_name, COUNT(*) as count,
               SUM(CASE WHEN f.status = 'active' THEN 1 ELSE 0 END) as still_active
        FROM flags f
        JOIN evaluations e ON f.evaluation_id = e.id
        JOIN reports r ON e.report_id = r.id
    """
    params: tuple = ()
    if section_name:
        query += " WHERE r.section_name = ?"
        params = (section_name,)
    query += " GROUP BY f.type, r.section_name ORDER BY count DESC"

    with get_connection() as db:
        rows = db.execute(query, params).fetchall()
        return rows_to_list(rows)
