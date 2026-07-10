#!/bin/bash
# mcp_server_http.sh — Run just the ATAT MCP server over streamable-http.
#
# start.sh and dev.sh both already start this alongside the web app — use
# this script instead of those when you want the MCP server running
# without the web app (e.g. driving ATAT purely through an agent).
#
# Only needed for an MCP client that can't spawn a stdio subprocess itself
# and only supports adding HTTP MCP endpoints (bridged via `npx mcp-remote
# <url>`). If your client supports stdio directly (e.g. a project-scoped
# .mcp.json in Claude Code), use `python -m mcp_server.server` instead —
# no need for this script or to keep anything running in the background.
#
# Unlike stdio servers, this process is NOT spawned on demand by the
# client — it must already be running before the client tries to connect.
# Run this in its own terminal (or nohup it) before opening the client.
#
# Binds to 127.0.0.1:8765 by default — override with ATAT_MCP_HOST /
# ATAT_MCP_PORT. Never bind this to anything other than 127.0.0.1: the
# server has no auth and full read/write access to your application data.
#
# Usage: ./mcp_server_http.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

cd "$SCRIPT_DIR"
export ATAT_MCP_TRANSPORT=streamable-http
export ATAT_MCP_HOST="${ATAT_MCP_HOST:-127.0.0.1}"
export ATAT_MCP_PORT="${ATAT_MCP_PORT:-8765}"

echo "Starting ATAT MCP server (streamable-http)..."
echo "  http://${ATAT_MCP_HOST}:${ATAT_MCP_PORT}/mcp"
echo ""

python3 -m mcp_server.server
