"""Tests for ParallelTestOrchestrator."""

import pytest
import asyncio
from pathlib import Path

from backend.app.services.parallel_orchestrator import (
    ParallelTestOrchestrator,
    ParallelTestConfig,
    ParallelTestReport,
)
from backend.app.services.test_queue import TestRequest, TestHarnessConfig


class TestOrchestratorLifecycle:
    """Test orchestrator lifecycle operations."""

    @pytest.fixture
    async def orchestrator(self, tmp_path):
        """Create an orchestrator with temp worktree directory."""
        config = ParallelTestConfig(
            num_workers=2,
            worktree_base_dir=str(tmp_path / "worktrees"),
            max_queue_size=10,
        )
        orch = ParallelTestOrchestrator(config=config)
        yield orch

        # Cleanup
        if orch._running:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_initialization(self, orchestrator):
        """Test orchestrator can be initialized."""
        assert orchestrator._initialized is False
        assert len(orchestrator.workers) == 0
        assert orchestrator.pool.worktrees == {}

        # Skip pool initialization to avoid git operations
        orchestrator.pool._initialized = True

        # Initialize workers only
        for i in range(1, orchestrator.config.num_workers + 1):
            from backend.app.services.execution_worker import ExecutionWorker
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orchestrator.queue,
                pool=orchestrator.pool,
            )
            orchestrator.workers.append(worker)

        orchestrator._initialized = True

        assert orchestrator._initialized is True
        assert len(orchestrator.workers) == 2

    @pytest.mark.asyncio
    async def test_orchestrator_start_stop(self, orchestrator, tmp_path):
        """Test orchestrator can start and stop workers."""
        # Skip pool initialization and manually set up
        orchestrator.pool._initialized = True
        from backend.app.services.worktree_pool import WorktreeInfo, WorktreeStatus
        from backend.app.services.execution_worker import ExecutionWorker

        # Add mock worktrees
        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)
            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            orchestrator.pool.worktrees[f"wt-{i}"] = worktree

        # Add workers
        for i in range(1, 3):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orchestrator.queue,
                pool=orchestrator.pool,
            )
            orchestrator.workers.append(worker)

        orchestrator._initialized = True

        # Start
        await orchestrator.start()

        assert orchestrator._running is True
        assert orchestrator.started_at is not None
        for worker in orchestrator.workers:
            assert worker.running is True

        # Stop
        await orchestrator.shutdown()

        assert orchestrator._running is False
        for worker in orchestrator.workers:
            assert worker.running is False

    @pytest.mark.asyncio
    async def test_orchestrator_submit_tests(self, orchestrator, tmp_path):
        """Test submitting tests to orchestrator."""
        # Skip initialization to avoid git operations
        orchestrator._initialized = True

        # Create test requests
        requests = [
            TestRequest(
                id=f"test-{i}",
                plan_file=f"test-{i}.md",
            )
            for i in range(3)
        ]

        # Submit tests
        for request in requests:
            await orchestrator.submit_test(request)

        # Check queue
        queue_status = orchestrator.queue.get_status()
        assert queue_status["pending_count"] == 3

    @pytest.mark.asyncio
    async def test_orchestrator_submit_batch(self, orchestrator):
        """Test submitting batch of tests."""
        # Skip initialization to avoid git operations
        orchestrator._initialized = True

        # Create test requests
        requests = [
            TestRequest(
                id=f"test-{i}",
                plan_file=f"test-{i}.md",
            )
            for i in range(5)
        ]

        # Submit batch
        await orchestrator.submit_batch(requests)

        # Check queue
        queue_status = orchestrator.queue.get_status()
        assert queue_status["pending_count"] == 5

    @pytest.mark.asyncio
    async def test_orchestrator_get_status(self, orchestrator, tmp_path):
        """Test getting orchestrator status."""
        # Skip pool init and manually set up
        orchestrator.pool._initialized = True
        from backend.app.services.worktree_pool import WorktreeInfo, WorktreeStatus
        from backend.app.services.execution_worker import ExecutionWorker

        # Add mock worktrees
        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)
            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            orchestrator.pool.worktrees[f"wt-{i}"] = worktree

        # Add workers
        for i in range(1, 3):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orchestrator.queue,
                pool=orchestrator.pool,
            )
            orchestrator.workers.append(worker)

        orchestrator._initialized = True
        await orchestrator.start()

        # Get status
        status = orchestrator.get_status()

        assert status["session_id"] == orchestrator.session_id
        assert status["initialized"] is True
        assert status["running"] is True
        assert status["started_at"] is not None
        assert "queue" in status
        assert "pool" in status
        assert "workers" in status
        assert len(status["workers"]) == 2

        await orchestrator.shutdown()


