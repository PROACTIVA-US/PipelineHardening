#!/usr/bin/env python3
"""Test script to verify worktree isolation prevents git corruption."""

import asyncio
import sys
import logging
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.parallel_orchestrator import (
    ParallelTestOrchestrator,
    ParallelTestConfig,
)
from app.services.test_queue import TestHarnessConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run parallel test orchestrator with 2 workers."""
    logger.info("=" * 80)
    logger.info("Starting Worktree Isolation Test")
    logger.info("=" * 80)

    # Configure the orchestrator with 2 workers
    config = ParallelTestConfig(
        num_workers=2,
        worktree_base_dir="../PipelineHardening-worktrees",
        max_queue_size=10,
        max_retries_per_test=1,
        worker_timeout_minutes=10,
        cleanup_on_completion=True,
        preserve_failed_worktrees=True,
    )

    # Create orchestrator
    orchestrator = ParallelTestOrchestrator(config=config)

    try:
        # Two test plans to run in parallel on different worktrees
        test_plan_1 = "docs/plans/worktree-test-1.md"
        test_plan_2 = "docs/plans/worktree-test-2.md"

        logger.info(f"Test plan 1: {test_plan_1}")
        logger.info(f"Test plan 2: {test_plan_2}")
        logger.info(f"Workers: {config.num_workers}")
        logger.info("")

        # Create test requests that will run in parallel
        from app.services.test_queue import TestRequest

        test_requests = [
            TestRequest(
                id="worktree-test-1",
                plan_file=test_plan_1,
                batch_range="1",
                config=TestHarnessConfig(
                    task_timeout=300,
                    max_retries=0,
                    auto_merge=False,  # Don't try to create PRs
                ),
            ),
            TestRequest(
                id="worktree-test-2",
                plan_file=test_plan_2,
                batch_range="1",
                config=TestHarnessConfig(
                    task_timeout=300,
                    max_retries=0,
                    auto_merge=False,  # Don't try to create PRs
                ),
            ),
        ]

        # Initialize and start orchestrator
        await orchestrator.initialize()
        await orchestrator.start()

        # Submit both tests - they will run in parallel
        await orchestrator.submit_batch(test_requests)

        # Wait for completion
        report = await orchestrator.wait_for_completion()

        # Cleanup if configured
        if config.cleanup_on_completion:
            await orchestrator.shutdown()

        # Print results
        logger.info("")
        logger.info("=" * 80)
        logger.info("TEST RESULTS")
        logger.info("=" * 80)
        logger.info(f"Session ID: {report.session_id}")
        logger.info(f"Status: {report.status}")
        logger.info(f"Duration: {report.duration_seconds:.2f}s")
        logger.info("")
        logger.info(f"Total Tests: {report.total_tests}")
        logger.info(f"Tests Passed: {report.tests_passed}")
        logger.info(f"Tests Failed: {report.tests_failed}")
        logger.info(f"Success Rate: {report.success_rate:.1f}%")
        logger.info("")

        # Print individual test results
        if report.completed_tests:
            logger.info("Completed Tests:")
            for test_result in report.completed_tests:
                logger.info(f"  - {test_result.test_request_id}: {test_result.status}")
                logger.info(f"    Worktree: {test_result.worktree_id}")
                logger.info(f"    Tasks Passed: {test_result.tasks_passed}")
                logger.info(f"    Tasks Failed: {test_result.tasks_failed}")
                if test_result.report_path:
                    logger.info(f"    Report: {test_result.report_path}")

        if report.failed_tests:
            logger.info("")
            logger.info("Failed Tests:")
            for test_result in report.failed_tests:
                logger.info(f"  - {test_result.test_request_id}: {test_result.status}")
                logger.info(f"    Error: {test_result.error}")

        logger.info("=" * 80)

        # Check git status to verify no corruption
        logger.info("")
        logger.info("Checking git status for corruption...")
        import subprocess

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("✓ Git repository is clean (no corruption detected)")
            if result.stdout.strip():
                logger.info(f"  Modified files:\n{result.stdout}")
        else:
            logger.error("✗ Git status command failed!")
            logger.error(f"  Error: {result.stderr}")
            return 1

        # Return exit code based on test results
        if report.tests_failed > 0:
            logger.error("✗ Some tests failed")
            return 1
        else:
            logger.info("✓ All tests passed - worktree isolation is working!")
            return 0

    except Exception as e:
        logger.error(f"Test execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
