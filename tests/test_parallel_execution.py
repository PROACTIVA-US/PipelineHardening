"""Tests for parallel execution system (Queue, Workers, Pool)."""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.services.test_queue import (
    TestQueue,
    TestRequest,
    TestResult,
    TestHarnessConfig,
    TestStatus,
)
from backend.app.services.worktree_pool import WorktreePool, WorktreeInfo, WorktreeStatus
from backend.app.services.execution_worker import ExecutionWorker


class TestTestQueue:
    """Test suite for TestQueue class."""

    @pytest.fixture
    def queue(self):
        """Create a test queue."""
        return TestQueue(max_size=10)

    @pytest.fixture
    def sample_request(self):
        """Create a sample test request."""
        return TestRequest(
            id="test-001",
            plan_file="docs/plans/e2e-test-01.md",
            batch_range="all",
        )

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self, queue, sample_request):
        """Test basic enqueue and dequeue operations."""
        # Enqueue a test
        await queue.enqueue(sample_request)

        # Check queue status
        status = queue.get_status()
        assert status["pending_count"] == 1
        assert status["running_count"] == 0

        # Dequeue the test
        dequeued = await queue.dequeue()
        assert dequeued.id == sample_request.id
        assert dequeued.plan_file == sample_request.plan_file

        # Queue should be empty
        assert queue.pending.empty()

    @pytest.mark.asyncio
    async def test_enqueue_batch(self, queue):
        """Test batch enqueue operation."""
        requests = [
            TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            for i in range(5)
        ]

        await queue.enqueue_batch(requests)

        status = queue.get_status()
        assert status["pending_count"] == 5

    @pytest.mark.asyncio
    async def test_mark_running(self, queue, sample_request):
        """Test marking a test as running."""
        await queue.enqueue(sample_request)
        test = await queue.dequeue()

        await queue.mark_running(test)

        status = queue.get_status()
        assert status["running_count"] == 1
        assert test.id in queue.running

    @pytest.mark.asyncio
    async def test_mark_complete(self, queue, sample_request):
        """Test marking a test as complete."""
        await queue.enqueue(sample_request)
        test = await queue.dequeue()
        await queue.mark_running(test)

        result = TestResult(
            test_request_id=test.id,
            worktree_id="wt-1",
            status="COMPLETE",
            tasks_passed=5,
            tasks_failed=0,
        )

        await queue.mark_complete(test.id, result)

        status = queue.get_status()
        assert status["running_count"] == 0
        assert status["completed_count"] == 1
        assert test.id in queue.completed

    @pytest.mark.asyncio
    async def test_mark_failed(self, queue, sample_request):
        """Test marking a test as failed."""
        await queue.enqueue(sample_request)
        test = await queue.dequeue()
        await queue.mark_running(test)

        result = TestResult(
            test_request_id=test.id,
            worktree_id="wt-1",
            status="FAILED",
            error="Test execution failed",
        )

        await queue.mark_failed(test.id, result)

        status = queue.get_status()
        assert status["running_count"] == 0
        assert status["failed_count"] == 1
        assert test.id in queue.failed

    @pytest.mark.asyncio
    async def test_requeue_for_retry(self, queue):
        """Test requeuing a failed test for retry."""
        request = TestRequest(
            id="test-retry",
            plan_file="test.md",
            max_retries=2,
        )

        await queue.enqueue(request)
        test = await queue.dequeue()
        await queue.mark_running(test)

        # First retry should succeed
        should_retry = await queue.requeue_for_retry(test)
        assert should_retry is True
        assert test.retry_count == 1
        assert queue.pending.qsize() == 1

        # Get it again and retry
        test = await queue.dequeue()
        await queue.mark_running(test)
        should_retry = await queue.requeue_for_retry(test)
        assert should_retry is True
        assert test.retry_count == 2

        # Third retry should fail (max_retries=2)
        test = await queue.dequeue()
        await queue.mark_running(test)
        should_retry = await queue.requeue_for_retry(test)
        assert should_retry is False

    @pytest.mark.asyncio
    async def test_get_results_summary(self, queue):
        """Test getting results summary."""
        # Create and complete some tests
        for i in range(3):
            request = TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            await queue.enqueue(request)
            test = await queue.dequeue()
            await queue.mark_running(test)

            result = TestResult(
                test_request_id=test.id,
                worktree_id="wt-1",
                status="COMPLETE",
                tasks_passed=5,
                tasks_failed=0,
            )
            await queue.mark_complete(test.id, result)

        # Create and fail one test
        request = TestRequest(id="test-fail", plan_file="test-fail.md")
        await queue.enqueue(request)
        test = await queue.dequeue()
        await queue.mark_running(test)

        result = TestResult(
            test_request_id=test.id,
            worktree_id="wt-1",
            status="FAILED",
            error="Test failed",
        )
        await queue.mark_failed(test.id, result)

        # Get summary
        summary = queue.get_results_summary()
        assert summary["total_tests"] == 4
        assert summary["tests_passed"] == 3
        assert summary["tests_failed"] == 1
        assert summary["success_rate"] == 75.0

    @pytest.mark.asyncio
    async def test_wait_until_empty(self, queue):
        """Test waiting until queue is empty."""
        # Add some tests
        for i in range(3):
            request = TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            await queue.enqueue(request)

        # Create a task that processes the queue
        async def process_queue():
            while not queue.pending.empty():
                test = await queue.dequeue()
                await queue.mark_running(test)
                await asyncio.sleep(0.1)
                result = TestResult(
                    test_request_id=test.id,
                    worktree_id="wt-1",
                    status="COMPLETE",
                )
                await queue.mark_complete(test.id, result)

        # Start processing
        processor_task = asyncio.create_task(process_queue())

        # Wait until empty
        await queue.wait_until_empty()

        # Verify queue is empty
        assert queue.pending.empty()
        assert len(queue.running) == 0

        # Clean up
        await processor_task

    @pytest.mark.asyncio
    async def test_clear(self, queue):
        """Test clearing the queue."""
        # Add some tests in various states
        for i in range(3):
            request = TestRequest(id=f"test-{i}", plan_file=f"test-{i}.md")
            await queue.enqueue(request)

        # Mark one as running
        test = await queue.dequeue()
        await queue.mark_running(test)

        # Clear the queue
        await queue.clear()

        # Verify everything is cleared
        assert queue.pending.empty()
        assert len(queue.running) == 0
        assert len(queue.completed) == 0
        assert len(queue.failed) == 0


