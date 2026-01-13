#!/usr/bin/env python3
"""Comprehensive parallel execution test suite."""

import asyncio
import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.parallel_orchestrator import (
    ParallelTestOrchestrator,
    ParallelTestConfig,
)
from app.services.test_queue import TestRequest, TestHarnessConfig

# Test configuration
TEST_SCENARIOS = [
    {
        "name": "Test 2: Parallel Execution (2 tasks)",
        "description": "Run 2 tasks in parallel on 2 workers",
        "test_plan": "docs/plans/parallel-test-2tasks.md",
        "num_workers": 2,
        "expected_tasks": 2,
        "should_fail": False,
    },
    {
        "name": "Test 3: Parallel Execution (3 tasks)",
        "description": "Run 3 tasks in parallel on 3 workers",
        "test_plan": "docs/plans/parallel-test-3tasks.md",
        "num_workers": 3,
        "expected_tasks": 3,
        "should_fail": False,
    },
    {
        "name": "Test 4: Error Handling",
        "description": "One task fails, others succeed",
        "test_plan": "docs/plans/error-handling-test.md",
        "num_workers": 2,
        "expected_tasks": 2,
        "should_fail": True,  # One task should fail
        "expected_failures": 1,
    },
    {
        "name": "Test 5: Large File Creation",
        "description": "Create a file with 500+ lines",
        "test_plan": "docs/plans/large-file-test.md",
        "num_workers": 1,
        "expected_tasks": 1,
        "should_fail": False,
    },
    {
        "name": "Test 6: Multi-File Task",
        "description": "Create multiple files in one task",
        "test_plan": "docs/plans/multi-file-test.md",
        "num_workers": 1,
        "expected_tasks": 1,
        "should_fail": False,
    },
]


class TestResult:
    """Result of a single test scenario."""

    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.duration_seconds = 0.0
        self.tasks_passed = 0
        self.tasks_failed = 0
        self.errors = []
        self.timing_data = {}
        self.git_clean = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "duration_seconds": self.duration_seconds,
            "tasks_passed": self.tasks_passed,
            "tasks_failed": self.tasks_failed,
            "errors": self.errors,
            "timing_data": self.timing_data,
            "git_clean": self.git_clean,
        }


