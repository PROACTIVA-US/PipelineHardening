# Pipeline Hardening

Isolated environment for developing and proving the autonomous execution pipeline works reliably.

**Goal:** 10+ consecutive E2E tests required before integrating with larger systems.

## Architecture

```
PipelineHardening/
├── backend/
│   └── app/
│       ├── main.py           # FastAPI app with health endpoint
│       ├── database.py       # SQLite database setup
│       ├── models/           # SQLAlchemy models
│       ├── schemas/          # Pydantic schemas
│       ├── routers/          # API endpoints
│       └── services/
│           ├── plan_parser.py      # Parse markdown plans
│           ├── task_executor.py    # Execute tasks via Claude CLI
│           ├── batch_orchestrator.py  # Manage batch execution
│           └── execution_runner.py    # Background execution loop
├── docs/plans/               # Test plan files
├── test-artifacts/           # Output from test executions
├── tests/                    # E2E test suite
└── scripts/                  # Setup and run scripts
```

## Quick Start

```bash
# Setup environment
./scripts/setup.sh

# Edit .env with your GitHub token
vim .env

# Run server
./scripts/run_server.sh

# Run tests (in another terminal)
./scripts/run_tests.sh
```

## API Endpoints

- `GET /health` - Health check
- `POST /api/v1/autonomous/start` - Start execution
- `GET /api/v1/autonomous/{id}/status` - Get status
- `GET /api/v1/autonomous/{id}/batches` - Get batch details

## Pipeline Flow

1. **Parse Plan** - Read markdown plan, extract batches and tasks
2. **Create Session** - Store execution state in SQLite
3. **Execute Tasks** - For each task:
   - Create feature branch
   - Run Claude CLI with task prompt
   - Commit and push changes
   - Create PR
   - Merge PR (if auto_merge enabled)
4. **Track Progress** - Update database with results

## Success Criteria

- Server starts without errors
- Can parse test plan files
- Can execute simple "create a file" task
- PR is created on GitHub
- PR can be merged
- 10+ consecutive successful E2E runs
