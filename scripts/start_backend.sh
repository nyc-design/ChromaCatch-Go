#!/bin/bash
# ChromaCatch-Go Backend startup script
# Run this on the cloud/remote server.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/services:$PYTHONPATH"

echo "=== ChromaCatch-Go Backend ==="
poetry run uvicorn backend.main:app \
    --host "${CC_BACKEND_HOST:-0.0.0.0}" \
    --port "${CC_BACKEND_PORT:-8000}" \
    --reload
