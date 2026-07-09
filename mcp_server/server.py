"""
server.py — entrypoint for the ATAT MCP server.

Transport is picked via ATAT_MCP_TRANSPORT (default "stdio"):

  stdio (default) — for a client that spawns this process itself, e.g. a
    project-scoped .mcp.json entry in Claude Code:
        python -m mcp_server.server

  streamable-http — for a client that can only add HTTP MCP endpoints (some
    Claude Desktop builds only support this, wrapped via `npx mcp-remote
    <url>` — a client-recognized shape that survives that app's config
    rewrites, unlike a hand-edited stdio entry). This process must already
    be running before the client tries to connect — it isn't spawned
    on-demand the way stdio servers are:
        ATAT_MCP_TRANSPORT=streamable-http python -m mcp_server.server
    Binds to ATAT_MCP_HOST:ATAT_MCP_PORT (see mcp_server/app.py), default
    127.0.0.1:8765 — 8000/8001/3000/3001 are already used by ATAT's own
    FastAPI/Next.js dev and prod servers.

Migrations are applied on startup — this process is typically started fresh
each session, not left running like the FastAPI app, and the two can run
against the same SQLite file (WAL mode) without issue since migrations are
idempotent.

Tool modules are imported for their side effects: each decorates functions
onto the shared `mcp` instance from mcp_server.app.
"""

import logging
import os

from mcp_server.app import mcp

# noqa: F401 — imported for @mcp.tool() registration side effects
import mcp_server.tools_applications  # noqa: F401
import mcp_server.tools_cover_letter  # noqa: F401
import mcp_server.tools_insights  # noqa: F401
import mcp_server.tools_intake  # noqa: F401
import mcp_server.tools_library  # noqa: F401
import mcp_server.tools_meta  # noqa: F401
import mcp_server.tools_pipeline  # noqa: F401
import mcp_server.tools_prompt_tuning  # noqa: F401
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

    transport = os.getenv("ATAT_MCP_TRANSPORT", "stdio")
    if transport not in ("stdio", "sse", "streamable-http"):
        raise ValueError(f"Invalid ATAT_MCP_TRANSPORT: {transport!r}")
    if transport != "stdio":
        log.info("Serving over %s at http://%s:%s%s", transport, mcp.settings.host, mcp.settings.port, mcp.settings.streamable_http_path)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
