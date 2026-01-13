# E2E Test Runbook

> **Goal:** 10 consecutive successful E2E runs before integrating with larger systems.

---

## Pre-flight Checklist

```bash
# Run this before any E2E test
./scripts/preflight.sh
```

Or manually verify:

| Check | Command | Expected |
|-------|---------|----------|
| Docker running | `docker info > /dev/null && echo "✅"` | ✅ |
| GitHub token set | `test -n "$GITHUB_TOKEN" && echo "✅"` | ✅ |
| Claude CLI available | `which claude && echo "✅"` | ✅ |
| Server NOT running | `curl -s localhost:8001/health \|\| echo "✅ (not running)"` | Not running (we'll start it) |
| Clean git state | `cd /path/to/PipelineHardening && git status` | Clean working tree |

---

## Running an E2E Test

### Step 1: Start the Server (Terminal 1)

```bash
cd /Users/danielconnolly/Projects/PipelineHardening
./scripts/run_server.sh
```

**CRITICAL:** Server runs WITHOUT `--reload`. This is required for background tasks.

Wait for: `Started server on http://localhost:8001`

### Step 2: Run E2E Test (Terminal 2)

```bash
cd /Users/danielconnolly/Projects/PipelineHardening
./scripts/run_e2e_test.sh
```

Or manually:

```bash
# Load environment
source backend/.venv/bin/activate
source .env

# Start execution
curl -X POST http://localhost:8001/api/v1/autonomous/start \
  -H "Content-Type: application/json" \
  -d '{
    "plan_path": "/Users/danielconnolly/Projects/PipelineHardening/docs/plans/e2e-test-01.md",
    "start_batch": 1,
    "end_batch": 1,
    "execution_mode": "local",
    "auto_merge": true
  }'
```

### Step 3: Monitor Progress

```bash
# Get execution ID from step 2, then:
curl -s http://localhost:8001/api/v1/autonomous/{EXECUTION_ID}/status | python3 -m json.tool

# Or watch batches:
curl -s http://localhost:8001/api/v1/autonomous/{EXECUTION_ID}/batches | python3 -m json.tool
```

---

## Expected Results

### Success Criteria

| Criterion | How to Verify |
|-----------|---------------|
| Session starts | API returns `execution_id` |
| Task transitions | Status: pending → in_progress → completed |
| Branch created | `git branch -a \| grep feature/batch-1` |
| File created | `ls test-artifacts/hello.txt` |
| PR created | Check GitHub or `pr_number` in task status |
| PR merged | PR status is "merged" on GitHub |
| Clean completion | Session status is "complete" |

### Success Output

```json
{
  "execution_id": "exec_abc123",
  "status": "complete",
  "tasks_completed": 1,
  "tasks_failed": 0
}
```

### Failure Indicators

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Task stuck in "executing" | Server using --reload | Restart WITHOUT --reload |
| "Claude CLI not found" | PATH missing homebrew | Add /opt/homebrew/bin to PATH |
| "No commits between branches" | Git race condition | Use sequential mode or Dagger |
| PR creation fails | GitHub token invalid | Check GITHUB_TOKEN in .env |

---

## Test Plan Files

| Test | Plan File | What It Tests |
|------|-----------|---------------|
| E2E-01 | `e2e-test-01.md` | Single file creation |
| E2E-02 | `e2e-test-02.md` | Two sequential tasks |
| E2E-03 | `e2e-test-03.md` | Task with dependencies |
| E2E-04 | `e2e-test-04.md` | File modification |
| E2E-05 | `e2e-test-05.md` | Multiple files |
| E2E-06 | `e2e-test-06.md` | Error handling |
| E2E-07 | `e2e-test-07.md` | Batch dependencies |
| E2E-08 | `e2e-test-08.md` | Large file |
| E2E-09 | `e2e-test-09.md` | Complex task |

---

## Recording Results

After each test, record in this format:

```markdown
### Run #N - YYYY-MM-DD HH:MM
- **Test:** e2e-test-0X.md
- **Mode:** local/dagger
- **Duration:** Xm Ys
- **Result:** PASS/FAIL
- **PR:** #XX (if created)
- **Notes:** [Any observations]
```

---

## Cleanup Between Tests

```bash
# Delete test artifacts
rm -rf test-artifacts/*

# Clean up branches (if needed)
git branch -D feature/batch-1-task-1-1 2>/dev/null || true
git push origin --delete feature/batch-1-task-1-1 2>/dev/null || true

# Reset database (if needed)
rm -f backend/pipeline.db
```

---

## Troubleshooting

### Server won't start
```bash
# Check if port is in use
lsof -i :8001

# Kill existing process
kill $(lsof -t -i :8001)
```

### Background tasks not running
```bash
# NEVER use --reload
# Restart server without --reload flag
```

### GitHub API errors
```bash
# Verify token
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user
```

---

*10 consecutive successes required before integration.*