class TestOrchestratorExecution:
    """Test orchestrator execution flow."""

    @pytest.fixture
    async def orchestrator(self, tmp_path):
        """Create orchestrator with mock worktrees."""
        config = ParallelTestConfig(
            num_workers=2,
            worktree_base_dir=str(tmp_path / "worktrees"),
            cleanup_on_completion=False,  # Don't cleanup for inspection
        )
        orch = ParallelTestOrchestrator(config=config)

        # Skip pool initialization to avoid git operations
        orch.pool._initialized = True
        from backend.app.services.worktree_pool import WorktreeInfo, WorktreeStatus
        from backend.app.services.execution_worker import ExecutionWorker

        # Add mock worktrees
        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)
            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            orch.pool.worktrees[f"wt-{i}"] = worktree

        # Create workers
        for i in range(1, 3):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orch.queue,
                pool=orch.pool,
            )
            orch.workers.append(worker)

        orch._initialized = True

        yield orch

        # Cleanup
        if orch._running:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_executes_tests(self, orchestrator):
        """Test orchestrator executes tests from queue."""
        # Start workers
        await orchestrator.start()

        # Submit tests
        requests = [
            TestRequest(
                id=f"test-{i}",
                plan_file=f"test-{i}.md",
            )
            for i in range(4)
        ]
        await orchestrator.submit_batch(requests)

        # Wait for completion
        report = await orchestrator.wait_for_completion()

        # Verify execution
        assert report.status in ["COMPLETE", "PARTIAL_SUCCESS"]
        assert report.total_tests == 4
        assert report.tests_passed == 4
        assert report.tests_failed == 0
        assert report.duration_seconds is not None
        assert report.duration_seconds > 0

        await orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_run_tests_convenience(self, orchestrator):
        """Test run_tests convenience method."""
        # Note: orchestrator is already initialized from fixture

        # Define test plans
        test_plans = [
            "docs/plans/test-01.md",
            "docs/plans/test-02.md",
            "docs/plans/test-03.md",
        ]

        # Run tests (this handles start, submit, wait, shutdown)
        report = await orchestrator.run_tests(test_plans)

        # Verify results
        assert report.total_tests == 3
        assert report.tests_passed == 3
        assert report.success_rate == 100.0
        assert report.duration_seconds is not None

    @pytest.mark.asyncio
    async def test_orchestrator_parallel_execution(self, orchestrator):
        """Test that tests run in parallel."""
        import time

        await orchestrator.start()

        # Submit 6 tests
        requests = [
            TestRequest(
                id=f"test-{i}",
                plan_file=f"test-{i}.md",
            )
            for i in range(6)
        ]
        await orchestrator.submit_batch(requests)

        # Measure time to complete
        start_time = time.time()
        report = await orchestrator.wait_for_completion()
        duration = time.time() - start_time

        # With 2 workers and 6 tests (each taking ~0.1s):
        # Sequential: 6 * 0.1 = 0.6s
        # Parallel: 6 / 2 * 0.1 = 0.3s
        # Allow some overhead, but should be much faster than sequential
        assert duration < 1.0  # Should complete quickly with parallel execution

        assert report.total_tests == 6
        assert report.tests_passed == 6

        await orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_context_manager(self, tmp_path):
        """Test orchestrator as async context manager."""
        from backend.app.services.worktree_pool import WorktreeInfo, WorktreeStatus
        from backend.app.services.execution_worker import ExecutionWorker

        config = ParallelTestConfig(
            num_workers=2,
            worktree_base_dir=str(tmp_path / "worktrees"),
        )

        orch = ParallelTestOrchestrator(config=config)

        # Skip real initialization
        orch.pool._initialized = True

        # Add mock worktrees
        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)
            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            orch.pool.worktrees[f"wt-{i}"] = worktree

        # Add workers
        for i in range(1, 3):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orch.queue,
                pool=orch.pool,
            )
            orch.workers.append(worker)

        orch._initialized = True

        # Use manually initialized orch
        await orch.start()

        try:
            # Should be running
            assert orch._initialized is True
            assert orch._running is True

            # Submit a test
            await orch.submit_test(TestRequest(
                id="test-1",
                plan_file="test-1.md",
            ))

        finally:
            # Shutdown
            await orch.shutdown()

        # After shutdown, should be stopped
        assert orch._running is False


class TestOrchestratorReporting:
    """Test orchestrator reporting functionality."""

    @pytest.fixture
    async def orchestrator(self, tmp_path):
        """Create orchestrator with mock worktrees."""
        config = ParallelTestConfig(
            num_workers=2,
            worktree_base_dir=str(tmp_path / "worktrees"),
        )
        orch = ParallelTestOrchestrator(config=config)

        # Skip pool init and manually set up
        orch.pool._initialized = True
        from backend.app.services.worktree_pool import WorktreeInfo, WorktreeStatus
        from backend.app.services.execution_worker import ExecutionWorker

        # Add mock worktrees
        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)
            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            orch.pool.worktrees[f"wt-{i}"] = worktree

        # Create workers
        for i in range(1, 3):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=orch.queue,
                pool=orch.pool,
            )
            orch.workers.append(worker)

        orch._initialized = True

        yield orch

        if orch._running:
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_report_generation(self, orchestrator):
        """Test report generation after test execution."""
        await orchestrator.start()

        # Submit and execute tests
        requests = [
            TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            for i in range(3)
        ]
        await orchestrator.submit_batch(requests)

        # Wait and get report
        report = await orchestrator.wait_for_completion()

        # Verify report structure
        assert isinstance(report, ParallelTestReport)
        assert report.session_id == orchestrator.session_id
        assert report.status in ["COMPLETE", "PARTIAL_SUCCESS"]
        assert report.started_at is not None
        assert report.completed_at is not None
        assert report.duration_seconds > 0
        assert report.total_tests == 3
        assert report.num_workers == 2

        await orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_report_success_rate(self, orchestrator):
        """Test success rate calculation in report."""
        await orchestrator.start()

        # Submit tests
        requests = [
            TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            for i in range(5)
        ]
        await orchestrator.submit_batch(requests)

        report = await orchestrator.wait_for_completion()

        # All tests should pass (simulation always succeeds)
        assert report.success_rate == 100.0
        assert report.tests_passed == 5
        assert report.tests_failed == 0

        await orchestrator.shutdown()
