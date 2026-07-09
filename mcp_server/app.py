"""
app.py — shared FastMCP instance for the ATAT MCP server.

Tool modules import `mcp` from here and register tools via @mcp.tool().
Kept separate from server.py so tool modules don't have to import the
entrypoint (which would create a circular import).

host/port only matter for HTTP transports (see server.py's ATAT_MCP_TRANSPORT) —
ignored entirely for stdio. Default host is 127.0.0.1: this server has no auth,
so it must never bind anywhere reachable off the local machine.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "atat",
    host=os.getenv("ATAT_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("ATAT_MCP_PORT", "8765")),
    instructions=(
        "Tools for ATAT — a personal job application tracker and LLM CV/cover-letter "
        "pipeline. Use these to browse and update application history, drive CV "
        "generation and the section judge/review pipeline, generate cover letters and "
        "application question answers, and mine past feedback for patterns before "
        "generating new content. Applications are addressed by their `uuid` field "
        "(not `id`, which is a filesystem slug used internally)."
    ),
)
