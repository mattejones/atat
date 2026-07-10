"""
resources.py — read-only MCP resources for ATAT.

Tool docstrings document what a single tool does; they don't document how
tools compose into a workflow, or what values are actually valid across the
whole schema. This module exposes exactly that as a fetchable resource,
separate from the per-call tool descriptions FastMCP already sends.

Also exposed as a plain tool (get_guide, in tools_meta.py) with identical
content — plenty of MCP clients implement tools/list + tools/call but never
wire up resources/list + resources/read, so a resource-only endpoint can be
invisible even on a server that's otherwise connected fine. Tools are the
one primitive every client reliably supports; treat the resource as a nice-
to-have for clients that do support it, not the only way in.
"""

from mcp_server.app import mcp
from mcp_server.guide import GUIDE


@mcp.resource("atat://guide")
def guide() -> str:
    """Workflow sequencing and schema value glossary for ATAT's MCP tools."""
    return GUIDE
