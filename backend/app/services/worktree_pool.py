"""Worktree Pool Manager - Manages pool of git worktrees for parallel execution."""

import asyncio
import subprocess
import logging
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class WorktreeStatus(Enum):
    """Status of a worktree."""
    FREE = "free"
    BUSY = "busy"
    ERROR = "error"


@dataclass
class WorktreeInfo:
    """Information about a worktree in the pool."""
    id: str                              # "wt-1", "wt-2", etc.
    path: Path                           # /path/to/PipelineHardening-worktrees/wt-1
    branch: str                          # "worktree-wt-1"
    status: WorktreeStatus               # FREE, BUSY, ERROR
    current_test: Optional[str] = None   # Test plan being executed
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None


class WorktreePool:
    """
    Manages a pool of git worktrees for parallel test execution.

    Each worktree is an isolated working directory linked to the main repository,
    allowing multiple tests to run simultaneously without conflicts.
    """

    def __init__(
        self,
        pool_size: int = 3,
        base_dir: str = "../PipelineHardening-worktrees",
        main_repo_path: Optional[str] = None,
    ):
        """
        Initialize worktree pool.

        Args:
            pool_size: Number of worktrees to create
            base_dir: Directory where worktrees will be created
            main_repo_path: Path to main repository (auto-detected if None)
        """
        self.pool_size = pool_size
        self.base_dir = Path(base_dir).absolute()
        self.main_repo_path = Path(main_repo_path) if main_repo_path else Path.cwd()
        self.worktrees: Dict[str, WorktreeInfo] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """
        Create all worktrees in the pool.

        Creates the base directory and initializes each worktree with a unique branch.
        """
        if self._initialized:
            logger.warning("Worktree pool already initialized")
            return

        logger.info(f"Initializing worktree pool with {self.pool_size} worktrees")
        logger.info(f"Base directory: {self.base_dir}")
        logger.info(f"Main repo: {self.main_repo_path}")

        # Create base directory
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Create each worktree
        for i in range(1, self.pool_size + 1):
            wt_id = f"wt-{i}"
            try:
                await self._create_worktree(wt_id)
                logger.info(f"✓ Created worktree: {wt_id}")
            except Exception as e:
                logger.error(f"✗ Failed to create worktree {wt_id}: {e}")
                raise

        self._initialized = True
        logger.info(f"Worktree pool initialized with {len(self.worktrees)} worktrees")

    async def _create_worktree(self, wt_id: str) -> None:
        """Create a single worktree."""
        wt_path = self.base_dir / wt_id
        branch_name = f"worktree-{wt_id}"

        # Remove if already exists (cleanup from previous run)
        if wt_path.exists():
            logger.warning(f"Worktree {wt_id} already exists, removing...")
            await self._remove_worktree_directory(wt_id)

        # Delete branch if it exists
        try:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(self.main_repo_path),
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise Exception(f"Timeout deleting branch {branch_name}")

        # Create worktree with new branch from main
        try:
            result = subprocess.run(
                ["git", "worktree", "add", str(wt_path), "-b", branch_name, "main"],
                cwd=str(self.main_repo_path),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                raise Exception(f"Git worktree add failed: {result.stderr}")

        except subprocess.TimeoutExpired:
            raise Exception(f"Timeout creating worktree {wt_id}")

        # Create WorktreeInfo
        info = WorktreeInfo(
            id=wt_id,
            path=wt_path,
            branch=branch_name,
            status=WorktreeStatus.FREE,
            created_at=datetime.now(timezone.utc),
        )

        self.worktrees[wt_id] = info

    async def acquire(self, test_name: Optional[str] = None) -> WorktreeInfo:
        """
        Acquire an available worktree from the pool.

        Blocks if all worktrees are busy until one becomes available.

        Args:
            test_name: Name of test that will use this worktree (for tracking)

        Returns:
            WorktreeInfo for the acquired worktree
        """
        if not self._initialized:
            raise Exception("Worktree pool not initialized. Call initialize() first.")

        async with self._lock:
            # Find free worktree
            while True:
                for wt_id, info in self.worktrees.items():
                    if info.status == WorktreeStatus.FREE:
                        # Mark as busy
                        info.status = WorktreeStatus.BUSY
                        info.current_test = test_name
                        info.last_used = datetime.now(timezone.utc)
                        logger.info(f"Acquired worktree {wt_id} for test: {test_name}")
                        return info

                # No free worktrees, wait and retry
                logger.debug("All worktrees busy, waiting...")
                await asyncio.sleep(1)

    async def release(self, worktree: WorktreeInfo) -> None:
        """
        Release a worktree back to the pool after cleaning it.

        Args:
            worktree: WorktreeInfo to release
        """
        async with self._lock:
            if worktree.id not in self.worktrees:
                logger.warning(f"Attempted to release unknown worktree: {worktree.id}")
                return

            logger.info(f"Releasing worktree {worktree.id}")

            try:
                # Clean the worktree
                await self._cleanup_worktree(worktree)

                # Mark as free
                worktree.status = WorktreeStatus.FREE
                worktree.current_test = None

                logger.info(f"✓ Worktree {worktree.id} released and ready")

            except Exception as e:
                logger.error(f"Error releasing worktree {worktree.id}: {e}")
                worktree.status = WorktreeStatus.ERROR
                raise

    async def _cleanup_worktree(self, worktree: WorktreeInfo) -> None:
        """
        Clean a worktree: reset to main branch state, remove test artifacts.

        Args:
            worktree: WorktreeInfo to clean
        """
        try:
            # Checkout main to ensure clean state
            subprocess.run(
                ["git", "checkout", "-f", "main"],
                cwd=str(worktree.path),
                capture_output=True,
                timeout=30,
                check=True,
            )

            # Reset to main
            subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=str(worktree.path),
                capture_output=True,
                timeout=30,
                check=True,
            )

            # Clean untracked files
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(worktree.path),
                capture_output=True,
                timeout=30,
                check=True,
            )

            # Delete all local branches except main and worktree branch
            result = subprocess.run(
                ["git", "branch", "--list"],
                cwd=str(worktree.path),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            branches = [b.strip().lstrip("* ") for b in result.stdout.split("\n") if b.strip()]
            for branch in branches:
                if branch not in ["main", worktree.branch]:
                    subprocess.run(
                        ["git", "branch", "-D", branch],
                        cwd=str(worktree.path),
                        capture_output=True,
                        timeout=30,
                    )

            logger.debug(f"Cleaned worktree {worktree.id}")

        except subprocess.TimeoutExpired:
            raise Exception(f"Timeout cleaning worktree {worktree.id}")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Git cleanup failed for {worktree.id}: {e.stderr}")

    async def cleanup(self) -> None:
        """
        Remove all worktrees from the pool.

        Should be called when shutting down to clean up resources.
        """
        logger.info("Cleaning up worktree pool...")

        for wt_id in list(self.worktrees.keys()):
            try:
                await self._remove_worktree_directory(wt_id)
                logger.info(f"✓ Removed worktree: {wt_id}")
            except Exception as e:
                logger.error(f"✗ Failed to remove worktree {wt_id}: {e}")

        self.worktrees.clear()
        self._initialized = False
        logger.info("Worktree pool cleanup complete")

    async def _remove_worktree_directory(self, wt_id: str) -> None:
        """Remove a worktree directory and its git tracking."""
        info = self.worktrees.get(wt_id)
        if not info:
            # Try to remove by path anyway
            wt_path = self.base_dir / wt_id
        else:
            wt_path = info.path

        if not wt_path.exists():
            return

        # Remove from git worktree tracking
        try:
            subprocess.run(
                ["git", "worktree", "remove", str(wt_path), "--force"],
                cwd=str(self.main_repo_path),
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout removing worktree {wt_id} via git")

        # Force delete directory if still exists
        if wt_path.exists():
            import shutil
            shutil.rmtree(wt_path, ignore_errors=True)

        # Delete branch
        if info:
            try:
                subprocess.run(
                    ["git", "branch", "-D", info.branch],
                    cwd=str(self.main_repo_path),
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                logger.warning(f"Timeout deleting branch {info.branch}")

        if wt_id in self.worktrees:
            del self.worktrees[wt_id]

    def get_status(self) -> Dict[str, dict]:
        """
        Get status of all worktrees in the pool.

        Returns:
            Dictionary mapping worktree ID to status information
        """
        return {
            wt_id: {
                "id": info.id,
                "path": str(info.path),
                "branch": info.branch,
                "status": info.status.value,
                "current_test": info.current_test,
                "created_at": info.created_at.isoformat() if info.created_at else None,
                "last_used": info.last_used.isoformat() if info.last_used else None,
            }
            for wt_id, info in self.worktrees.items()
        }

    @property
    def num_free(self) -> int:
        """Get number of free worktrees."""
        return sum(1 for info in self.worktrees.values() if info.status == WorktreeStatus.FREE)

    @property
    def num_busy(self) -> int:
        """Get number of busy worktrees."""
        return sum(1 for info in self.worktrees.values() if info.status == WorktreeStatus.BUSY)

    @property
    def num_error(self) -> int:
        """Get number of errored worktrees."""
        return sum(1 for info in self.worktrees.values() if info.status == WorktreeStatus.ERROR)
