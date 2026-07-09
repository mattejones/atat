"""
server.py — entrypoint for the ATAT MCP server.

Run as a subprocess over stdio (the transport an MCP client like Claude
Desktop or Claude Code spawns directly), e.g.:

    python -m mcp_server.server

Migrations are applied on startup — this process is typically started fresh
each session, not left running like the FastAPI app, and the two can run
against the same SQLite file (WAL mode) without issue since migrations are
idempotent.

Tool modules are imported for their side effects: each decorates functions
onto the shared `mcp` instance from mcp_server.app.
"""

import logging

from mcp_server.app import mcp

# noqa: F401 — imported for @mcp.tool() registration side effects
import mcp_server.tools_applications  # noqa: F401
import mcp_server.tools_cover_letter  # noqa: F401
import mcp_server.tools_insights  # noqa: F401
import mcp_server.tools_intake  # noqa: F401
import mcp_server.tools_pipeline  # noqa: F401
import mcp_server.tools_questions  # noqa: F401
import mcp_server.tools_submission  # noqa: F401
import mcp_server.resources  # noqa: F401

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from db.migrate import run_migrations
    try:
        run_migrations()
    except Exception:
        log.exception("Migration failed — starting anyway, some tools may error")

    mcp.run()


if __name__ == "__main__":
    main()
