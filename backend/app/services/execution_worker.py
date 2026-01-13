"""Execution Worker - Worker process for executing tests in parallel."""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from .test_queue import TestQueue, TestRequest, TestResult, TestStatus
from .worktree_pool import WorktreePool, WorktreeInfo

logger = logging.getLogger(__name__)


class ExecutionWorker:
    """
    Worker that executes tests from the queue using worktrees from the pool.

    Each worker runs in its own async task, continuously pulling tests from the queue,
    acquiring a worktree, executing the test, and releasing the worktree back to the pool.
    """

    def __init__(
        self,
        worker_id: str,
        queue: TestQueue,
        pool: WorktreePool,
    ):
        """
        Initialize execution worker.

        Args:
            worker_id: Unique identifier for this worker (e.g., "worker-1")
            queue: Test queue to pull tests from
            pool: Worktree pool to acquire worktrees from
        """
        self.worker_id = worker_id
        self.queue = queue
        self.pool = pool
        self.running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the worker loop in a background task."""
        if self.running:
            logger.warning(f"Worker {self.worker_id} already running")
            return

        self.running = True
        self._task = asyncio.create_task(self.run())
        logger.info(f"Worker {self.worker_id} started")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        if not self.running:
            logger.warning(f"Worker {self.worker_id} not running")
            return

        logger.info(f"Stopping worker {self.worker_id}...")
        self.running = False

        # Cancel the worker task
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(f"Worker {self.worker_id} stopped")

    async def run(self) -> None:
        """
        Main worker loop - runs until stopped.

        Continuously pulls tests from queue, executes them in a worktree, and reports results.
        """
        logger.info(f"Worker {self.worker_id} entering main loop")

        try:
            while self.running:
                await self._process_next_test()

        except asyncio.CancelledError:
            logger.info(f"Worker {self.worker_id} cancelled")
            raise

        except Exception as e:
            logger.error(f"Worker {self.worker_id} encountered unexpected error: {e}")
            raise

        finally:
            logger.info(f"Worker {self.worker_id} exiting main loop")

    async def _process_next_test(self) -> None:
        """Process a single test from the queue."""
        try:
            # 1. Get next test from queue (with timeout to allow checking self.running)
            test_request = await asyncio.wait_for(
                self.queue.dequeue(),
                timeout=1.0
            )

        except asyncio.TimeoutError:
            # No test available, loop again to check if we should stop
            return

        logger.info(
            f"Worker {self.worker_id} got test: {test_request.plan_file} "
            f"(id: {test_request.id})"
        )

        # 2. Mark test as running
        await self.queue.mark_running(test_request)

        worktree: Optional[WorktreeInfo] = None

        try:
            # 3. Acquire worktree from pool
            worktree = await self.pool.acquire(test_name=test_request.plan_file)
            logger.info(
                f"Worker {self.worker_id} acquired worktree {worktree.id} "
                f"for test {test_request.id}"
            )

            # 4. Execute test in worktree
            result = await self._execute_test(test_request, worktree)

            # 5. Handle result - complete or retry
            if result.status == "COMPLETE":
                await self.queue.mark_complete(test_request.id, result)
                logger.info(
                    f"Worker {self.worker_id} completed test {test_request.id}: "
                    f"{result.tasks_passed} passed, {result.tasks_failed} failed"
                )

            elif result.status == "FAILED":
                # Try to retry if retries remain
                should_retry = await self.queue.requeue_for_retry(test_request)

                if not should_retry:
                    # Max retries exceeded, mark as failed
                    await self.queue.mark_failed(test_request.id, result)
                    logger.error(
                        f"Worker {self.worker_id} failed test {test_request.id} "
                        f"after {test_request.retry_count} retries: {result.error}"
                    )

        except Exception as e:
            # Worker-level error (not test execution error)
            logger.error(f"Worker {self.worker_id} error processing test: {e}")

            # Create failure result
            result = TestResult(
                test_request_id=test_request.id,
                worktree_id=worktree.id if worktree else "unknown",
                status="FAILED",
                error=f"Worker error: {str(e)}",
            )

            # Try to retry or mark as failed
            should_retry = await self.queue.requeue_for_retry(test_request)
            if not should_retry:
                await self.queue.mark_failed(test_request.id, result)

        finally:
            # 6. Always release worktree back to pool
            if worktree:
                try:
                    await self.pool.release(worktree)
                    logger.info(
                        f"Worker {self.worker_id} released worktree {worktree.id}"
                    )
                except Exception as e:
                    logger.error(
                        f"Worker {self.worker_id} failed to release worktree "
                        f"{worktree.id}: {e}"
                    )

    async def _execute_test(
        self,
        test_request: TestRequest,
        worktree: WorktreeInfo,
    ) -> TestResult:
        """
        Execute a test in the given worktree.

        Args:
            test_request: Test request to execute
            worktree: Worktree to execute in

        Returns:
            TestResult with execution details
        """
        started_at = datetime.now(timezone.utc)
        logger.info(
            f"Worker {self.worker_id} executing test {test_request.id} "
            f"in worktree {worktree.id}"
        )

        try:
            # TODO: Integrate with actual TestHarnessService
            # For now, simulate test execution
            result = await self._run_test_simulation(
                test_request, worktree, started_at
            )

            logger.info(
                f"Worker {self.worker_id} test {test_request.id} completed "
                f"in {result.duration_seconds:.1f}s"
            )

            return result

        except Exception as e:
            # Test execution failed
            completed_at = datetime.now(timezone.utc)
            duration = (completed_at - started_at).total_seconds()

            logger.error(
                f"Worker {self.worker_id} test {test_request.id} failed: {e}"
            )

            return TestResult(
                test_request_id=test_request.id,
                worktree_id=worktree.id,
                status="FAILED",
                error=str(e),
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
            )

    async def _run_test_simulation(
        self,
        test_request: TestRequest,
        worktree: WorktreeInfo,
        started_at: datetime,
    ) -> TestResult:
        """
        Simulate test execution (placeholder for real implementation).

        Args:
            test_request: Test request to execute
            worktree: Worktree to execute in
            started_at: Test start time

        Returns:
            TestResult with simulated execution details
        """
        # Simulate some work
        await asyncio.sleep(0.1)

        completed_at = datetime.now(timezone.utc)
        duration = (completed_at - started_at).total_seconds()

        return TestResult(
            test_request_id=test_request.id,
            worktree_id=worktree.id,
            status="COMPLETE",
            tasks_passed=5,  # Simulated
            tasks_failed=0,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
        )

    def get_status(self) -> dict:
        """
        Get current worker status.

        Returns:
            Dictionary with worker status information
        """
        return {
            "worker_id": self.worker_id,
            "running": self.running,
            "task_done": self._task.done() if self._task else True,
        }
