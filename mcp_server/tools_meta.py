"""
tools_meta.py — tools about the server itself, rather than application data.

get_guide is the tool-shaped twin of the atat://guide resource — see
resources.py for why both exist. Call this first in a new session, before
any other atat tool, if you haven't already fetched the guide.
"""

from mcp_server.app import mcp
from mcp_server.guide import GUIDE


@mcp.tool()
def get_guide() -> str:
    """
    Return ATAT's workflow guide: how the tools compose into an end-to-end
    job-ad -> submitted-application flow, and the valid values for every
    enum field (status, tier, work_arrangement, section_name, flag type,
    etc). Call this first, before other atat tools, if this is a new
    session — it'll save you guessing at valid values or call order.
    """
    return GUIDE
