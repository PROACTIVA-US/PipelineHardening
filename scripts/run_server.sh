#!/bin/bash
# Run the Pipeline Hardening server
#
# CRITICAL: Do NOT use --reload for autonomous execution!
# --reload kills background tasks when files change.
# See: skills/lessons - "Incident: --reload Kills Background Tasks"

set -e

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d "backend/.venv" ]; then
    source backend/.venv/bin/activate
fi

# Load environment variables
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Run server (NO --reload for autonomous execution!)
cd backend
echo "Starting server on http://localhost:8001"
echo "CRITICAL: Running WITHOUT --reload (required for background tasks)"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
