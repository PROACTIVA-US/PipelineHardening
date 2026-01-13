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
        # Mock the database session
        with patch('backend.app.database.sync_session') as mock_session:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

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
        # Mock the database session
        with patch('backend.app.database.sync_session') as mock_session:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

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
        # Mock the database session
        with patch('backend.app.database.sync_session') as mock_session:
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_db.close = Mock()

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