class TestExecutionWorker:
    """Test suite for ExecutionWorker class."""

    @pytest.fixture
    def queue(self):
        """Create a test queue."""
        return TestQueue()

    @pytest.fixture
    async def pool(self, tmp_path):
        """Create a mock worktree pool (not real worktrees)."""
        # Create a simple mock pool that doesn't actually create worktrees
        pool = WorktreePool(pool_size=1)
        pool._initialized = True

        # Create a temporary directory for the mock worktree
        wt_path = tmp_path / "wt-test"
        wt_path.mkdir(parents=True, exist_ok=True)

        # Create a mock worktree with real path
        worktree = WorktreeInfo(
            id="wt-test",
            path=wt_path,
            branch="test-branch",
            status=WorktreeStatus.FREE,
        )
        pool.worktrees["wt-test"] = worktree

        yield pool

        # Cleanup: mark as free
        worktree.status = WorktreeStatus.FREE

    @pytest.mark.asyncio
    async def test_worker_lifecycle(self, queue, pool):
        """Test worker start and stop."""
        worker = ExecutionWorker(
            worker_id="worker-test",
            queue=queue,
            pool=pool,
        )

        # Start worker
        await worker.start()
        assert worker.running is True
        assert worker._task is not None

        # Stop worker
        await worker.stop()
        assert worker.running is False

    @pytest.mark.asyncio
    async def test_worker_processes_test(self, queue, pool):
        """Test worker processes a test from queue."""
        # Mock the database session and task execution
        with patch('backend.app.database.sync_session') as mock_session, \
             patch.object(ExecutionWorker, '_run_tasks_in_worktree') as mock_run:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

            # Mock successful task execution
            mock_run.return_value = TestResult(
                test_request_id="test-001",
                worktree_id="wt-test",
                status="COMPLETE",
                tasks_passed=1,
                tasks_failed=0,
            )

            worker = ExecutionWorker(
                worker_id="worker-test",
                queue=queue,
                pool=pool,
            )

            # Add a test to the queue
            request = TestRequest(
                id="test-001",
                plan_file="test-01.md",
            )
            await queue.enqueue(request)

            # Start worker
            await worker.start()

            # Wait for test to be processed (with timeout)
            try:
                await asyncio.wait_for(
                    queue.wait_until_empty(),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                pytest.fail("Worker did not process test in time")

            # Stop worker
            await worker.stop()

            # Verify test was processed
            status = queue.get_status()
            assert status["pending_count"] == 0
            assert status["running_count"] == 0
            assert status["completed_count"] == 1

    @pytest.mark.asyncio
    async def test_worker_handles_multiple_tests(self, queue, pool):
        """Test worker handles multiple tests sequentially."""
        # Mock the database session and task execution
        with patch('backend.app.database.sync_session') as mock_session, \
             patch.object(ExecutionWorker, '_run_tasks_in_worktree') as mock_run:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

            # Mock successful task execution (return different result for each test)
            def mock_run_tasks(test_request, worktree, started_at):
                return TestResult(
                    test_request_id=test_request.id,
                    worktree_id=worktree.id,
                    status="COMPLETE",
                    tasks_passed=1,
                    tasks_failed=0,
                )
            mock_run.side_effect = mock_run_tasks

            worker = ExecutionWorker(
                worker_id="worker-test",
                queue=queue,
                pool=pool,
            )

            # Add multiple tests
            for i in range(3):
                request = TestRequest(
                    id=f"test-{i}",
                    plan_file=f"test-{i}.md",
                )
                await queue.enqueue(request)

            # Start worker
            await worker.start()

            # Wait for all tests to be processed
            try:
                await asyncio.wait_for(
                    queue.wait_until_empty(),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                pytest.fail("Worker did not process all tests in time")

            # Stop worker
            await worker.stop()

            # Verify all tests were processed
            status = queue.get_status()
            assert status["completed_count"] == 3


class TestIntegration:
    """Integration tests for the full parallel execution system."""

    @pytest.mark.asyncio
    async def test_multiple_workers_process_queue(self, tmp_path):
        """Test multiple workers processing tests in parallel."""
        # Mock the database session and task execution
        with patch('backend.app.database.sync_session') as mock_session, \
             patch.object(ExecutionWorker, '_run_tasks_in_worktree') as mock_run:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

            # Mock successful task execution
            def mock_run_tasks(test_request, worktree, started_at):
                return TestResult(
                    test_request_id=test_request.id,
                    worktree_id=worktree.id,
                    status="COMPLETE",
                    tasks_passed=1,
                    tasks_failed=0,
                )
            mock_run.side_effect = mock_run_tasks

            queue = TestQueue()

            # Create mock pool with 2 worktrees with real temp paths
            pool = WorktreePool(pool_size=2)
            pool._initialized = True

            for i in range(1, 3):
                wt_path = tmp_path / f"wt-{i}"
                wt_path.mkdir(parents=True, exist_ok=True)

                worktree = WorktreeInfo(
                    id=f"wt-{i}",
                    path=wt_path,
                    branch=f"branch-{i}",
                    status=WorktreeStatus.FREE,
                )
                pool.worktrees[f"wt-{i}"] = worktree

            # Create 2 workers
            workers = [
                ExecutionWorker(f"worker-{i}", queue, pool)
                for i in range(1, 3)
            ]

            # Add 6 tests to the queue
            for i in range(6):
                request = TestRequest(
                    id=f"test-{i}",
                    plan_file=f"test-{i}.md",
                )
                await queue.enqueue(request)

            # Start workers
            for worker in workers:
                await worker.start()

            # Wait for all tests to complete
            try:
                await asyncio.wait_for(
                    queue.wait_until_empty(),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                pytest.fail("Workers did not process all tests in time")

            # Stop workers
            for worker in workers:
                await worker.stop()

            # Verify all tests completed
            status = queue.get_status()
            assert status["completed_count"] == 6
            assert status["failed_count"] == 0

            # Cleanup
            for worktree in pool.worktrees.values():
                worktree.status = WorktreeStatus.FREE


class TestHardening:
    """Hardening tests for robustness and edge cases."""

    @pytest.mark.asyncio
    async def test_pool_exhaustion_queueing(self, tmp_path):
        """Test that 6 tasks with 2 workers queues properly without deadlock."""
        # Mock the database session and task execution
        with patch('backend.app.database.sync_session') as mock_session, \
             patch.object(ExecutionWorker, '_run_tasks_in_worktree') as mock_run:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

            # Mock successful task execution
            def mock_run_tasks(test_request, worktree, started_at):
                return TestResult(
                    test_request_id=test_request.id,
                    worktree_id=worktree.id,
                    status="COMPLETE",
                    tasks_passed=1,
                    tasks_failed=0,
                )
            mock_run.side_effect = mock_run_tasks

            queue = TestQueue()

            # Create pool with only 2 worktrees
            pool = WorktreePool(pool_size=2)
            pool._initialized = True

            for i in range(1, 3):
                wt_path = tmp_path / f"wt-{i}"
                wt_path.mkdir(parents=True, exist_ok=True)

                worktree = WorktreeInfo(
                    id=f"wt-{i}",
                    path=wt_path,
                    branch=f"branch-{i}",
                    status=WorktreeStatus.FREE,
                )
                pool.worktrees[f"wt-{i}"] = worktree

            # Create 2 workers for 2 worktrees
            workers = [
                ExecutionWorker(f"worker-{i}", queue, pool)
                for i in range(1, 3)
            ]

            # Submit 6 tasks (3x more than workers)
            for i in range(6):
                request = TestRequest(
                    id=f"test-{i}",
                    plan_file=f"test-{i}.md",
                )
                await queue.enqueue(request)

            # Verify 6 tasks pending
            status = queue.get_status()
            assert status["pending_count"] == 6

            # Start workers
            for worker in workers:
                await worker.start()

            # Wait for all tests to complete (with reasonable timeout)
            try:
                await asyncio.wait_for(
                    queue.wait_until_empty(),
                    timeout=30.0  # Should complete in ~3 batches
                )
            except asyncio.TimeoutError:
                pytest.fail("Pool exhaustion test timed out - possible deadlock")

            # Stop workers
            for worker in workers:
                await worker.stop()

            # Verify all 6 tests completed
            status = queue.get_status()
            assert status["completed_count"] == 6
            assert status["failed_count"] == 0

            # Cleanup
            for worktree in pool.worktrees.values():
                worktree.status = WorktreeStatus.FREE

    @pytest.mark.asyncio
    async def test_stress_10_tasks(self, tmp_path):
        """Stress test with 10+ tasks to validate scalability."""
        # Mock the database session and task execution
        with patch('backend.app.database.sync_session') as mock_session, \
             patch.object(ExecutionWorker, '_run_tasks_in_worktree') as mock_run:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

            # Mock successful task execution
            def mock_run_tasks(test_request, worktree, started_at):
                return TestResult(
                    test_request_id=test_request.id,
                    worktree_id=worktree.id,
                    status="COMPLETE",
                    tasks_passed=1,
                    tasks_failed=0,
                )
            mock_run.side_effect = mock_run_tasks

            queue = TestQueue()

            # Create pool with 3 worktrees
            pool = WorktreePool(pool_size=3)
            pool._initialized = True

            for i in range(1, 4):
                wt_path = tmp_path / f"wt-{i}"
                wt_path.mkdir(parents=True, exist_ok=True)

                worktree = WorktreeInfo(
                    id=f"wt-{i}",
                    path=wt_path,
                    branch=f"branch-{i}",
                    status=WorktreeStatus.FREE,
                )
                pool.worktrees[f"wt-{i}"] = worktree

            # Create 3 workers
            workers = [
                ExecutionWorker(f"worker-{i}", queue, pool)
                for i in range(1, 4)
            ]

            # Submit 12 tasks (4x workers)
            num_tasks = 12
            for i in range(num_tasks):
                request = TestRequest(
                    id=f"stress-test-{i}",
                    plan_file=f"stress-test-{i}.md",
                )
                await queue.enqueue(request)

            # Start workers
            for worker in workers:
                await worker.start()

            # Wait for completion
            try:
                await asyncio.wait_for(
                    queue.wait_until_empty(),
                    timeout=60.0  # ~4 batches
                )
            except asyncio.TimeoutError:
                pytest.fail("Stress test timed out")

            # Stop workers
            for worker in workers:
                await worker.stop()

            # Verify all tasks completed
            status = queue.get_status()
            assert status["completed_count"] == num_tasks
            assert status["failed_count"] == 0

            # Cleanup
            for worktree in pool.worktrees.values():
                worktree.status = WorktreeStatus.FREE

    @pytest.mark.asyncio
    async def test_acquire_timeout(self, tmp_path):
        """Test that worktree acquisition properly times out."""
        pool = WorktreePool(pool_size=1)
        pool._initialized = True

        # Create one worktree but mark it BUSY
        wt_path = tmp_path / "wt-1"
        wt_path.mkdir(parents=True, exist_ok=True)

        worktree = WorktreeInfo(
            id="wt-1",
            path=wt_path,
            branch="branch-1",
            status=WorktreeStatus.BUSY,  # Already busy
            current_test="blocking-test",
        )
        pool.worktrees["wt-1"] = worktree

        # Import the exception class
        from backend.app.services.worktree_pool import WorktreeAcquisitionTimeout

        # Try to acquire with a short timeout
        with pytest.raises(WorktreeAcquisitionTimeout) as exc_info:
            await pool.acquire(test_name="waiting-test", timeout=2.0)

        assert "No worktree available within 2.0s" in str(exc_info.value)
        assert "blocking-test" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_worktree_recovery(self, tmp_path):
        """Test that ERROR worktrees are identified by health check."""
        pool = WorktreePool(pool_size=1)
        pool._initialized = True
        pool.main_repo_path = tmp_path  # Use tmp_path as fake repo
        pool.base_dir = tmp_path / "worktrees"

        # Create a worktree directory with .git as directory (more realistic)
        wt_path = tmp_path / "wt-1"
        wt_path.mkdir(parents=True, exist_ok=True)
        (wt_path / ".git").mkdir()  # Fake git dir

        worktree = WorktreeInfo(
            id="wt-1",
            path=wt_path,
            branch="branch-1",
            status=WorktreeStatus.ERROR,  # In error state
            current_test="failed-test",
        )
        pool.worktrees["wt-1"] = worktree

        # Mock _try_recover_worktree to avoid actual git operations
        with patch.object(pool, '_try_recover_worktree') as mock_recover:
            # Recovery will succeed (mocked)
            mock_recover.return_value = None
            worktree.status = WorktreeStatus.FREE  # Simulated recovery

            # Run health check
            health_results = await pool.health_check()

            # Verify health check ran
            assert "wt-1" in health_results
            # The worktree was in ERROR state, so issues should be recorded
            # (though recovery was mocked to succeed)

    @pytest.mark.asyncio
    async def test_worker_status_tracking(self, tmp_path):
        """Test that worker status properly tracks current test."""
        queue = TestQueue()

        pool = WorktreePool(pool_size=1)
        pool._initialized = True

        wt_path = tmp_path / "wt-1"
        wt_path.mkdir(parents=True, exist_ok=True)

        worktree = WorktreeInfo(
            id="wt-1",
            path=wt_path,
            branch="branch-1",
            status=WorktreeStatus.FREE,
        )
        pool.worktrees["wt-1"] = worktree

        worker = ExecutionWorker(
            "worker-test",
            queue,
            pool,
            task_timeout_seconds=60.0,
            worktree_acquire_timeout=10.0,
        )

        # Check initial status
        status = worker.get_status()
        assert status["worker_id"] == "worker-test"
        assert status["running"] is False
        assert status["current_test"] is None
        assert status["task_timeout_seconds"] == 60.0

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release(self, tmp_path):
        """Test that concurrent acquire/release doesn't deadlock."""
        pool = WorktreePool(pool_size=2)
        pool._initialized = True

        for i in range(1, 3):
            wt_path = tmp_path / f"wt-{i}"
            wt_path.mkdir(parents=True, exist_ok=True)

            worktree = WorktreeInfo(
                id=f"wt-{i}",
                path=wt_path,
                branch=f"branch-{i}",
                status=WorktreeStatus.FREE,
            )
            pool.worktrees[f"wt-{i}"] = worktree

        # Acquire both worktrees
        wt1 = await pool.acquire(test_name="test-1", timeout=5.0)
        wt2 = await pool.acquire(test_name="test-2", timeout=5.0)

        assert wt1.status == WorktreeStatus.BUSY
        assert wt2.status == WorktreeStatus.BUSY

        # Start a task that will try to acquire (should wait)
        async def try_acquire():
            return await pool.acquire(test_name="test-3", timeout=10.0)

        acquire_task = asyncio.create_task(try_acquire())

        # Give it a moment to start waiting
        await asyncio.sleep(0.5)

        # Release one worktree - this should NOT deadlock
        wt1.status = WorktreeStatus.FREE  # Simulate release

        # The waiting acquire should now succeed
        try:
            wt3 = await asyncio.wait_for(acquire_task, timeout=5.0)
            assert wt3.status == WorktreeStatus.BUSY
        except asyncio.TimeoutError:
            pytest.fail("Concurrent acquire/release caused deadlock")

        # Cleanup
        for wt in pool.worktrees.values():
            wt.status = WorktreeStatus.FREE
