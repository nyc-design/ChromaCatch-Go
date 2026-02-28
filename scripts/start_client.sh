#!/bin/bash
# ChromaCatch-Go Client startup script
# Run this on the local machine near the iPhone + ESP32.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/services:$PROJECT_DIR/services/airplay-client:$PYTHONPATH"

echo "=== ChromaCatch-Go Client ==="
poetry run python -m airplay_client.main