async def check_git_integrity() -> tuple[bool, str]:
    """Check git repository integrity."""
    # Check git status
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False, f"Git status failed: {result.stderr}"

    # Check git fsck
    result = subprocess.run(
        ["git", "fsck", "--no-progress"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False, f"Git fsck failed: {result.stderr}"

    return True, "Git repository is clean"


async def run_test_scenario(scenario: Dict[str, Any]) -> TestResult:
    """Run a single test scenario."""
    result = TestResult(scenario["name"])

    print(f"\n{'=' * 80}")
    print(f"{scenario['name']}")
    print(f"{'=' * 80}")
    print(f"Description: {scenario['description']}")
    print(f"Test Plan: {scenario['test_plan']}")
    print(f"Workers: {scenario['num_workers']}")
    print()

    start_time = time.time()

    try:
        # Create orchestrator
        config = ParallelTestConfig(
            num_workers=scenario["num_workers"],
            worktree_base_dir="../PipelineHardening-worktrees",
            max_queue_size=10,
            max_retries_per_test=0,  # No retries for testing
            worker_timeout_minutes=5,
            cleanup_on_completion=True,
            preserve_failed_worktrees=False,
        )

        orchestrator = ParallelTestOrchestrator(config=config)

        # Create test request
        test_request = TestRequest(
            id=f"test-{scenario['name'].lower().replace(' ', '-')}",
            plan_file=scenario["test_plan"],
            batch_range="1",
            config=TestHarnessConfig(
                task_timeout=180,  # 3 minute timeout per task
                max_retries=0,
                auto_merge=False,  # Don't create PRs
            ),
        )

        # Initialize and start
        print("Initializing orchestrator...")
        await orchestrator.initialize()
        await orchestrator.start()

        # Submit test
        print("Submitting test...")
        await orchestrator.submit_test(test_request)

        # Wait for completion
        print("Waiting for completion...")
        report = await orchestrator.wait_for_completion()

        # Cleanup
        print("Cleaning up...")
        await orchestrator.shutdown()

        # Calculate duration
        end_time = time.time()
        result.duration_seconds = end_time - start_time

        # Store results
        result.tasks_passed = report.tests_passed
        result.tasks_failed = report.tests_failed

        # Validate results
        if scenario.get("should_fail"):
            expected_failures = scenario.get("expected_failures", 1)
            if report.tests_failed == expected_failures:
                result.passed = True
                print(f"✓ Expected failure occurred ({report.tests_failed} task(s) failed)")
            else:
                result.passed = False
                result.errors.append(
                    f"Expected {expected_failures} failure(s), got {report.tests_failed}"
                )
        else:
            if report.tests_passed == scenario["expected_tasks"] and report.tests_failed == 0:
                result.passed = True
                print(f"✓ All {report.tests_passed} task(s) passed")
            else:
                result.passed = False
                result.errors.append(
                    f"Expected {scenario['expected_tasks']} passed, "
                    f"got {report.tests_passed} passed, {report.tests_failed} failed"
                )

        # Check git integrity
        print("\nChecking git integrity...")
        git_ok, git_msg = await check_git_integrity()
        result.git_clean = git_ok

        if git_ok:
            print(f"✓ {git_msg}")
        else:
            print(f"✗ {git_msg}")
            result.errors.append(git_msg)
            result.passed = False

        # Print summary
        print(f"\nTest Result: {'✓ PASSED' if result.passed else '✗ FAILED'}")
        print(f"Duration: {result.duration_seconds:.2f}s")
        print(f"Tasks Passed: {result.tasks_passed}")
        print(f"Tasks Failed: {result.tasks_failed}")

        if result.errors:
            print(f"\nErrors:")
            for error in result.errors:
                print(f"  - {error}")

    except Exception as e:
        result.passed = False
        result.errors.append(f"Exception: {str(e)}")
        end_time = time.time()
        result.duration_seconds = end_time - start_time

        print(f"✗ Test failed with exception: {e}")

    return result


async def main():
    """Run all test scenarios."""
    print("=" * 80)
    print("PARALLEL EXECUTION TEST SUITE")
    print("=" * 80)
    print(f"Starting at: {datetime.now().isoformat()}")
    print(f"Total scenarios: {len(TEST_SCENARIOS)}")
    print()

    # Run all tests
    results: List[TestResult] = []

    for scenario in TEST_SCENARIOS:
        result = await run_test_scenario(scenario)
        results.append(result)

        # Small delay between tests
        await asyncio.sleep(2)

    # Generate summary report
    print("\n" + "=" * 80)
    print("TEST SUITE SUMMARY")
    print("=" * 80)

    passed_count = sum(1 for r in results if r.passed)
    failed_count = len(results) - passed_count
    total_duration = sum(r.duration_seconds for r in results)

    print(f"\nTotal Tests: {len(results)}")
    print(f"Passed: {passed_count}")
    print(f"Failed: {failed_count}")
    print(f"Total Duration: {total_duration:.2f}s")
    print()

    # Print individual results
    print("Individual Test Results:")
    print("-" * 80)
    for result in results:
        status = "✓ PASS" if result.passed else "✗ FAIL"
        print(f"{status:8} | {result.name:40} | {result.duration_seconds:6.2f}s")

    # Save detailed results to file
    report_file = Path("test-artifacts/parallel-test-report.json")
    report_file.parent.mkdir(parents=True, exist_ok=True)

    report_data = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_tests": len(results),
            "passed": passed_count,
            "failed": failed_count,
            "total_duration_seconds": total_duration,
        },
        "results": [r.to_dict() for r in results],
    }

    with open(report_file, "w") as f:
        json.dump(report_data, f, indent=2)

    print(f"\nDetailed report saved to: {report_file}")

    # Final git check
    print("\nFinal git integrity check...")
    git_ok, git_msg = await check_git_integrity()
    print(f"  {git_msg}")

    # Return exit code
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
