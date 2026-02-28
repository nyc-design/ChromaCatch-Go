#!/bin/bash
# ChromaCatch-Go development startup script
# Runs both backend and client locally for development.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/services:$PROJECT_DIR/services/airplay-client:$PYTHONPATH"

echo "=== ChromaCatch-Go (Dev Mode) ==="
echo "Starting backend..."

poetry run uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload &
BACKEND_PID=$!

cleanup() {
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    wait $BACKEND_PID 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT

sleep 2
echo "Starting client..."
poetry run python -m airplay_client.main
