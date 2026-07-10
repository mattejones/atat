#!/bin/bash
# dev.sh — Start both the FastAPI backend and Next.js frontend in development mode.
#
# Runs on separate ports from production so both can run simultaneously:
#   Backend:  http://localhost:8001
#   Frontend: http://localhost:3001
#
# Usage: ./dev.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# nvm's PATH setup only runs in an interactive shell (guarded early-return in
# ~/.bashrc) — a non-interactive invocation never gets it, and npm/node
# silently fall back to whatever's on Windows' PATH via WSL interop instead.
# Source it explicitly so this works the same interactively or not.
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Activate venv if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "Starting ATAT (dev mode)..."
echo "  Backend:    http://localhost:8001"
echo "  Frontend:   http://localhost:3001"
echo "  MCP server: http://localhost:8765/mcp"
echo ""

# Start FastAPI on dev port with hot reload
cd "$SCRIPT_DIR"
uvicorn api.main:app --reload --port 8001 &
BACKEND_PID=$!

# Export the dev API URL so Next.js picks it up at request time
# (NEXT_PUBLIC_* vars are read from the environment in next dev, unlike next build)
cd "$SCRIPT_DIR/web"
export NEXT_PUBLIC_API_URL=http://localhost:8001
npm run dev -- -p 3001 &
FRONTEND_PID=$!

# MCP server — streamable-http, same port regardless of dev/prod (no dev/prod
# split concept for MCP the way there is for the web app; an MCP client's
# config points at one fixed port). No hot-reload equivalent — restart
# dev.sh if you change mcp_server/ code.
cd "$SCRIPT_DIR"
ATAT_MCP_TRANSPORT=streamable-http python3 -m mcp_server.server &
MCP_PID=$!

# Trap Ctrl+C to kill all three
trap "kill $BACKEND_PID $FRONTEND_PID $MCP_PID 2>/dev/null; exit" INT TERM

wait
