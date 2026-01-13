"""Test Queue Manager - Manages queue of test requests for parallel execution."""

import asyncio
import logging
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class TestStatus(Enum):
    """Status of a test execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    RETRY = "retry"


@dataclass
class TestHarnessConfig:
    """Configuration for test harness execution."""
    task_timeout: int = 600  # seconds
    max_retries: int = 2
    auto_merge: bool = True
    github_token: Optional[str] = None


@dataclass
class TestRequest:
    """Request to execute a test plan."""
    id: str                                    # Unique test request ID
    plan_file: str                             # "docs/plans/e2e-test-01.md"
    batch_range: str = "all"                   # "all" or "1-3"
    config: TestHarnessConfig = field(default_factory=TestHarnessConfig)
    priority: int = 0                          # Higher = run first
    retry_count: int = 0                       # Number of retries attempted
    max_retries: int = 2                       # Max retries allowed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TestResult:
    """Result of a test execution."""
    test_request_id: str
    worktree_id: str
    status: str                                # COMPLETE, FAILED
    tasks_passed: int = 0
    tasks_failed: int = 0
    report_path: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


class TestQueue:
    """
    Manages queue of test requests and their execution status.

    Provides async queue operations for enqueueing tests, dequeueing for workers,
    and tracking test status throughout execution lifecycle.
    """

    def __init__(self, max_size: int = 100):
        """
        Initialize test queue.

        Args:
            max_size: Maximum number of tests that can be queued
        """
        self.max_size = max_size
        self.pending: asyncio.Queue[TestRequest] = asyncio.Queue(maxsize=max_size)
        self.running: Dict[str, TestRequest] = {}
        self.completed: Dict[str, TestResult] = {}
        self.failed: Dict[str, TestResult] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, test_request: TestRequest) -> None:
        """
        Add test to the queue.

        Args:
            test_request: Test request to add to queue

        Raises:
            asyncio.QueueFull: If queue is at max capacity
        """
        async with self._lock:
            # Check if test already exists in any state
            if self._test_exists(test_request.id):
                logger.warning(f"Test {test_request.id} already in queue")
                return

            await self.pending.put(test_request)
            logger.info(f"Enqueued test: {test_request.plan_file} (id: {test_request.id})")

    async def enqueue_batch(self, test_requests: list[TestRequest]) -> None:
        """
        Add multiple tests to the queue.

        Args:
            test_requests: List of test requests to enqueue
        """
        for request in test_requests:
            await self.enqueue(request)

        logger.info(f"Enqueued batch of {len(test_requests)} tests")

    async def dequeue(self) -> TestRequest:
        """
        Get next test from queue (blocks if empty).

        Returns:
            Next test request to execute
        """
        test_request = await self.pending.get()
        logger.debug(f"Dequeued test: {test_request.plan_file}")
        return test_request

    async def mark_running(self, test_request: TestRequest) -> None:
        """
        Mark test as currently running.

        Args:
            test_request: Test request that started execution
        """
        async with self._lock:
            self.running[test_request.id] = test_request
            logger.info(f"Test {test_request.id} marked as running")

    async def mark_complete(self, test_id: str, result: TestResult) -> None:
        """
        Mark test as completed successfully.

        Args:
            test_id: ID of completed test
            result: Test execution result
        """
        async with self._lock:
            if test_id in self.running:
                del self.running[test_id]

            self.completed[test_id] = result
            logger.info(f"Test {test_id} marked as complete")

    async def mark_failed(self, test_id: str, result: TestResult) -> None:
        """
        Mark test as failed.

        Args:
            test_id: ID of failed test
            result: Test execution result with error info
        """
        async with self._lock:
            if test_id in self.running:
                del self.running[test_id]

            self.failed[test_id] = result
            logger.error(f"Test {test_id} marked as failed: {result.error}")

    async def requeue_for_retry(self, test_request: TestRequest) -> bool:
        """
        Requeue a failed test for retry if retries remain.

        Args:
            test_request: Failed test request to retry

        Returns:
            True if requeued, False if max retries exceeded
        """
        async with self._lock:
            if test_request.retry_count >= test_request.max_retries:
                logger.warning(
                    f"Test {test_request.id} exceeded max retries "
                    f"({test_request.retry_count}/{test_request.max_retries})"
                )
                return False

            # Increment retry count
            test_request.retry_count += 1

            # Remove from running if present
            if test_request.id in self.running:
                del self.running[test_request.id]

            # Re-enqueue
            await self.pending.put(test_request)
            logger.info(
                f"Requeued test {test_request.id} for retry "
                f"(attempt {test_request.retry_count + 1}/{test_request.max_retries + 1})"
            )
            return True

    def _test_exists(self, test_id: str) -> bool:
        """Check if test exists in any state."""
        return (
            test_id in self.running or
            test_id in self.completed or
            test_id in self.failed
        )

    def get_status(self) -> dict:
        """
        Get overall queue status.

        Returns:
            Dictionary with queue statistics
        """
        return {
            "pending_count": self.pending.qsize(),
            "running_count": len(self.running),
            "completed_count": len(self.completed),
            "failed_count": len(self.failed),
            "total_processed": len(self.completed) + len(self.failed),
            "running_tests": [
                {
                    "id": req.id,
                    "plan_file": req.plan_file,
                    "retry_count": req.retry_count,
                }
                for req in self.running.values()
            ],
        }

    async def wait_until_empty(self) -> None:
        """Wait until queue is empty and no tests are running."""
        while not self.pending.empty() or len(self.running) > 0:
            await asyncio.sleep(0.5)

        logger.info("Queue is empty and no tests running")

    def get_results_summary(self) -> dict:
        """
        Get summary of all test results.

        Returns:
            Dictionary with test results summary
        """
        total_tests = len(self.completed) + len(self.failed)

        return {
            "total_tests": total_tests,
            "tests_passed": len(self.completed),
            "tests_failed": len(self.failed),
            "success_rate": (
                len(self.completed) / total_tests * 100 if total_tests > 0 else 0
            ),
            "completed_tests": [
                {
                    "id": result.test_request_id,
                    "worktree_id": result.worktree_id,
                    "tasks_passed": result.tasks_passed,
                    "tasks_failed": result.tasks_failed,
                    "duration_seconds": result.duration_seconds,
                }
                for result in self.completed.values()
            ],
            "failed_tests": [
                {
                    "id": result.test_request_id,
                    "worktree_id": result.worktree_id,
                    "error": result.error,
                    "tasks_passed": result.tasks_passed,
                    "tasks_failed": result.tasks_failed,
                }
                for result in self.failed.values()
            ],
        }

    async def clear(self) -> None:
        """Clear all queue state (for testing/cleanup)."""
        async with self._lock:
            # Clear pending queue
            while not self.pending.empty():
                try:
                    self.pending.get_nowait()
                except asyncio.QueueEmpty:
                    break

            self.running.clear()
            self.completed.clear()
            self.failed.clear()

            logger.info("Queue cleared")
