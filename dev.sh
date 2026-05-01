#!/bin/bash
# dev.sh — Start both the FastAPI backend and Next.js frontend.
#
# Usage: ./dev.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate venv if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "Starting ATAT..."
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo ""

# Start FastAPI in background
cd "$SCRIPT_DIR"
uvicorn api.main:app --reload --port 8000 &
BACKEND_PID=$!

# Start Next.js
cd "$SCRIPT_DIR/web"
npm run dev &
FRONTEND_PID=$!

# Trap Ctrl+C to kill both
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM

wait
