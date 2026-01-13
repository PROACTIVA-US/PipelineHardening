# Parallel Worktree Test Execution System

## Overview

Extend the Test Harness to support parallel test execution using Git worktrees. This enables running multiple test plans simultaneously without conflicts, dramatically reducing total test execution time.

## Problem Statement

**Current Limitation:**
- Tests run sequentially in a single working directory
- Each test must wait for previous tests to complete
- 10 tests @ 10 minutes each = 100 minutes total runtime
- Working directory conflicts prevent parallelization

**Goal:**
- Run tests in parallel across isolated worktrees
- 10 tests @ 10 minutes each = ~10-15 minutes total runtime (with 3 workers)
- No conflicts, clean isolation, automatic resource management

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                  Parallel Test Orchestrator                     │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐      │
│  │  Test Queue  │   │ Worktree Pool│   │Result Manager│      │
│  │              │   │              │   │              │      │
│  │ - test-01.md │   │ • wt-1 (free)│   │ • Aggregates │      │
│  │ - test-02.md │   │ • wt-2 (busy)│   │ • Reports    │      │
│  │ - test-03.md │   │ • wt-3 (busy)│   │ • Failures   │      │
│  │ - test-04.md │   │              │   │              │      │
│  └──────────────┘   └──────────────┘   └──────────────┘      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              Execution Workers (Async)                  │  │
│  ├─────────────────────────────────────────────────────────┤  │
│  │  Worker 1 → Worktree 1 → Test Harness → test-01.md     │  │
│  │  Worker 2 → Worktree 2 → Test Harness → test-02.md     │  │
│  │  Worker 3 → Worktree 3 → Test Harness → test-03.md     │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     Git Worktree Layout                         │
│                                                                 │
│  Main Repo: /Users/.../PipelineHardening/                      │
│  ├── backend/                                                   │
│  ├── docs/                                                      │
│  └── test-artifacts/ (empty - tests use worktree dirs)         │
│                                                                 │
│  Worktrees: /Users/.../PipelineHardening-worktrees/            │
│  ├── wt-1/                                                      │
│  │   ├── .git → (linked to main repo)                          │
│  │   ├── backend/                                              │
│  │   └── test-artifacts/ (wt-1's isolated artifacts)           │
│  ├── wt-2/                                                      │
│  │   ├── .git → (linked to main repo)                          │
│  │   ├── backend/                                              │
│  │   └── test-artifacts/ (wt-2's isolated artifacts)           │
│  └── wt-3/                                                      │
│      ├── .git → (linked to main repo)                          │
│      ├── backend/                                              │
│      └── test-artifacts/ (wt-3's isolated artifacts)           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Component Breakdown

#### 1. Worktree Pool Manager

**Responsibilities:**
- Create and manage pool of worktrees
- Track worktree availability (free/busy)
- Assign worktrees to workers
- Clean up after test completion
- Health check and recovery

**Implementation:**
```python
class WorktreePool:
    def __init__(self, pool_size: int = 3, base_dir: str = "../PipelineHardening-worktrees"):
        self.pool_size = pool_size
        self.base_dir = Path(base_dir)
        self.worktrees: Dict[str, WorktreeInfo] = {}
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Create all worktrees in the pool."""
        for i in range(1, self.pool_size + 1):
            wt_id = f"wt-{i}"
            await self._create_worktree(wt_id)

    async def acquire(self) -> Worktree:
        """Get an available worktree (blocks if all busy)."""
        async with self._lock:
            # Find free worktree or wait
            ...

    async def release(self, worktree: Worktree):
        """Return worktree to pool after cleaning."""
        await self._cleanup_worktree(worktree)
        worktree.status = WorktreeStatus.FREE
```

**Data Model:**
```python
@dataclass
class WorktreeInfo:
    id: str                           # "wt-1", "wt-2", etc.
    path: Path                        # /path/to/wt-1
    branch: str                       # "worktree-wt-1"
    status: WorktreeStatus            # FREE, BUSY, ERROR
    current_test: Optional[str]       # Test plan being executed
    created_at: datetime
    last_used: datetime

class WorktreeStatus(enum.Enum):
    FREE = "free"
    BUSY = "busy"
    ERROR = "error"
```

#### 2. Test Queue Manager

**Responsibilities:**
- Queue incoming test requests
- Prioritize tests (optional)
- Track test status
- Handle retries

**Implementation:**
```python
class TestQueue:
    def __init__(self):
        self.pending: asyncio.Queue[TestRequest] = asyncio.Queue()
        self.running: Dict[str, TestRequest] = {}
        self.completed: Dict[str, TestResult] = {}
        self.failed: Dict[str, TestResult] = {}

    async def enqueue(self, test_request: TestRequest):
        """Add test to queue."""
        await self.pending.put(test_request)

    async def dequeue(self) -> TestRequest:
        """Get next test (blocks if empty)."""
        return await self.pending.get()

    def mark_running(self, test_id: str, request: TestRequest):
        """Mark test as currently running."""
        self.running[test_id] = request

    def mark_complete(self, test_id: str, result: TestResult):
        """Mark test as completed."""
        self.completed[test_id] = result
        del self.running[test_id]
```

**Data Model:**
```python
@dataclass
class TestRequest:
    id: str                            # Unique test request ID
    plan_file: str                     # "docs/plans/e2e-test-01.md"
    batch_range: str                   # "all" or "1-3"
    config: TestHarnessConfig          # Test harness config
    priority: int = 0                  # Higher = run first
    retry_count: int = 0               # Number of retries
    max_retries: int = 2               # Max retries allowed
    created_at: datetime
```

#### 3. Execution Worker

**Responsibilities:**
- Pull tests from queue
- Acquire worktree from pool
- Execute test via Test Harness
- Release worktree when done
- Report results

**Implementation:**
```python
class ExecutionWorker:
    def __init__(
        self,
        worker_id: str,
        queue: TestQueue,
        pool: WorktreePool,
        db: Session,
    ):
        self.worker_id = worker_id
        self.queue = queue
        self.pool = pool
        self.db = db
        self.running = False

    async def run(self):
        """Main worker loop - runs until stopped."""
        self.running = True
        logger.info(f"Worker {self.worker_id} started")

        while self.running:
            # 1. Get next test from queue
            test_request = await self.queue.dequeue()
            logger.info(f"Worker {self.worker_id} got test: {test_request.plan_file}")

            # 2. Acquire worktree
            worktree = await self.pool.acquire()
            logger.info(f"Worker {self.worker_id} acquired {worktree.id}")

            try:
                # 3. Execute test in worktree
                result = await self._execute_test(test_request, worktree)

                # 4. Mark complete or retry
                if result.status == "COMPLETE":
                    self.queue.mark_complete(test_request.id, result)
                elif test_request.retry_count < test_request.max_retries:
                    # Retry
                    test_request.retry_count += 1
                    await self.queue.enqueue(test_request)
                else:
                    self.queue.mark_failed(test_request.id, result)

            except Exception as e:
                logger.error(f"Worker {self.worker_id} error: {e}")
                self.queue.mark_failed(test_request.id, TestResult(error=str(e)))

            finally:
                # 5. Release worktree
                await self.pool.release(worktree)
                logger.info(f"Worker {self.worker_id} released {worktree.id}")

    async def _execute_test(
        self, test_request: TestRequest, worktree: Worktree
    ) -> TestResult:
        """Execute test in specific worktree."""
        # Create test harness with worktree-specific config
        config = test_request.config
        orchestrator = BatchOrchestrator(self.db, repo_path=str(worktree.path))
        service = TestHarnessService(config, self.db, orchestrator)

        # Run test
        report = await service.run_test_plan(
            plan_file=test_request.plan_file,
            batch_range=test_request.batch_range,
        )

        return TestResult(
            test_request_id=test_request.id,
            worktree_id=worktree.id,
            status=report.status,
            tasks_passed=report.tasks_passed,
            tasks_failed=report.tasks_failed,
            report_path=report.path,
        )
```

#### 4. Parallel Orchestrator

**Responsibilities:**
- Initialize worktree pool
- Start worker tasks
- Manage overall execution
- Aggregate results
- Generate summary report

**Implementation:**
```python
class ParallelTestOrchestrator:
    def __init__(
        self,
        num_workers: int = 3,
        db: Session = None,
    ):
        self.num_workers = num_workers
        self.db = db
        self.queue = TestQueue()
        self.pool = WorktreePool(pool_size=num_workers)
        self.workers: List[ExecutionWorker] = []
        self._tasks: List[asyncio.Task] = []

    async def initialize(self):
        """Set up worktree pool and workers."""
        # Create worktrees
        await self.pool.initialize()

        # Create workers
        for i in range(1, self.num_workers + 1):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=self.queue,
                pool=self.pool,
                db=self.db,
            )
            self.workers.append(worker)

    async def start(self):
        """Start all workers."""
        for worker in self.workers:
            task = asyncio.create_task(worker.run())
            self._tasks.append(task)
        logger.info(f"Started {self.num_workers} workers")

    async def submit_test(self, test_request: TestRequest):
        """Submit a test to the queue."""
        await self.queue.enqueue(test_request)
        logger.info(f"Queued test: {test_request.plan_file}")

    async def submit_batch(self, test_requests: List[TestRequest]):
        """Submit multiple tests at once."""
        for request in test_requests:
            await self.submit_test(request)

    async def wait_for_completion(self) -> ParallelTestReport:
        """Wait for all queued tests to complete."""
        # Wait until queue is empty and no tests running
        while (
            not self.queue.pending.empty()
            or len(self.queue.running) > 0
        ):
            await asyncio.sleep(1)

        # Generate summary report
        return self._generate_report()

    async def shutdown(self):
        """Stop all workers and cleanup."""
        for worker in self.workers:
            worker.running = False

        # Cancel worker tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Cleanup worktrees
        await self.pool.cleanup()

    def _generate_report(self) -> ParallelTestReport:
        """Generate summary report of all tests."""
        return ParallelTestReport(
            total_tests=len(self.queue.completed) + len(self.queue.failed),
            tests_passed=len(self.queue.completed),
            tests_failed=len(self.queue.failed),
            completed_tests=list(self.queue.completed.values()),
            failed_tests=list(self.queue.failed.values()),
        )
```

### Database Schema Updates

```python
class ParallelTestSession(Base):
    """Tracks a parallel test execution session."""
    __tablename__ = "parallel_test_sessions"

    id = Column(String, primary_key=True)
    num_workers = Column(Integer, nullable=False)
    status = Column(String, nullable=False)  # RUNNING, COMPLETE, FAILED
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    # Summary stats
    tests_total = Column(Integer, default=0)
    tests_passed = Column(Integer, default=0)
    tests_failed = Column(Integer, default=0)

    # Relationships
    test_executions = relationship("ParallelTestExecution")


class ParallelTestExecution(Base):
    """Individual test execution within parallel session."""
    __tablename__ = "parallel_test_executions"

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("parallel_test_sessions.id"))
    test_run_id = Column(String, ForeignKey("test_runs.id"))

    # Test info
    plan_file = Column(String, nullable=False)
    batch_range = Column(String, nullable=False)

    # Execution info
    worktree_id = Column(String)
    worker_id = Column(String)
    status = Column(String, nullable=False)
    retry_count = Column(Integer, default=0)

    # Timing
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    # Relationships
    session = relationship("ParallelTestSession")
    test_run = relationship("TestRun")
```

## API Design

### New Endpoints

#### 1. Start Parallel Test Session

```http
POST /api/v1/parallel-test/start
Content-Type: application/json

{
  "test_plans": [
    {
      "plan_file": "docs/plans/e2e-test-01.md",
      "batch_range": "all"
    },
    {
      "plan_file": "docs/plans/e2e-test-02.md",
      "batch_range": "1-2"
    },
    {
      "plan_file": "docs/plans/e2e-test-03.md",
      "batch_range": "all"
    }
  ],
  "num_workers": 3,
  "config": {
    "task_timeout": 600,
    "max_retries": 2
  }
}

Response:
{
  "session_id": "abc-123",
  "status": "RUNNING",
  "num_workers": 3,
  "tests_queued": 3,
  "estimated_duration_minutes": 15
}
```

#### 2. Get Parallel Session Status

```http
GET /api/v1/parallel-test/{session_id}/status

Response:
{
  "session_id": "abc-123",
  "status": "RUNNING",
  "progress": {
    "total": 3,
    "completed": 1,
    "running": 2,
    "pending": 0,
    "failed": 0
  },
  "workers": [
    {
      "worker_id": "worker-1",
      "status": "BUSY",
      "current_test": "e2e-test-02.md",
      "worktree_id": "wt-1"
    },
    {
      "worker_id": "worker-2",
      "status": "BUSY",
      "current_test": "e2e-test-03.md",
      "worktree_id": "wt-2"
    },
    {
      "worker_id": "worker-3",
      "status": "FREE",
      "current_test": null,
      "worktree_id": null
    }
  ]
}
```

#### 3. Get Parallel Session Results

```http
GET /api/v1/parallel-test/{session_id}/results

Response:
{
  "session_id": "abc-123",
  "status": "COMPLETE",
  "duration_minutes": 12.5,
  "summary": {
    "total_tests": 3,
    "tests_passed": 2,
    "tests_failed": 1
  },
  "results": [
    {
      "plan_file": "e2e-test-01.md",
      "status": "COMPLETE",
      "tasks_passed": 5,
      "tasks_failed": 0,
      "duration_seconds": 450
    },
    {
      "plan_file": "e2e-test-02.md",
      "status": "COMPLETE",
      "tasks_passed": 3,
      "tasks_failed": 0,
      "duration_seconds": 380
    },
    {
      "plan_file": "e2e-test-03.md",
      "status": "FAILED",
      "tasks_passed": 2,
      "tasks_failed": 1,
      "report_path": "test-reports/test_run_xyz_20260113.md",
      "duration_seconds": 720
    }
  ]
}
```

## Implementation Phases

### Phase A: Worktree Pool (Week 1)
- [ ] Implement WorktreePool class
- [ ] Create worktree creation/deletion logic
- [ ] Add worktree health checks
- [ ] Test isolation between worktrees

### Phase B: Queue & Workers (Week 2)
- [ ] Implement TestQueue class
- [ ] Implement ExecutionWorker class
- [ ] Add worker lifecycle management
- [ ] Test queue operations

### Phase C: Parallel Orchestrator (Week 3)
- [ ] Implement ParallelTestOrchestrator
- [ ] Add result aggregation
- [ ] Generate summary reports
- [ ] Test end-to-end parallel execution

### Phase D: API Integration (Week 4)
- [ ] Add database models
- [ ] Create API endpoints
- [ ] Add request validation
- [ ] Test via API

### Phase E: Testing & Optimization (Week 5)
- [ ] Run parallel tests on actual plans
- [ ] Optimize worker count
- [ ] Add monitoring/observability
- [ ] Performance tuning

## Configuration

```python
@dataclass
class ParallelTestConfig:
    """Configuration for parallel test execution."""

    # Worktree settings
    num_workers: int = 3                    # Number of parallel workers
    worktree_base_dir: str = "../PipelineHardening-worktrees"

    # Queue settings
    max_queue_size: int = 100               # Max tests in queue
    retry_failed_tests: bool = True         # Retry failed tests
    max_retries_per_test: int = 2          # Max retries per test

    # Worker settings
    worker_timeout_minutes: int = 30        # Max time per test
    worker_restart_on_error: bool = True    # Restart worker on crash

    # Cleanup settings
    cleanup_on_completion: bool = True      # Remove worktrees when done
    preserve_failed_worktrees: bool = True  # Keep failed worktrees for debug

    # Test harness config (per test)
    default_test_config: TestHarnessConfig = TestHarnessConfig()
```

## Performance Expectations

### Single Worker (Sequential)
```
Test 1: 10 min
Test 2: 8 min
Test 3: 12 min
Test 4: 9 min
Test 5: 11 min
---------------
Total: 50 minutes
```

### Three Workers (Parallel)
```
Worker 1: Test 1 (10 min) → Test 4 (9 min)  = 19 min
Worker 2: Test 2 (8 min)  → Test 5 (11 min) = 19 min
Worker 3: Test 3 (12 min)                    = 12 min
---------------------------------------------------
Total: 19 minutes (2.6x speedup)
```

### Theoretical Limits
- **Best case**: N tests / N workers = ~1 test time (if all equal duration)
- **Worst case**: Same as sequential (if tests must run serially)
- **Realistic**: 2-3x speedup with 3 workers for typical test suites

## Risk Mitigation

### Risk 1: Worktree Conflicts
**Mitigation**: Each worktree has unique branch name and isolated working directory

### Risk 2: Resource Exhaustion
**Mitigation**: Limit worker count, monitor system resources, graceful degradation

### Risk 3: Database Contention
**Mitigation**: Each worker uses separate DB session, row-level locking

### Risk 4: Worktree Cleanup Failures
**Mitigation**: Retry cleanup, manual cleanup script, health checks

### Risk 5: Test Interdependencies
**Mitigation**: Tests must be independent, queue ordering for dependencies (future)

## Success Criteria

- [ ] Can run 3+ tests in parallel without conflicts
- [ ] Achieves 2x+ speedup with 3 workers
- [ ] Automatic worktree creation and cleanup
- [ ] Comprehensive error handling and recovery
- [ ] Clear status reporting via API
- [ ] Zero data loss or corruption

## Future Enhancements

### Dynamic Worker Scaling
- Auto-scale workers based on queue depth
- Use system resources as scaling signal

### Smart Scheduling
- Prioritize shorter tests
- Group tests by resource requirements
- Dependency-aware scheduling

### Distributed Execution
- Run workers across multiple machines
- Remote worktrees over network

### Cost Optimization
- Estimate cost per test
- Budget-aware scheduling
- Spot instance support

## References

- Git Worktree Documentation: https://git-scm.com/docs/git-worktree
- Python AsyncIO: https://docs.python.org/3/library/asyncio.html
- Test Harness Design: `docs/designs/test-harness-design.md`

---

**Next Steps**: Proceed to Phase A implementation - Worktree Pool creation
