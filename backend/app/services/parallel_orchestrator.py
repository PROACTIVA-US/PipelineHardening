"""Parallel Test Orchestrator - Manages parallel test execution across workers."""

import asyncio
import logging
import uuid
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .test_queue import TestQueue, TestRequest, TestResult, TestHarnessConfig
from .worktree_pool import WorktreePool
from .execution_worker import ExecutionWorker

logger = logging.getLogger(__name__)


@dataclass
class ParallelTestConfig:
    """Configuration for parallel test execution."""

    # Worktree settings
    num_workers: int = 3
    worktree_base_dir: str = "../PipelineHardening-worktrees"

    # Queue settings
    max_queue_size: int = 100
    max_retries_per_test: int = 2

    # Worker settings
    worker_task_timeout_seconds: float = 1800.0  # 30 minutes per task
    worktree_acquire_timeout_seconds: float = 300.0  # 5 minutes to acquire worktree

    # Cleanup settings
    cleanup_on_completion: bool = True
    preserve_failed_worktrees: bool = False

    # Test harness config (per test)
    default_test_config: TestHarnessConfig = field(default_factory=TestHarnessConfig)


@dataclass
class ParallelTestReport:
    """Summary report of parallel test execution."""

    session_id: str
    status: str  # RUNNING, COMPLETE, FAILED

    # Timing
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    # Summary stats
    total_tests: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    success_rate: float = 0.0

    # Test results
    completed_tests: List[TestResult] = field(default_factory=list)
    failed_tests: List[TestResult] = field(default_factory=list)

    # Worker stats
    num_workers: int = 0
    worker_utilization: float = 0.0


