#!/usr/bin/env python3
"""Fast parallel execution test using mock task execution."""

import asyncio
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.worktree_pool import WorktreePool
from app.services.test_queue import TestQueue, TestRequest, TestResult, TestHarnessConfig
from app.services.execution_worker import ExecutionWorker


async def mock_task_execution(worktree_path: Path, task_id: str, duration: float = 2.0):
    """Mock task execution that creates a file and simulates work."""
    # Simulate work
    await asyncio.sleep(duration)

    # Create a test file
    test_file = worktree_path / "test-artifacts" / f"task-{task_id}.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(f"Mock task {task_id}\nCompleted at {datetime.now().isoformat()}\n")

    # Commit the change
    result = subprocess.run(
        ["git", "add", "."],
        cwd=worktree_path,
        capture_output=True,
    )

    if result.returncode != 0:
        raise Exception(f"Git add failed: {result.stderr.decode()}")

    result = subprocess.run(
        ["git", "commit", "-m", f"Mock task {task_id}"],
        cwd=worktree_path,
        capture_output=True,
    )

    if result.returncode != 0:
        raise Exception(f"Git commit failed: {result.stderr.decode()}")

    return True


async def run_parallel_test(num_tasks: int, num_workers: int, task_duration: float = 2.0):
    """Run a parallel test with specified number of tasks and workers."""
    print(f"\n{'=' * 80}")
    print(f"PARALLEL TEST: {num_tasks} tasks on {num_workers} workers")
    print(f"{'=' * 80}")

    # Create components
    queue = TestQueue()
    pool = WorktreePool(pool_size=num_workers, base_dir="../PipelineHardening-worktrees")

    try:
        # Initialize pool
        print(f"Initializing {num_workers} worktrees...")
        await pool.initialize()
        print(f"✓ Created {num_workers} worktrees")

        # Create workers that will use mock execution
        workers = []
        for i in range(1, num_workers + 1):
            worker = ExecutionWorker(
                worker_id=f"worker-{i}",
                queue=queue,
                pool=pool,
            )
            workers.append(worker)

        # Start workers
        print("Starting workers...")
        for worker in workers:
            await worker.start()

        # Submit tasks
        print(f"Submitting {num_tasks} tasks...")
        task_start_times = {}

        for i in range(1, num_tasks + 1):
            # Create a mock test request
            # In a real scenario, this would reference a plan file
            # For this mock test, we'll handle it differently
            request = TestRequest(
                id=f"task-{i}",
                plan_file=f"mock-plan-{i}.md",  # Mock plan file
                batch_range="1",
                config=TestHarnessConfig(
                    task_timeout=30,
                    max_retries=0,
                    auto_merge=False,
                ),
            )
            await queue.enqueue(request)
            task_start_times[f"task-{i}"] = time.time()

        # Record overall start time
        overall_start = time.time()

        # Instead of using the regular worker loop, manually handle task execution for mocking
        # This is a simplified version just for testing parallel execution
        async def mock_worker_loop(worker_id: str):
            """Mock worker that executes tasks."""
            while True:
                try:
                    # Try to get a task (with timeout)
                    task = await asyncio.wait_for(queue.dequeue(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Check if queue is empty
                    if queue.pending.empty() and len(queue.running) == 0:
                        break
                    continue

                print(f"  [{worker_id}] Starting task {task.id}")
                await queue.mark_running(task)

                # Acquire worktree
                worktree_id = None
                for wt_id, wt in pool.worktrees.items():
                    if await pool.acquire(wt_id):
                        worktree_id = wt_id
                        break

                if not worktree_id:
                    print(f"  [{worker_id}] No worktree available, requeueing")
                    await queue.requeue_for_retry(task)
                    continue

                worktree = pool.worktrees[worktree_id]
                task_actual_start = time.time()

                try:
                    # Execute mock task
                    await mock_task_execution(worktree.path, task.id, task_duration)

                    task_end = time.time()
                    duration = task_end - task_actual_start

                    result = TestResult(
                        test_request_id=task.id,
                        worktree_id=worktree_id,
                        status="COMPLETE",
                        tasks_passed=1,
                        tasks_failed=0,
                        duration_seconds=duration,
                    )

                    await queue.mark_complete(task.id, result)
                    print(f"  [{worker_id}] ✓ Completed task {task.id} in {duration:.2f}s")

                except Exception as e:
                    result = TestResult(
                        test_request_id=task.id,
                        worktree_id=worktree_id,
                        status="FAILED",
                        error=str(e),
                    )
                    await queue.mark_failed(task.id, result)
                    print(f"  [{worker_id}] ✗ Failed task {task.id}: {e}")

                finally:
                    # Release worktree (skip cleanup for speed)
                    worktree.status = "FREE"
                    worktree.current_test = None

        # Stop the regular workers and run mock workers
        for worker in workers:
            await worker.stop()

        # Run mock workers
        print(f"\nExecuting {num_tasks} tasks...")
        worker_tasks = [mock_worker_loop(f"worker-{i}") for i in range(1, num_workers + 1)]
        await asyncio.gather(*worker_tasks)

        # Record overall end time
        overall_end = time.time()
        total_duration = overall_end - overall_start

        # Get results
        summary = queue.get_results_summary()

        print(f"\n{'=' * 80}")
        print("RESULTS")
        print(f"{'=' * 80}")
        print(f"Total Duration: {total_duration:.2f}s")
        print(f"Tasks Passed: {summary['tests_passed']}")
        print(f"Tasks Failed: {summary['tests_failed']}")
        print(f"Success Rate: {summary['success_rate']:.1f}%")

        # Calculate timing analysis
        print(f"\nTiming Analysis:")
        print(f"  Task duration: ~{task_duration:.2f}s each")
        print(f"  Expected sequential time: {num_tasks * task_duration:.2f}s")
        print(f"  Actual parallel time: {total_duration:.2f}s")

        # Calculate expected parallel time based on workers
        expected_parallel = (num_tasks / num_workers) * task_duration
        efficiency = (expected_parallel / total_duration) * 100 if total_duration > 0 else 0

        print(f"  Expected parallel time: {expected_parallel:.2f}s")
        print(f"  Parallel efficiency: {efficiency:.1f}%")

        if total_duration < (num_tasks * task_duration * 0.7):
            print(f"  ✓ Tasks executed in parallel (speedup confirmed)")
        else:
            print(f"  ✗ Tasks may have executed sequentially")

        # Check git integrity
        print(f"\nGit Integrity Check:")
        result = subprocess.run(
            ["git", "fsck", "--no-progress"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print(f"  ✓ Git repository integrity verified")
        else:
            print(f"  ✗ Git integrity check failed: {result.stderr}")
            return False

        # Cleanup
        print(f"\nCleaning up worktrees...")
        await pool.cleanup()
        print(f"  ✓ Cleaned up {num_workers} worktrees")

        return summary['tests_passed'] == num_tasks and summary['tests_failed'] == 0

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        try:
            await pool.cleanup()
        except:
            pass
        return False


async def main():
    """Run comprehensive parallel tests."""
    print("=" * 80)
    print("FAST PARALLEL EXECUTION TEST SUITE")
    print("=" * 80)
    print("Using mock task execution for fast validation")
    print()

    tests = [
        ("2 tasks on 2 workers", 2, 2, 3.0),
        ("3 tasks on 3 workers", 3, 3, 3.0),
        ("4 tasks on 2 workers", 4, 2, 2.0),
        ("6 tasks on 3 workers", 6, 3, 2.0),
    ]

    results = []

    for test_name, num_tasks, num_workers, task_duration in tests:
        success = await run_parallel_test(num_tasks, num_workers, task_duration)
        results.append((test_name, success))

        # Small delay between tests
        await asyncio.sleep(1)

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUITE SUMMARY")
    print("=" * 80)

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for test_name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status} | {test_name}")

    print(f"\nOverall: {passed}/{total} tests passed")

    # Final git check
    print("\nFinal git integrity check...")
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    print(f"  Git status: {'clean' if not result.stdout.strip() else 'has changes'}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
