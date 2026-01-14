"""Execution Worker - Worker process for executing tests in parallel."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from .test_queue import TestQueue, TestRequest, TestResult, TestStatus
from .worktree_pool import WorktreePool, WorktreeInfo, WorktreeAcquisitionTimeout
from .plan_parser import PlanParser, PlanParseError
from .task_executor import TaskExecutor

logger = logging.getLogger(__name__)


class WorkerTaskTimeout(Exception):
    """Raised when a worker task exceeds its timeout."""
    pass


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
        task_timeout_seconds: float = 1800.0,  # 30 minutes default
        worktree_acquire_timeout: float = 300.0,  # 5 minutes default
    ):
        """
        Initialize execution worker.

        Args:
            worker_id: Unique identifier for this worker (e.g., "worker-1")
            queue: Test queue to pull tests from
            pool: Worktree pool to acquire worktrees from
            task_timeout_seconds: Maximum time for a single task execution (default: 30 min)
            worktree_acquire_timeout: Maximum time to wait for a worktree (default: 5 min)
        """
        self.worker_id = worker_id
        self.queue = queue
        self.pool = pool
        self.task_timeout_seconds = task_timeout_seconds
        self.worktree_acquire_timeout = worktree_acquire_timeout
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._current_test: Optional[str] = None
        self._current_test_started: Optional[datetime] = None

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

        # Track current test for watchdog
        self._current_test = test_request.id
        self._current_test_started = datetime.now(timezone.utc)

        # 2. Mark test as running
        await self.queue.mark_running(test_request)

        worktree: Optional[WorktreeInfo] = None

        try:
            # 3. Acquire worktree from pool (with timeout)
            try:
                worktree = await self.pool.acquire(
                    test_name=test_request.plan_file,
                    timeout=self.worktree_acquire_timeout,
                )
                logger.info(
                    f"Worker {self.worker_id} acquired worktree {worktree.id} "
                    f"for test {test_request.id}"
                )
            except WorktreeAcquisitionTimeout as e:
                raise WorkerTaskTimeout(f"Failed to acquire worktree: {e}")

            # 4. Execute test in worktree (with timeout)
            try:
                result = await asyncio.wait_for(
                    self._execute_test(test_request, worktree),
                    timeout=self.task_timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise WorkerTaskTimeout(
                    f"Task execution exceeded timeout of {self.task_timeout_seconds}s"
                )

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

        except WorkerTaskTimeout as e:
            # Timeout-specific handling
            logger.error(f"Worker {self.worker_id} task timeout: {e}")

            result = TestResult(
                test_request_id=test_request.id,
                worktree_id=worktree.id if worktree else "unknown",
                status="FAILED",
                error=f"Timeout: {str(e)}",
                started_at=self._current_test_started,
                completed_at=datetime.now(timezone.utc),
            )

            # Don't retry timeouts - they'll likely timeout again
            await self.queue.mark_failed(test_request.id, result)

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
            # Clear current test tracking
            self._current_test = None
            self._current_test_started = None

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
            # Execute tasks from the plan in the worktree
            result = await self._run_tasks_in_worktree(
                test_request, worktree, started_at
            )

            duration_str = f"{result.duration_seconds:.1f}s" if result.duration_seconds else "N/A"
            logger.info(
                f"Worker {self.worker_id} test {test_request.id} completed "
                f"in {duration_str}"
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

    async def _run_tasks_in_worktree(
        self,
        test_request: TestRequest,
        worktree: WorktreeInfo,
        started_at: datetime,
    ) -> TestResult:
        """
        Execute tasks from the plan file in the worktree.

        Args:
            test_request: Test request containing plan file path
            worktree: Worktree to execute in (provides isolation)
            started_at: Test start time

        Returns:
            TestResult with execution details
        """
        tasks_passed = 0
        tasks_failed = 0
        error_msg = None

        try:
            # 1. Parse the plan file to get tasks
            logger.info(f"Worker {self.worker_id} parsing plan: {test_request.plan_file}")
            parser = PlanParser(test_request.plan_file)
            batches = parser.parse()

            if not batches:
                raise PlanParseError("No batches found in plan file")

            # 2. Create TaskExecutor with worktree path
            executor = TaskExecutor(
                repo_path=str(worktree.path),
                github_token=test_request.config.github_token,
            )

            # 3. Execute each task in each batch
            for batch in batches:
                logger.info(
                    f"Worker {self.worker_id} executing batch {batch.number}: "
                    f"{batch.title} ({len(batch.tasks)} tasks)"
                )

                for task in batch.tasks:
                    logger.info(
                        f"Worker {self.worker_id} executing task {task.number}: {task.title}"
                    )

                    try:
                        result = await executor.execute_task(
                            task_number=task.number,
                            task_title=task.title,
                            implementation=task.implementation,
                            files=task.files,
                            verification_steps=task.verification_steps,
                            batch_number=batch.number,
                            auto_merge=test_request.config.auto_merge,
                            worktree_path=worktree.path,
                            branch_name=worktree.branch,
                        )

                        if result.success:
                            tasks_passed += 1
                            logger.info(
                                f"Worker {self.worker_id} task {task.number} PASSED "
                                f"(PR: {result.pr_url})"
                            )
                        else:
                            tasks_failed += 1
                            logger.error(
                                f"Worker {self.worker_id} task {task.number} FAILED: "
                                f"{result.error}"
                            )

                    except Exception as e:
                        tasks_failed += 1
                        logger.error(
                            f"Worker {self.worker_id} task {task.number} exception: {e}"
                        )

        except PlanParseError as e:
            error_msg = f"Plan parse error: {e}"
            logger.error(f"Worker {self.worker_id} {error_msg}")

        except Exception as e:
            error_msg = f"Execution error: {e}"
            logger.error(f"Worker {self.worker_id} {error_msg}")

        completed_at = datetime.now(timezone.utc)
        duration = (completed_at - started_at).total_seconds()

        status = "COMPLETE" if tasks_failed == 0 and not error_msg else "FAILED"

        return TestResult(
            test_request_id=test_request.id,
            worktree_id=worktree.id,
            status=status,
            tasks_passed=tasks_passed,
            tasks_failed=tasks_failed,
            error=error_msg,
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
        status = {
            "worker_id": self.worker_id,
            "running": self.running,
            "task_done": self._task.done() if self._task else True,
            "current_test": self._current_test,
            "task_timeout_seconds": self.task_timeout_seconds,
        }

        # Add elapsed time if processing a test
        if self._current_test_started:
            elapsed = (datetime.now(timezone.utc) - self._current_test_started).total_seconds()
            status["current_test_elapsed_seconds"] = elapsed
            status["current_test_started"] = self._current_test_started.isoformat()

            # Warn if approaching timeout
            if elapsed > self.task_timeout_seconds * 0.8:
                status["warning"] = "Approaching task timeout"

        return status
