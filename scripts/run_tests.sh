#!/bin/bash
# Run Pipeline Hardening tests

set -e

cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d "backend/.venv" ]; then
    source backend/.venv/bin/activate
fi

# Run tests
pytest tests/ -v "$@"
