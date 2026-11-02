"""
statuses.py — the one Python list of application statuses.

Used by the API (api/routes/applications.py) and the MCP server
(mcp_server/tools_applications.py). The web UI's copy, with labels, colours and the
allowed transitions, is web/src/lib/statuses.ts — keep the two in step.

Status is the application's current state for day-to-day tracking. For what actually
happened to an application, use its outcome (pipeline/insights/outcomes.py), which is
derived from email and status history rather than this single value.
"""

VALID_STATUSES = frozenset({
    "analysed", "generated", "reviewing",                           # not yet sent
    "applied", "acknowledged", "interviewing", "case_study", "offered",
    "rejected", "ghosted", "excluded", "archived",
})

# Open applications the auto-ghost check may mark ghosted after a quiet spell.
GHOSTABLE_STATUSES = ("applied", "acknowledged", "interviewing", "case_study")
