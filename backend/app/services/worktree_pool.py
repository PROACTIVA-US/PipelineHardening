"""Worktree Pool Manager - Manages pool of git worktrees for parallel execution."""

import asyncio
import subprocess
import logging
import time
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class WorktreeAcquisitionTimeout(Exception):
    """Raised when worktree acquisition times out."""
    pass


class WorktreeRecoveryFailed(Exception):
    """Raised when worktree recovery fails."""
    pass


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

    async def acquire(
        self,
        test_name: Optional[str] = None,
        timeout: float = 300.0,
    ) -> WorktreeInfo:
        """
        Acquire an available worktree from the pool.

        Blocks if all worktrees are busy until one becomes available or timeout.

        Args:
            test_name: Name of test that will use this worktree (for tracking)
            timeout: Maximum seconds to wait for a worktree (default: 300s / 5 minutes)

        Returns:
            WorktreeInfo for the acquired worktree

        Raises:
            WorktreeAcquisitionTimeout: If no worktree available within timeout
            Exception: If pool not initialized
        """
        if not self._initialized:
            raise Exception("Worktree pool not initialized. Call initialize() first.")

        deadline = time.time() + timeout
        attempt = 0

        while time.time() < deadline:
            attempt += 1

            # Acquire lock only for the check-and-acquire operation (not during sleep)
            async with self._lock:
                # First, try to recover any ERROR worktrees
                for wt_id, info in self.worktrees.items():
                    if info.status == WorktreeStatus.ERROR:
                        logger.info(f"Attempting to recover ERROR worktree {wt_id}")
                        try:
                            await self._try_recover_worktree(wt_id)
                        except Exception as e:
                            logger.warning(f"Failed to recover worktree {wt_id}: {e}")

                # Find free worktree
                for wt_id, info in self.worktrees.items():
                    if info.status == WorktreeStatus.FREE:
                        # Mark as busy
                        info.status = WorktreeStatus.BUSY
                        info.current_test = test_name
                        info.last_used = datetime.now(timezone.utc)
                        logger.info(f"Acquired worktree {wt_id} for test: {test_name}")
                        return info

            # No free worktrees - release lock and wait before retry
            remaining = deadline - time.time()
            if remaining <= 0:
                break

            if attempt % 10 == 0:  # Log every 10 attempts
                logger.warning(
                    f"All worktrees busy, waiting... (attempt {attempt}, "
                    f"{remaining:.1f}s remaining)"
                )
            else:
                logger.debug(f"All worktrees busy, waiting... (attempt {attempt})")

            await asyncio.sleep(min(1.0, remaining))

        # Timeout reached
        busy_worktrees = [
            f"{wt_id}:{info.current_test}"
            for wt_id, info in self.worktrees.items()
            if info.status == WorktreeStatus.BUSY
        ]
        raise WorktreeAcquisitionTimeout(
            f"No worktree available within {timeout}s. "
            f"Busy worktrees: {busy_worktrees}"
        )

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
        # Check if worktree path exists before attempting cleanup
        if not worktree.path.exists():
            logger.warning(f"Worktree path {worktree.path} does not exist, skipping cleanup")
            return

        # Check if it's a git repository
        git_dir = worktree.path / ".git"
        if not git_dir.exists():
            logger.warning(f"Worktree {worktree.id} is not a git repository, skipping git cleanup")
            return

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

    async def _try_recover_worktree(self, wt_id: str) -> None:
        """
        Attempt to recover a worktree in ERROR state.

        Args:
            wt_id: ID of worktree to recover

        Raises:
            WorktreeRecoveryFailed: If recovery fails
        """
        info = self.worktrees.get(wt_id)
        if not info:
            raise WorktreeRecoveryFailed(f"Unknown worktree: {wt_id}")

        if info.status != WorktreeStatus.ERROR:
            logger.debug(f"Worktree {wt_id} not in ERROR state, skipping recovery")
            return

        logger.info(f"Attempting recovery of worktree {wt_id}")

        try:
            # Try to clean the worktree
            await self._cleanup_worktree(info)

            # If cleanup succeeded, mark as FREE
            info.status = WorktreeStatus.FREE
            info.current_test = None
            logger.info(f"✓ Recovered worktree {wt_id}")

        except Exception as e:
            logger.warning(f"Cleanup failed for {wt_id}, attempting full recreate: {e}")

            try:
                # Remove and recreate the worktree
                await self._remove_worktree_directory(wt_id)
                await self._create_worktree(wt_id)
                logger.info(f"✓ Recreated worktree {wt_id}")

            except Exception as recreate_error:
                raise WorktreeRecoveryFailed(
                    f"Failed to recover worktree {wt_id}: {recreate_error}"
                )

    async def health_check(self) -> Dict[str, dict]:
        """
        Perform health check on all worktrees and attempt recovery of ERROR ones.

        Returns:
            Dictionary with health status for each worktree
        """
        results = {}

        # Create a copy of items to avoid mutation during iteration
        worktree_items = list(self.worktrees.items())

        for wt_id, info in worktree_items:
            health = {
                "id": wt_id,
                "status": info.status.value,
                "healthy": True,
                "issues": [],
            }

            # Check if path exists
            if not info.path.exists():
                health["healthy"] = False
                health["issues"].append("Path does not exist")

            # Check if it's a valid git repo
            elif not (info.path / ".git").exists():
                health["healthy"] = False
                health["issues"].append("Not a valid git repository")

            # Check for ERROR status
            if info.status == WorktreeStatus.ERROR:
                health["healthy"] = False
                health["issues"].append("Worktree in ERROR state")

                # Attempt recovery
                try:
                    await self._try_recover_worktree(wt_id)
                    health["recovered"] = True
                    health["status"] = self.worktrees[wt_id].status.value
                except WorktreeRecoveryFailed as e:
                    health["recovered"] = False
                    health["recovery_error"] = str(e)

            # Check for stuck BUSY worktrees (busy for > 30 minutes)
            if info.status == WorktreeStatus.BUSY and info.last_used:
                busy_duration = (datetime.now(timezone.utc) - info.last_used).total_seconds()
                if busy_duration > 1800:  # 30 minutes
                    health["issues"].append(
                        f"Worktree busy for {busy_duration/60:.1f} minutes (may be stuck)"
                    )

            results[wt_id] = health

        return results

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
