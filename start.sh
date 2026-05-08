#!/bin/bash
# start.sh — Build and run ATAT in production mode.
#
# Run this instead of dev.sh for a fast, optimised experience.
# First run will take ~30s to build Next.js. Subsequent starts are instant.
#
# Usage:
#   ./start.sh          # build if needed, then start
#   ./start.sh --build  # force a fresh build before starting

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE_BUILD=false

if [[ "$1" == "--build" ]]; then
    FORCE_BUILD=true
fi

# Activate venv if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

# Build Next.js if no build exists or --build flag passed
BUILD_DIR="$SCRIPT_DIR/web/.next"
if [ ! -d "$BUILD_DIR" ] || [ "$FORCE_BUILD" = true ]; then
    echo "Building Next.js..."
    cd "$SCRIPT_DIR/web"
    npm run build
    if [ $? -ne 0 ]; then
        echo "Build failed — aborting."
        exit 1
    fi
fi

echo ""
echo "Starting ATAT (production mode)..."
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo ""

# FastAPI — no --reload in production
cd "$SCRIPT_DIR"
uvicorn api.main:app --port 8000 &
BACKEND_PID=$!

# Next.js production server
cd "$SCRIPT_DIR/web"
npm run start &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM

wait
