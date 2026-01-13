"""Test script for worktree pool manager."""

import asyncio
import logging
from app.services.worktree_pool import WorktreePool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def test_worktree_pool():
    """Test basic worktree pool operations."""
    print("\n" + "="*60)
    print("Testing Worktree Pool Manager")
    print("="*60 + "\n")

    # Create pool
    pool = WorktreePool(pool_size=3)

    try:
        # Initialize
        print("1. Initializing pool...")
        await pool.initialize()
        print(f"   ✓ Created {pool.pool_size} worktrees\n")

        # Check status
        print("2. Checking initial status...")
        status = pool.get_status()
        for wt_id, info in status.items():
            print(f"   {wt_id}: {info['status']} (path: {info['path']})")
        print(f"   Free: {pool.num_free}, Busy: {pool.num_busy}\n")

        # Acquire worktrees
        print("3. Acquiring worktrees...")
        wt1 = await pool.acquire(test_name="test-01.md")
        print(f"   ✓ Acquired {wt1.id} for test-01.md")

        wt2 = await pool.acquire(test_name="test-02.md")
        print(f"   ✓ Acquired {wt2.id} for test-02.md")

        print(f"   Free: {pool.num_free}, Busy: {pool.num_busy}\n")

        # Release one
        print("4. Releasing worktree...")
        await pool.release(wt1)
        print(f"   ✓ Released {wt1.id}")
        print(f"   Free: {pool.num_free}, Busy: {pool.num_busy}\n")

        # Release another
        print("5. Releasing worktree...")
        await pool.release(wt2)
        print(f"   ✓ Released {wt2.id}")
        print(f"   Free: {pool.num_free}, Busy: {pool.num_busy}\n")

        # Final status
        print("6. Final status:")
        for wt_id, info in pool.get_status().items():
            print(f"   {wt_id}: {info['status']}")

        print("\n✓ All tests passed!\n")

    finally:
        # Cleanup
        print("7. Cleaning up...")
        await pool.cleanup()
        print("   ✓ Pool cleaned up\n")


if __name__ == "__main__":
    asyncio.run(test_worktree_pool())
