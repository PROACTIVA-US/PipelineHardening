#!/bin/bash
# Run a single E2E test against the Pipeline Hardening server
#
# Usage:
#   ./scripts/run_e2e_test.sh [test_number]
#
# Examples:
#   ./scripts/run_e2e_test.sh        # Run e2e-test-01.md
#   ./scripts/run_e2e_test.sh 3      # Run e2e-test-03.md

set -e

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# Test number (default: 01)
TEST_NUM="${1:-01}"
TEST_NUM=$(printf "%02d" "$TEST_NUM")

# Load environment
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Activate venv
if [ -d "backend/.venv" ]; then
    source backend/.venv/bin/activate
fi

echo "=============================================="
echo "Pipeline Hardening - E2E Test Runner"
echo "=============================================="
echo ""

# Pre-flight checks
echo "Pre-flight checks..."

# Check server is running
if ! curl -s http://localhost:8001/health > /dev/null 2>&1; then
    echo "❌ Server not running on localhost:8001"
    echo ""
    echo "Start it with: ./scripts/run_server.sh"
    exit 1
fi
echo "✅ Server is running"

# Check GitHub token
if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ GITHUB_TOKEN not set"
    exit 1
fi
echo "✅ GitHub token is set"

# Check Claude CLI
if ! which claude > /dev/null 2>&1; then
    echo "⚠️  Claude CLI not in PATH (may be available in subprocess)"
fi

echo ""
echo "=============================================="
echo "Running E2E Test: e2e-test-${TEST_NUM}.md"
echo "=============================================="
echo ""

# Plan path
PLAN_PATH="${PROJECT_ROOT}/docs/plans/e2e-test-${TEST_NUM}.md"

if [ ! -f "$PLAN_PATH" ]; then
    echo "❌ Plan file not found: $PLAN_PATH"
    exit 1
fi

echo "Plan: $PLAN_PATH"
echo ""

# Clean up test artifacts
echo "Cleaning test-artifacts/..."
rm -rf test-artifacts/*
mkdir -p test-artifacts

# Start execution
echo "Starting autonomous execution..."
echo ""

RESPONSE=$(curl -s -X POST http://localhost:8001/api/v1/autonomous/start \
  -H "Content-Type: application/json" \
  -d "{
    \"plan_path\": \"${PLAN_PATH}\",
    \"start_batch\": 1,
    \"end_batch\": 1,
    \"execution_mode\": \"local\",
    \"auto_merge\": true
  }")

echo "Response: $RESPONSE"
echo ""

# Extract execution ID
EXEC_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('execution_id', ''))" 2>/dev/null)

if [ -z "$EXEC_ID" ]; then
    echo "❌ Failed to get execution_id from response"
    exit 1
fi

echo "Execution ID: $EXEC_ID"
echo ""

# Poll for completion
echo "Polling for completion (timeout: 5 minutes)..."
echo ""

TIMEOUT=300  # 5 minutes
ELAPSED=0
INTERVAL=5

while [ $ELAPSED -lt $TIMEOUT ]; do
    STATUS_RESPONSE=$(curl -s "http://localhost:8001/api/v1/autonomous/${EXEC_ID}/status")
    STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))" 2>/dev/null)

    TASKS_COMPLETED=$(echo "$STATUS_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('tasks_completed', 0))" 2>/dev/null)
    TASKS_FAILED=$(echo "$STATUS_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('tasks_failed', 0))" 2>/dev/null)

    echo "[${ELAPSED}s] Status: $STATUS | Completed: $TASKS_COMPLETED | Failed: $TASKS_FAILED"

    if [ "$STATUS" = "complete" ] || [ "$STATUS" = "completed" ]; then
        echo ""
        echo "=============================================="
        echo "✅ E2E TEST PASSED"
        echo "=============================================="
        echo ""

        # Show final status
        echo "Final Status:"
        echo "$STATUS_RESPONSE" | python3 -m json.tool

        # Show created files
        echo ""
        echo "Created files:"
        ls -la test-artifacts/ 2>/dev/null || echo "(none)"

        # Get batch details
        echo ""
        echo "Batch details:"
        curl -s "http://localhost:8001/api/v1/autonomous/${EXEC_ID}/batches" | python3 -m json.tool

        exit 0
    fi

    if [ "$STATUS" = "failed" ]; then
        echo ""
        echo "=============================================="
        echo "❌ E2E TEST FAILED"
        echo "=============================================="
        echo ""
        echo "Status Response:"
        echo "$STATUS_RESPONSE" | python3 -m json.tool

        # Get batch details for debugging
        echo ""
        echo "Batch details:"
        curl -s "http://localhost:8001/api/v1/autonomous/${EXEC_ID}/batches" | python3 -m json.tool

        exit 1
    fi

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

echo ""
echo "=============================================="
echo "⏱️  E2E TEST TIMEOUT"
echo "=============================================="
echo ""
echo "Test did not complete within ${TIMEOUT} seconds"
echo ""
echo "Final status:"
curl -s "http://localhost:8001/api/v1/autonomous/${EXEC_ID}/status" | python3 -m json.tool

exit 1