class ParallelTestOrchestrator:
    """
    Orchestrates parallel test execution across multiple workers.

    Manages the full lifecycle:
    1. Initialize worktree pool
    2. Start workers
    3. Submit tests to queue
    4. Monitor execution
    5. Aggregate results
    6. Cleanup resources
    """

    def __init__(
        self,
        config: Optional[ParallelTestConfig] = None,
    ):
        """
        Initialize parallel test orchestrator.

        Args:
            config: Configuration for parallel execution
        """
        self.config = config or ParallelTestConfig()
        self.session_id = str(uuid.uuid4())

        # Core components
        self.queue = TestQueue(max_size=self.config.max_queue_size)
        self.pool = WorktreePool(
            pool_size=self.config.num_workers,
            base_dir=self.config.worktree_base_dir,
        )
        self.workers: List[ExecutionWorker] = []

        # State
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self._initialized = False
        self._running = False

        logger.info(f"Created ParallelTestOrchestrator (session: {self.session_id})")

    async def initialize(self) -> None:
        """
        Initialize the orchestrator: create worktrees and workers.

        This must be called before starting execution.
        """
        if self._initialized:
            logger.warning("Orchestrator already initialized")
            return

        logger.info(f"Initializing orchestrator with {self.config.num_workers} workers")

        # Initialize worktree pool
        await self.pool.initialize()

        # Create workers with configured timeouts
        for i in range(1, self.config.num_workers + 1):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=self.queue,
                pool=self.pool,
                task_timeout_seconds=self.config.worker_task_timeout_seconds,
                worktree_acquire_timeout=self.config.worktree_acquire_timeout_seconds,
            )
            self.workers.append(worker)

        self._initialized = True
        logger.info(f"Orchestrator initialized: {len(self.workers)} workers, {self.pool.pool_size} worktrees")

    async def start(self) -> None:
        """
        Start all workers.

        Workers will begin processing tests from the queue.
        """
        if not self._initialized:
            raise Exception("Orchestrator not initialized. Call initialize() first.")

        if self._running:
            logger.warning("Orchestrator already running")
            return

        logger.info("Starting all workers...")
        self.started_at = datetime.now(timezone.utc)

        # Start all workers
        for worker in self.workers:
            await worker.start()

        self._running = True
        logger.info(f"All {len(self.workers)} workers started")

    async def submit_test(self, test_request: TestRequest) -> None:
        """
        Submit a single test to the queue.

        Args:
            test_request: Test request to execute
        """
        await self.queue.enqueue(test_request)
        logger.info(f"Submitted test: {test_request.plan_file} (id: {test_request.id})")

    async def submit_batch(self, test_requests: List[TestRequest]) -> None:
        """
        Submit multiple tests at once.

        Args:
            test_requests: List of test requests to execute
        """
        await self.queue.enqueue_batch(test_requests)
        logger.info(f"Submitted batch of {len(test_requests)} tests")

    async def run_tests(
        self,
        test_plans: List[str],
        config: Optional[TestHarnessConfig] = None,
    ) -> ParallelTestReport:
        """
        Convenience method: initialize, start, submit tests, wait, and cleanup.

        Args:
            test_plans: List of test plan file paths
            config: Test harness configuration (uses default if None)

        Returns:
            ParallelTestReport with execution summary
        """
        # Initialize if needed
        if not self._initialized:
            await self.initialize()

        # Start workers if needed
        if not self._running:
            await self.start()

        # Create test requests
        test_config = config or self.config.default_test_config
        requests = [
            TestRequest(
                id=f"test-{i}",
                plan_file=plan,
                batch_range="all",
                config=test_config,
                max_retries=self.config.max_retries_per_test,
            )
            for i, plan in enumerate(test_plans)
        ]

        # Submit tests
        await self.submit_batch(requests)

        # Wait for completion
        report = await self.wait_for_completion()

        # Cleanup if configured
        if self.config.cleanup_on_completion:
            await self.shutdown()

        return report

    async def wait_for_completion(self) -> ParallelTestReport:
        """
        Wait for all queued tests to complete.

        Returns:
            ParallelTestReport with execution summary
        """
        logger.info("Waiting for all tests to complete...")

        # Wait until queue is empty and no tests running
        await self.queue.wait_until_empty()

        self.completed_at = datetime.now(timezone.utc)
        logger.info("All tests completed")

        # Generate and return report
        return self._generate_report()

    async def shutdown(self) -> None:
        """
        Stop all workers and cleanup resources.

        Gracefully stops workers and cleans up worktrees.
        """
        logger.info("Shutting down orchestrator...")

        # Stop all workers
        for worker in self.workers:
            try:
                await worker.stop()
            except Exception as e:
                logger.error(f"Error stopping worker {worker.worker_id}: {e}")

        # Cleanup worktrees (unless preserving failed ones)
        if self.config.cleanup_on_completion:
            if self.config.preserve_failed_worktrees and self.queue.failed:
                logger.info("Preserving worktrees for failed tests")
            else:
                await self.pool.cleanup()

        self._running = False
        logger.info("Orchestrator shutdown complete")

    def _generate_report(self) -> ParallelTestReport:
        """
        Generate summary report of execution.

        Returns:
            ParallelTestReport with statistics and results
        """
        # Get results summary from queue
        summary = self.queue.get_results_summary()

        # Calculate duration
        duration = None
        if self.started_at and self.completed_at:
            duration = (self.completed_at - self.started_at).total_seconds()

        # Determine status based on completion state
        if summary["tests_failed"] > 0:
            status = "PARTIAL_SUCCESS"
        elif summary["total_tests"] == 0:
            status = "NO_TESTS"
        elif self.completed_at is None:
            status = "RUNNING"
        else:
            status = "COMPLETE"

        # Create report
        report = ParallelTestReport(
            session_id=self.session_id,
            status=status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_seconds=duration,
            total_tests=summary["total_tests"],
            tests_passed=summary["tests_passed"],
            tests_failed=summary["tests_failed"],
            success_rate=summary["success_rate"],
            completed_tests=[
                self.queue.completed[r["id"]]
                for r in summary["completed_tests"]
            ],
            failed_tests=[
                self.queue.failed[r["id"]]
                for r in summary["failed_tests"]
            ],
            num_workers=len(self.workers),
        )

        logger.info(
            f"Report generated: {report.tests_passed}/{report.total_tests} passed "
            f"({report.success_rate:.1f}%) in {duration:.1f}s"
        )

        return report

    def get_status(self) -> Dict:
        """
        Get current orchestrator status.

        Returns:
            Dictionary with orchestrator status information
        """
        queue_status = self.queue.get_status()
        pool_status = self.pool.get_status()

        return {
            "session_id": self.session_id,
            "initialized": self._initialized,
            "running": self._running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "queue": queue_status,
            "pool": {
                "num_free": self.pool.num_free,
                "num_busy": self.pool.num_busy,
                "num_error": self.pool.num_error,
                "worktrees": pool_status,
            },
            "workers": [
                worker.get_status()
                for worker in self.workers
            ],
        }

    async def __aenter__(self):
        """Context manager entry: initialize and start."""
        await self.initialize()
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit: shutdown."""
        await self.shutdown()
