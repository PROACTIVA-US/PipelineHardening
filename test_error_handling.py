#!/usr/bin/env python3
"""Test error handling in parallel execution."""

import asyncio
import subprocess
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.worktree_pool import WorktreePool


async def successful_task(worktree_path: Path, task_id: str):
    """A task that succeeds."""
    await asyncio.sleep(1)

    test_file = worktree_path / "test-artifacts" / f"success-{task_id}.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(f"Successful task {task_id}\n")

    subprocess.run(
        ["git", "add", "."],
        cwd=worktree_path,
        capture_output=True,
        check=True,
    )

    subprocess.run(
        ["git", "commit", "-m", f"Success task {task_id}"],
        cwd=worktree_path,
        capture_output=True,
        check=True,
    )

    return True, None


async def failing_task(worktree_path: Path, task_id: str):
    """A task that fails."""
    await asyncio.sleep(1)

    # Simulate a failure
    raise Exception(f"Intentional failure in task {task_id}")


async def run_error_handling_test():
    """Test that errors in one task don't affect others."""
    print("=" * 80)
    print("ERROR HANDLING TEST")
    print("=" * 80)
    print("Goal: Verify one failing task doesn't affect other tasks\n")

    pool = WorktreePool(pool_size=3, base_dir="../PipelineHardening-worktrees")

    try:
        # Initialize
        print("Creating 3 worktrees...")
        await pool.initialize()
        print("✓ Created 3 worktrees\n")

        # Acquire worktrees
        worktrees = []
        for i in range(1, 4):
            wt_id = f"wt-{i}"
            if await pool.acquire(wt_id):
                worktrees.append((wt_id, pool.worktrees[wt_id]))

        print("✓ Acquired 3 worktrees\n")

        # Define tasks: 2 succeed, 1 fails
        print("Executing 3 tasks (2 should succeed, 1 should fail)...")
        print("  - Task 1: Should succeed")
        print("  - Task 2: Should fail (intentional)")
        print("  - Task 3: Should succeed\n")

        async def run_task(wt_id, wt, task_num):
            """Run a task and handle errors."""
            try:
                if task_num == 2:
                    # This task should fail
                    await failing_task(wt.path, f"task-{task_num}")
                    return task_num, True, None
                else:
                    # These tasks should succeed
                    await successful_task(wt.path, f"task-{task_num}")
                    return task_num, True, None
            except Exception as e:
                return task_num, False, str(e)

        tasks = [
            run_task(wt_id, wt, i)
            for i, (wt_id, wt) in enumerate(worktrees, 1)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Analyze results
        print("=" * 80)
        print("TASK RESULTS")
        print("=" * 80)

        success_count = 0
        failure_count = 0

        for task_num, success, error in results:
            if success:
                print(f"✓ Task {task_num}: SUCCESS")
                success_count += 1
            else:
                print(f"✗ Task {task_num}: FAILED - {error}")
                failure_count += 1

        print(f"\nSuccesses: {success_count}")
        print(f"Failures: {failure_count}")

        # Verify expected results
        expected_success = 2
        expected_failure = 1

        results_ok = (success_count == expected_success and
                     failure_count == expected_failure)

        if results_ok:
            print(f"\n✓ EXPECTED RESULTS: {expected_success} succeeded, {expected_failure} failed")
        else:
            print(f"\n✗ UNEXPECTED RESULTS: Expected {expected_success} succeeded, {expected_failure} failed")

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
            print(f"✗ Git integrity check failed: {result.stderr}")
            git_ok = False

        # Verify successful tasks created files
        print(f"\n{'=' * 80}")
        print("FILE VERIFICATION")
        print(f"{'=' * 80}")

        files_ok = True
        for i, (wt_id, wt) in enumerate(worktrees, 1):
            if i != 2:  # Tasks 1 and 3 should have created files
                test_file = wt.path / "test-artifacts" / f"success-task-{i}.txt"
                if test_file.exists():
                    print(f"✓ Task {i}: File created successfully")
                else:
                    print(f"✗ Task {i}: File NOT created")
                    files_ok = False
            else:
                print(f"  Task 2: (Failed - no file expected)")

        # Cleanup
        print(f"\n{'=' * 80}")
        print("CLEANUP")
        print(f"{'=' * 80}")

        await pool.cleanup()
        print("✓ All worktrees cleaned up\n")

        # Overall result
        overall_ok = results_ok and git_ok and files_ok

        print("=" * 80)
        print(f"TEST RESULT: {'✓ PASSED' if overall_ok else '✗ FAILED'}")
        print("=" * 80)

        if overall_ok:
            print("\n✓ Error handling works correctly:")
            print("  - Failed task did not crash the system")
            print("  - Other tasks completed successfully")
            print("  - Git repository remains uncorrupted")
            print("  - Worktrees cleaned up properly")

        return overall_ok

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
    """Run error handling test."""
    success = await run_error_handling_test()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
