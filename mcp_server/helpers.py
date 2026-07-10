"""
helpers.py — shared DB helpers for ATAT MCP tools.

Mirrors the uuid-lookup pattern used throughout api/routes/*.py — applications
are addressed by their uuid column everywhere outside of FK/filesystem contexts.
The `id` column (slug) remains the primary key for FK relationships and
filesystem paths; it's returned in tool output but should not be used by
callers to address an application.

Tools raise plain ValueError on not-found/invalid-input — FastMCP surfaces
these to the calling agent as a tool error, which is what we want (no HTTP
status code plumbing needed here).
"""

import sqlite3
from pathlib import Path

from db.database import get_connection, row_to_dict, rows_to_list

__all__ = [
    "get_connection",
    "row_to_dict",
    "rows_to_list",
    "get_app_by_uuid",
    "enrich_app",
    "output_dir_for",
]


def get_app_by_uuid(app_uuid: str, db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT * FROM applications WHERE uuid = ?", (app_uuid,)
    ).fetchone()
    if not row:
        raise ValueError(f"No application found with uuid={app_uuid!r}")
    return row_to_dict(row)


def enrich_app(app: dict) -> dict:
    """Add derived has_cv/has_pdf flags based on filesystem state."""
    out_dir = Path(app.get("output_dir") or "")
    app["has_cv"] = (out_dir / "cv.md").exists() if out_dir.exists() else False
    app["has_pdf"] = (out_dir / "cv.pdf").exists() if out_dir.exists() else bool(app.get("has_pdf"))
    return app


def output_dir_for(app: dict) -> Path:
    from pipeline.config import OUTPUT_PATH
    return Path(app["output_dir"]) if app.get("output_dir") else OUTPUT_PATH / app["id"]
