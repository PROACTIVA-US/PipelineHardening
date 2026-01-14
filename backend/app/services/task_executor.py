"""
Task Executor - Executes individual tasks via Claude Code subprocess.

Handles the complete lifecycle:
1. Create feature branch
2. Build execution prompt
3. Execute via `claude` CLI subprocess
4. Commit and push changes
5. Create PR via GitHub API
6. Merge PR
"""

import asyncio
import subprocess
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

from github import Github, GithubException, Auth

from app.config import settings

logger = logging.getLogger(__name__)


class TaskExecutorError(Exception):
    """Base exception for task executor errors."""
    pass


class BranchError(TaskExecutorError):
    """Error creating or managing branches."""
    pass


class ExecutionError(TaskExecutorError):
    """Error during task execution."""
    pass


class PRError(TaskExecutorError):
    """Error creating or managing PR."""
    pass


@dataclass
class ExecutionResult:
    """Result of task execution."""
    success: bool
    branch_name: str
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    commits: List[str] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    merged: bool = False
    merge_sha: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    claude_output: str = ""


class TaskExecutor:
    """
    Executes individual tasks using Claude Code CLI.

    The minimal pipeline executor that:
    1. Creates a branch (or uses provided worktree)
    2. Runs `claude` CLI with the task prompt
    3. Commits changes
    4. Creates and merges PR

    For parallel execution, use with WorktreePool:
    - WorktreePool creates isolated worktrees with branches
    - Pass worktree path to execute_task()
    - Each task runs in isolation - no git conflicts
    """

    def __init__(
        self,
        repo_path: Optional[str] = None,
        github_token: Optional[str] = None,
        repo_owner: Optional[str] = None,
        repo_name: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path) if repo_path else Path(settings.repo_path)
        self.github_token = github_token or settings.github_token
        self.repo_owner = repo_owner or settings.github_repo_owner or "PROACTIVA-US"
        self.repo_name = repo_name or settings.github_repo_name or "PipelineHardening"
        self._github: Optional[Github] = None

    @property
    def github(self) -> Github:
        """Lazy-load GitHub client."""
        if self._github is None:
            if not self.github_token:
                raise PRError("GitHub token not configured. Set GITHUB_TOKEN env var.")
            self._github = Github(auth=Auth.Token(self.github_token))
        return self._github

    async def execute_task(
        self,
        task_number: str,
        task_title: str,
        implementation: str,
        files: List[str],
        verification_steps: List[str],
        batch_number: int = 1,
        auto_merge: bool = True,
        worktree_path: Optional[Path] = None,
        branch_name: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute a single task end-to-end.

        Args:
            task_number: Task identifier (e.g., "1.1")
            task_title: Human-readable title
            implementation: Full implementation instructions
            files: List of files to create/modify
            verification_steps: Commands to verify success
            batch_number: Batch this task belongs to
            auto_merge: Whether to merge PR automatically
            worktree_path: Path to worktree for isolated execution.
                          If provided, skips branch creation (worktree already has branch).
                          If None, uses legacy _create_branch (not recommended).
            branch_name: Branch name (required if worktree_path provided).

        Returns:
            ExecutionResult with success status and details
        """
        start_time = datetime.now(timezone.utc)

        # Use provided branch name or generate one
        if branch_name is None:
            branch_name = self._generate_branch_name(batch_number, task_number)

        # Determine execution path (worktree or repo_path)
        exec_path = worktree_path if worktree_path else self.repo_path

        claude_output = ""

        try:
            # 1. Create feature branch (only if NOT using worktree)
            # Worktrees already have their branch set up by WorktreePool
            if worktree_path:
                logger.info(f"[Task {task_number}] Using worktree: {worktree_path} (branch: {branch_name})")
            else:
                # DEPRECATED: This path has git corruption bugs with parallel execution
                logger.warning(f"[Task {task_number}] Using legacy _create_branch - NOT RECOMMENDED for parallel execution")
                logger.info(f"[Task {task_number}] Creating branch: {branch_name}")
                await self._create_branch(branch_name)

            # 2. Build prompt and execute with Claude
            logger.info(f"[Task {task_number}] Executing with Claude CLI...")
            prompt = self._build_prompt(task_number, task_title, implementation, files, verification_steps)
            claude_output = await self._execute_with_claude(prompt, branch_name, exec_path)

            # 3. Commit and push
            logger.info(f"[Task {task_number}] Committing changes...")
            commit_sha, files_changed = await self._commit_and_push(
                branch_name=branch_name,
                task_number=task_number,
                task_title=task_title,
                exec_path=exec_path,
            )

            if not commit_sha:
                logger.warning(f"[Task {task_number}] No changes to commit")
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                return ExecutionResult(
                    success=True,
                    branch_name=branch_name,
                    duration_seconds=duration,
                    claude_output=claude_output,
                )

            # 4. Create PR
            logger.info(f"[Task {task_number}] Creating PR...")
            pr_number, pr_url = await self._create_pr(
                branch_name=branch_name,
                task_number=task_number,
                task_title=task_title,
                batch_number=batch_number,
                files=files,
            )

            # 5. Merge PR if auto_merge enabled
            merged = False
            merge_sha = None
            if auto_merge and pr_number:
                logger.info(f"[Task {task_number}] Merging PR #{pr_number}...")
                merged, merge_sha = await self._merge_pr(pr_number, branch_name)

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()

            return ExecutionResult(
                success=True,
                branch_name=branch_name,
                pr_number=pr_number,
                pr_url=pr_url,
                commits=[commit_sha] if commit_sha else [],
                files_changed=files_changed,
                merged=merged,
                merge_sha=merge_sha,
                duration_seconds=duration,
                claude_output=claude_output,
            )

        except Exception as e:
            logger.error(f"[Task {task_number}] Execution failed: {e}")
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()

            return ExecutionResult(
                success=False,
                branch_name=branch_name,
                error=str(e),
                duration_seconds=duration,
                claude_output=claude_output,
            )

    def _generate_branch_name(self, batch_number: int, task_number: str) -> str:
        """Generate feature branch name."""
        sanitized_task = task_number.replace(".", "-")
        return f"feature/batch-{batch_number}-task-{sanitized_task}"

    async def _create_branch(self, branch_name: str) -> None:
        """Create a new feature branch from main."""
        try:
            # Clean working directory
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=str(self.repo_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "reset", "HEAD"],
                cwd=str(self.repo_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "checkout", "--", "."],
                cwd=str(self.repo_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(self.repo_path),
                capture_output=True,
            )

            # Fetch latest main
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=str(self.repo_path),
                capture_output=True,
                check=True,
            )

            # Delete existing branch if it exists
            subprocess.run(
                ["git", "push", "origin", "--delete", branch_name],
                cwd=str(self.repo_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(self.repo_path),
                capture_output=True,
            )

            # Create fresh branch
            subprocess.run(
                ["git", "checkout", "-b", branch_name, "origin/main"],
                cwd=str(self.repo_path),
                capture_output=True,
                check=True,
            )

        except subprocess.CalledProcessError as e:
            raise BranchError(f"Failed to create branch: {e.stderr}")

    def _build_prompt(
        self,
        task_number: str,
        task_title: str,
        implementation: str,
        files: List[str],
        verification_steps: List[str],
    ) -> str:
        """Build the execution prompt for Claude."""
        prompt_parts = [
            f"# Task {task_number}: {task_title}",
            "",
            "## Files to modify",
        ]

        for f in files:
            prompt_parts.append(f"- {f}")

        prompt_parts.extend([
            "",
            "## Implementation",
            implementation,
            "",
            "## Verification",
            "After completing, run these commands to verify:",
        ])

        for step in verification_steps:
            prompt_parts.append(f"- {step}")

        prompt_parts.extend([
            "",
            "## Instructions",
            "1. Implement the changes described above",
            "2. Ensure all tests pass",
            "3. Follow existing code patterns",
            "4. Do not modify unrelated files",
        ])

        return "\n".join(prompt_parts)

    async def _execute_with_claude(
        self,
        prompt: str,
        branch_name: str,
        exec_path: Optional[Path] = None,
    ) -> str:
        """Execute task using Claude Code CLI.

        Args:
            prompt: Task prompt to execute
            branch_name: Git branch name (for logging)
            exec_path: Path to execute in (worktree or repo). Uses self.repo_path if None.
        """
        work_path = exec_path if exec_path else self.repo_path
        logger.info(f"Executing Claude CLI with prompt ({len(prompt)} chars) in {work_path}...")

        # Write prompt to temp file
        prompt_file = work_path / ".claude_prompt.md"
        prompt_file.write_text(prompt)

        try:
            # Build environment with proper PATH for Claude CLI
            # macOS homebrew installs to /opt/homebrew/bin which may not be in subprocess PATH
            env = os.environ.copy()
            env["CLAUDE_AUTO_ACCEPT"] = "1"

            # Ensure common tool paths are available
            extra_paths = [
                "/opt/homebrew/bin",  # macOS Apple Silicon homebrew
                "/usr/local/bin",      # macOS Intel homebrew / Linux
                "/usr/bin",
            ]
            current_path = env.get("PATH", "")
            for extra_path in extra_paths:
                if extra_path not in current_path:
                    current_path = f"{extra_path}:{current_path}"
            env["PATH"] = current_path

            # Run claude CLI
            # Using --print for non-interactive mode, -p for prompt from stdin
            result = subprocess.run(
                ["claude", "--print", "-p", prompt],
                cwd=str(work_path),
                capture_output=True,
                text=True,
                timeout=1800,  # 30 minute timeout
                env=env,
            )

            output = result.stdout + result.stderr
            logger.info(f"Claude CLI completed with return code {result.returncode}")

            if result.returncode != 0:
                logger.warning(f"Claude CLI returned non-zero: {result.returncode}")
                logger.warning(f"stderr: {result.stderr[:500]}")

            return output

        except subprocess.TimeoutExpired:
            raise ExecutionError("Claude CLI execution timed out after 30 minutes")
        except FileNotFoundError:
            raise ExecutionError("Claude CLI not found. Is `claude` installed and in PATH?")
        finally:
            # Cleanup temp file
            if prompt_file.exists():
                prompt_file.unlink()

    async def _commit_and_push(
        self,
        branch_name: str,
        task_number: str,
        task_title: str,
        exec_path: Optional[Path] = None,
    ) -> Tuple[Optional[str], List[str]]:
        """Commit changes and push to remote.

        Args:
            branch_name: Git branch name
            task_number: Task identifier for commit message
            task_title: Task title for commit message
            exec_path: Path to execute in (worktree or repo). Uses self.repo_path if None.
        """
        work_path = exec_path if exec_path else self.repo_path

        try:
            # Check for changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(work_path),
                capture_output=True,
                text=True,
                check=True,
            )

            if not result.stdout.strip():
                return None, []

            # Get list of changed files
            files_changed = [line[3:] for line in result.stdout.strip().split("\n") if line]

            # Stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(work_path),
                capture_output=True,
                check=True,
            )

            # Commit
            commit_msg = (
                f"feat(pipeline): {task_title}\n\n"
                f"Task {task_number} from autonomous pipeline execution.\n\n"
                f"Co-Authored-By: Claude <noreply@anthropic.com>"
            )

            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=str(work_path),
                capture_output=True,
                check=True,
            )

            # Get commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(work_path),
                capture_output=True,
                text=True,
                check=True,
            )
            commit_sha = result.stdout.strip()

            # Push
            subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=str(work_path),
                capture_output=True,
                check=True,
            )

            return commit_sha, files_changed

        except subprocess.CalledProcessError as e:
            raise TaskExecutorError(f"Git operation failed: {e.stderr}")

    async def _create_pr(
        self,
        branch_name: str,
        task_number: str,
        task_title: str,
        batch_number: int,
        files: List[str],
    ) -> Tuple[int, str]:
        """Create a pull request via GitHub API."""
        try:
            repo = self.github.get_repo(f"{self.repo_owner}/{self.repo_name}")

            # Check for existing PR
            existing_prs = list(repo.get_pulls(state="open", head=f"{self.repo_owner}:{branch_name}"))
            if existing_prs:
                pr = existing_prs[0]
                logger.info(f"Found existing PR #{pr.number}")
                return pr.number, pr.html_url

            # Build PR body
            file_list = "\n".join(f"- `{f}`" for f in files) if files else "- See diff"

            body = f"""## Task {task_number}: {task_title}

**Batch:** {batch_number}

### Files Changed
{file_list}

---

**Automated PR** created by Pipeline Hardening System

Co-Authored-By: Claude <noreply@anthropic.com>
"""

            # Create PR
            pr = repo.create_pull(
                title=f"Task {task_number}: {task_title}",
                body=body,
                head=branch_name,
                base="main",
                draft=False,
            )

            logger.info(f"Created PR #{pr.number}: {pr.html_url}")
            return pr.number, pr.html_url

        except GithubException as e:
            raise PRError(f"Failed to create PR: {e}")

    async def _merge_pr(
        self,
        pr_number: int,
        branch_name: str,
    ) -> Tuple[bool, Optional[str]]:
        """Merge a pull request."""
        try:
            repo = self.github.get_repo(f"{self.repo_owner}/{self.repo_name}")
            pr = repo.get_pull(pr_number)

            # Check if mergeable
            if not pr.mergeable:
                logger.warning(f"PR #{pr_number} is not mergeable")
                return False, None

            # Merge
            merge_result = pr.merge(
                commit_title=f"Merge: {pr.title}",
                merge_method="squash",
            )

            if merge_result.merged:
                logger.info(f"PR #{pr_number} merged: {merge_result.sha}")

                # Delete branch
                try:
                    ref = repo.get_git_ref(f"heads/{branch_name}")
                    ref.delete()
                    logger.info(f"Deleted branch: {branch_name}")
                except GithubException:
                    pass

                return True, merge_result.sha

            return False, None

        except GithubException as e:
            logger.error(f"Failed to merge PR: {e}")
            return False, None
