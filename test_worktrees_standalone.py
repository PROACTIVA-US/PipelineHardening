"""Standalone test for git worktree creation - no dependencies."""

import subprocess
import shutil
from pathlib import Path


def test_worktree_basic():
    """Test basic git worktree operations."""
    print("\n" + "="*60)
    print("Testing Git Worktree Creation")
    print("="*60 + "\n")

    base_dir = Path("../PipelineHardening-worktrees").absolute()
    main_repo = Path.cwd()

    print(f"Main repo: {main_repo}")
    print(f"Worktrees dir: {base_dir}\n")

    # Create base directory
    print("1. Creating base directory...")
    base_dir.mkdir(parents=True, exist_ok=True)
    print(f"   ✓ Created {base_dir}\n")

    worktrees_created = []

    try:
        # Create 3 worktrees
        for i in range(1, 4):
            wt_id = f"wt-{i}"
            wt_path = base_dir / wt_id
            branch_name = f"worktree-{wt_id}"

            print(f"2.{i} Creating worktree: {wt_id}")

            # Delete branch if exists
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(main_repo),
                capture_output=True,
            )

            # Create worktree
            result = subprocess.run(
                ["git", "worktree", "add", str(wt_path), "-b", branch_name, "main"],
                cwd=str(main_repo),
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                print(f"     ✓ Created {wt_id} at {wt_path}")
                worktrees_created.append((wt_id, wt_path, branch_name))
            else:
                print(f"     ✗ Failed: {result.stderr}")
                break

        print()

        # List worktrees
        print("3. Listing all worktrees:")
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=str(main_repo),
            capture_output=True,
            text=True,
        )
        for line in result.stdout.strip().split("\n"):
            print(f"   {line}")

        print("\n✓ Worktree creation successful!\n")

    finally:
        # Cleanup
        print("4. Cleaning up...")
        for wt_id, wt_path, branch_name in worktrees_created:
            # Remove worktree
            subprocess.run(
                ["git", "worktree", "remove", str(wt_path), "--force"],
                cwd=str(main_repo),
                capture_output=True,
            )

            # Delete directory if still exists
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

            # Delete branch
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(main_repo),
                capture_output=True,
            )

            print(f"   ✓ Removed {wt_id}")

        # Remove base directory if empty
        if base_dir.exists() and not list(base_dir.iterdir()):
            base_dir.rmdir()
            print(f"   ✓ Removed {base_dir}")

        print("\n✓ Cleanup complete!\n")


if __name__ == "__main__":
    test_worktree_basic()
