#!/bin/bash
# Run the Pipeline Hardening server

set -e

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d "backend/.venv" ]; then
    source backend/.venv/bin/activate
fi

# Run server
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
