#!/usr/bin/env python3
"""Test parallel execution timing to verify tasks run in parallel, not sequentially."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.worktree_pool import WorktreePool


async def simulate_task(worktree_path: Path, task_id: str, duration: float):
    """Simulate a task that takes a specific amount of time."""
    start = time.time()

    # Simulate work
    await asyncio.sleep(duration)

    # Create a test file
    test_file = worktree_path / "test-artifacts" / f"parallel-task-{task_id}.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        f"Task ID: {task_id}\n"
        f"Duration: {duration}s\n"
        f"Started: {datetime.fromtimestamp(start).isoformat()}\n"
        f"Ended: {datetime.now().isoformat()}\n"
    )

    # Commit
    subprocess.run(
        ["git", "add", "."],
        cwd=worktree_path,
        capture_output=True,
        check=True,
    )

    subprocess.run(
        ["git", "commit", "-m", f"Parallel task {task_id}"],
        cwd=worktree_path,
        capture_output=True,
        check=True,
    )

    elapsed = time.time() - start
    return task_id, elapsed


async def run_parallel_test(num_tasks: int, task_duration: float = 3.0):
    """Run tasks in parallel and measure timing."""
    print(f"\n{'=' * 80}")
    print(f"Test: {num_tasks} tasks in parallel (each ~{task_duration}s)")
    print(f"{'=' * 80}")

    # Create worktree pool
    pool = WorktreePool(pool_size=num_tasks, base_dir="../PipelineHardening-worktrees")

    try:
        # Initialize
        print(f"Creating {num_tasks} worktrees...")
        await pool.initialize()
        print(f"✓ Created {num_tasks} worktrees")

        # Acquire worktrees
        worktrees = []
        for i in range(1, num_tasks + 1):
            wt_id = f"wt-{i}"
            if await pool.acquire(wt_id):
                worktrees.append((wt_id, pool.worktrees[wt_id]))
            else:
                print(f"✗ Failed to acquire worktree {wt_id}")
                return False

        print(f"✓ Acquired {len(worktrees)} worktrees")

        # Run tasks in parallel
        print(f"\nExecuting {num_tasks} tasks in parallel...")
        overall_start = time.time()

        tasks = [
            simulate_task(wt.path, f"task-{i}", task_duration)
            for i, (wt_id, wt) in enumerate(worktrees, 1)
        ]

        results = await asyncio.gather(*tasks)

        overall_end = time.time()
        total_duration = overall_end - overall_start

        # Analyze results
        print(f"\n{'=' * 80}")
        print("TIMING ANALYSIS")
        print(f"{'=' * 80}")

        for task_id, elapsed in results:
            print(f"  {task_id}: {elapsed:.2f}s")

        print(f"\nSequential time (if run one after another): {num_tasks * task_duration:.2f}s")
        print(f"Actual parallel time: {total_duration:.2f}s")

        # Calculate speedup
        speedup = (num_tasks * task_duration) / total_duration
        efficiency = (speedup / num_tasks) * 100

        print(f"Speedup: {speedup:.2f}x")
        print(f"Parallel efficiency: {efficiency:.1f}%")

        # Verify parallel execution
        if total_duration < (num_tasks * task_duration * 0.7):
            print(f"\n✓ PARALLEL EXECUTION CONFIRMED")
            print(f"  Tasks ran concurrently, not sequentially")
            parallel_ok = True
        else:
            print(f"\n✗ SEQUENTIAL EXECUTION DETECTED")
            print(f"  Tasks appear to have run sequentially")
            parallel_ok = False

        # Check git integrity
        print(f"\n{'=' * 80}")
        print("GIT INTEGRITY CHECK")
        print(f"{'=' * 80}")

        result = subprocess.run(
            ["git", "fsck", "--no-progress"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print("✓ Git repository integrity verified - no corruption")
            git_ok = True
        else:
            print(f"✗ Git integrity check failed:")
            print(f"  {result.stderr}")
            git_ok = False

        # Verify all files were created
        print(f"\n{'=' * 80}")
        print("FILE CREATION VERIFICATION")
        print(f"{'=' * 80}")

        all_files_exist = True
        for wt_id, wt in worktrees:
            task_num = wt_id.split("-")[1]
            test_file = wt.path / "test-artifacts" / f"parallel-task-task-{task_num}.txt"
            if test_file.exists():
                print(f"✓ {test_file.name} created in {wt_id}")
            else:
                print(f"✗ {test_file.name} NOT found in {wt_id}")
                all_files_exist = False

        # Cleanup
        print(f"\n{'=' * 80}")
        print("CLEANUP")
        print(f"{'=' * 80}")

        await pool.cleanup()
        print(f"✓ Cleaned up {num_tasks} worktrees")

        # Return overall success
        success = parallel_ok and git_ok and all_files_exist

        print(f"\n{'=' * 80}")
        print(f"TEST RESULT: {'✓ PASSED' if success else '✗ FAILED'}")
        print(f"{'=' * 80}")

        return success

    except Exception as e:
        print(f"\n✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()

        try:
            await pool.cleanup()
        except:
            pass

        return False


async def main():
    """Run all parallel timing tests."""
    print("=" * 80)
    print("PARALLEL EXECUTION TIMING VALIDATION")
    print("=" * 80)
    print("Goal: Verify tasks execute in parallel, not sequentially")
    print()

    tests = [
        ("2 tasks in parallel", 2, 3.0),
        ("3 tasks in parallel", 3, 3.0),
        ("4 tasks in parallel", 4, 2.0),
    ]

    results = []

    for test_name, num_tasks, task_duration in tests:
        success = await run_parallel_test(num_tasks, task_duration)
        results.append((test_name, success))

        # Delay between tests
        if num_tasks < len(tests):
            await asyncio.sleep(2)

    # Summary
    print(f"\n{'=' * 80}")
    print("OVERALL TEST SUMMARY")
    print(f"{'=' * 80}")

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for test_name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status} | {test_name}")

    print(f"\nTests Passed: {passed}/{total}")

    # Final git check
    print(f"\n{'=' * 80}")
    print("FINAL GIT STATUS")
    print(f"{'=' * 80}")

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )

    status = result.stdout.strip()
    if not status:
        print("✓ Git repository is clean")
    else:
        print("! Git repository has changes (test artifacts)")

    result = subprocess.run(
        ["git", "worktree", "list"],
        capture_output=True,
        text=True,
    )

    worktrees = [line for line in result.stdout.split("\n") if "PipelineHardening-worktrees" in line]
    if worktrees:
        print(f"! Warning: {len(worktrees)} worktrees still exist")
    else:
        print("✓ All worktrees cleaned up")

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
