#!/bin/bash
# Pre-flight check for E2E testing
#
# Verifies all requirements are met before running E2E tests.

set -e

cd "$(dirname "$0")/.."

echo "=============================================="
echo "Pipeline Hardening - Pre-flight Check"
echo "=============================================="
echo ""

ERRORS=0

# Load environment
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check 1: GitHub Token
echo -n "GitHub Token: "
if [ -n "$GITHUB_TOKEN" ]; then
    # Verify token works
    if curl -s -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user > /dev/null 2>&1; then
        echo "✅ Set and valid"
    else
        echo "⚠️  Set but may be invalid"
    fi
else
    echo "❌ Not set"
    echo "   Fix: Add GITHUB_TOKEN to .env file"
    ERRORS=$((ERRORS + 1))
fi

# Check 2: Claude CLI
echo -n "Claude CLI: "
if which claude > /dev/null 2>&1; then
    echo "✅ Available at $(which claude)"
else
    # Check if it's in homebrew
    if [ -x "/opt/homebrew/bin/claude" ]; then
        echo "✅ Available at /opt/homebrew/bin/claude (not in PATH)"
    else
        echo "❌ Not found"
        echo "   Fix: Install Claude Code CLI"
        ERRORS=$((ERRORS + 1))
    fi
fi

# Check 3: Python venv
echo -n "Python venv: "
if [ -d "backend/.venv" ]; then
    echo "✅ Found"
else
    echo "❌ Not found"
    echo "   Fix: Run ./scripts/setup.sh"
    ERRORS=$((ERRORS + 1))
fi

# Check 4: Git status
echo -n "Git status: "
if git diff --quiet && git diff --cached --quiet; then
    echo "✅ Clean working tree"
else
    echo "⚠️  Uncommitted changes"
    echo "   This may interfere with branch operations"
fi

# Check 5: Server port
echo -n "Port 8001: "
if lsof -i :8001 > /dev/null 2>&1; then
    echo "⚠️  In use (server may be running)"
else
    echo "✅ Available"
fi

# Check 6: Test plan files
echo -n "Test plans: "
PLAN_COUNT=$(ls docs/plans/e2e-test-*.md 2>/dev/null | wc -l | tr -d ' ')
if [ "$PLAN_COUNT" -gt 0 ]; then
    echo "✅ $PLAN_COUNT found"
else
    echo "❌ None found"
    ERRORS=$((ERRORS + 1))
fi

# Check 7: test-artifacts directory
echo -n "Test artifacts dir: "
if [ -d "test-artifacts" ]; then
    echo "✅ Exists"
else
    mkdir -p test-artifacts
    echo "✅ Created"
fi

echo ""
echo "=============================================="

if [ $ERRORS -eq 0 ]; then
    echo "✅ All checks passed - ready for E2E testing"
    echo ""
    echo "Next steps:"
    echo "  1. Start server: ./scripts/run_server.sh"
    echo "  2. Run E2E test: ./scripts/run_e2e_test.sh"
    exit 0
else
    echo "❌ $ERRORS check(s) failed - fix before running E2E tests"
    exit 1
fi
