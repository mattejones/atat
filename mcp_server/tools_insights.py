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


@mcp.tool()
def get_prompt_signals(limit: int = 30) -> list[dict]:
    """
    Return distilled prompt signals from past answer feedback — short,
    actionable rules like "lead with a concrete example before the general
    principle" or "avoid generic phrases like 'passionate about'". Use these
    to bias tone/content before generating new question answers or CV copy.
    """
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
def get_exclusion_patterns() -> list[dict]:
    """
    Aggregate EX1-tier exclusion reasons and filter suggestions — recurring
    reasons a role was excluded, useful for deciding whether to even bother
    generating a CV for a borderline job ad.
    """
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT ef.filter_suggestion, COUNT(*) as count,
                   GROUP_CONCAT(ef.reason, ' | ') as sample_reasons
            FROM exclusion_feedback ef
            WHERE ef.filter_suggestion IS NOT NULL AND trim(ef.filter_suggestion) != ''
            GROUP BY ef.filter_suggestion
            ORDER BY count DESC
            """
        ).fetchall()
        return rows_to_list(rows)


@mcp.tool()
def get_success_stats(group_by: str = "tier") -> list[dict]:
    """
    Break down application outcomes by a grouping dimension.

    group_by: tier | work_arrangement | location | company
    Returns, per group value: total applications and counts at each status.
    """
    if group_by not in STAT_GROUP_COLUMNS:
        raise ValueError(f"Invalid group_by: {group_by!r}. Must be one of {sorted(STAT_GROUP_COLUMNS)}")

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

    return sorted(stats.values(), key=lambda s: -s["total"])


@mcp.tool()
def search_applications(query: str, limit: int = 20) -> list[dict]:
    """
    Search past applications by free text across company, role, notes,
    JD text, and generation reasoning. Case-insensitive substring match.
    Use this before generating a new CV to find "have I applied somewhere
    like this before, and what did I learn."
    """
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
