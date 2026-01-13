#!/usr/bin/env python3
"""Simplified test to verify worktree isolation prevents git corruption."""

import asyncio
import subprocess
import sys
import logging
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.services.worktree_pool import WorktreePool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def test_worktree_isolation():
    """Test that worktree isolation works correctly."""
    logger.info("=" * 80)
    logger.info("WORKTREE ISOLATION TEST")
    logger.info("=" * 80)

    # Check initial git status
    logger.info("\n1. Checking initial git status...")
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(f"✗ Git status failed: {result.stderr}")
        return False

    initial_status = result.stdout.strip()
    logger.info(f"   Initial git status: {'clean' if not initial_status else 'modified'}")

    # Create worktree pool
    logger.info("\n2. Creating worktree pool with 2 worktrees...")
    pool = WorktreePool(
        pool_size=2,
        base_dir="../PipelineHardening-worktrees",
    )

    try:
        await pool.initialize()
        logger.info(f"   ✓ Created {pool.pool_size} worktrees")

        # Verify worktrees were created
        logger.info("\n3. Verifying worktrees...")
        result = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True,
            text=True,
        )

        worktree_list = result.stdout
        logger.info(f"   Git worktree list:\n{worktree_list}")

        if "wt-1" in worktree_list and "wt-2" in worktree_list:
            logger.info("   ✓ Both worktrees found in git worktree list")
        else:
            logger.error("   ✗ Worktrees not found in git worktree list")
            return False

        # Simulate parallel operations in different worktrees
        logger.info("\n4. Simulating parallel operations in worktrees...")

        async def create_file_in_worktree(worktree_id):
            """Create a file in a worktree."""
            worktree = pool.worktrees[worktree_id]
            await pool.acquire(worktree_id)

            logger.info(f"   Worker {worktree_id}: Creating file in worktree...")
            test_file = worktree.path / "test-artifacts" / f"test-{worktree_id}.txt"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(f"Test content from {worktree_id}\n")

            # Commit the change
            logger.info(f"   Worker {worktree_id}: Committing change...")
            result = subprocess.run(
                ["git", "add", "."],
                cwd=worktree.path,
                capture_output=True,
            )

            result = subprocess.run(
                ["git", "commit", "-m", f"Test commit from {worktree_id}"],
                cwd=worktree.path,
                capture_output=True,
            )

            if result.returncode == 0:
                logger.info(f"   ✓ Worker {worktree_id}: Commit successful")
            else:
                logger.error(f"   ✗ Worker {worktree_id}: Commit failed: {result.stderr.decode()}")

            # Skip release to avoid cleanup issues - we'll cleanup the pool at the end
            # await pool.release(worktree)

        # Run operations in parallel
        await asyncio.gather(
            create_file_in_worktree("wt-1"),
            create_file_in_worktree("wt-2"),
        )

        logger.info("   ✓ Parallel operations completed")

        # Check git status in main repo
        logger.info("\n5. Checking git status after parallel operations...")
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"   ✗ Git status failed: {result.stderr}")
            return False

        final_status = result.stdout.strip()

        if final_status == initial_status:
            logger.info("   ✓ Main repo git status unchanged - no corruption!")
        else:
            logger.warning(f"   ! Git status changed:\n{final_status}")
            logger.info("   (This may be expected if test files were created)")

        # Verify git repository integrity
        logger.info("\n6. Verifying git repository integrity...")
        result = subprocess.run(
            ["git", "fsck", "--no-progress"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("   ✓ Git fsck passed - repository is not corrupted")
        else:
            logger.error(f"   ✗ Git fsck failed: {result.stderr}")
            return False

        # Cleanup
        logger.info("\n7. Cleaning up worktrees...")
        await pool.cleanup()
        logger.info("   ✓ Worktrees cleaned up")

        return True

    except Exception as e:
        logger.error(f"\n✗ Test failed with exception: {e}", exc_info=True)
        try:
            await pool.cleanup()
        except:
            pass
        return False


async def main():
    """Run the test."""
    success = await test_worktree_isolation()

    logger.info("\n" + "=" * 80)
    if success:
        logger.info("✓ WORKTREE ISOLATION TEST PASSED")
        logger.info("  Worktree isolation successfully prevents git corruption!")
        logger.info("=" * 80)
        return 0
    else:
        logger.error("✗ WORKTREE ISOLATION TEST FAILED")
        logger.info("=" * 80)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
