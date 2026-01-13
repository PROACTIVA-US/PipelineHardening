"""
Execution Runner - Background execution loop for autonomous sessions.

Orchestrates the complete execution:
1. Get ready batches
2. Execute tasks sequentially
3. Track progress
4. Mark completion
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Set, List
from sqlalchemy import select

from app.database import get_sync_db
from app.models.autonomous import (
    AutonomousSession,
    BatchExecution,
    TaskExecution,
    SessionStatus,
    BatchStatus,
    TaskStatus,
)
from app.services.task_executor import TaskExecutor, ExecutionResult

logger = logging.getLogger(__name__)

# Track running executions to prevent duplicates
_running_executions: Set[str] = set()

# Store task references to prevent garbage collection
_background_tasks: Set[asyncio.Task] = set()


class ExecutionRunner:
    """Background execution runner for autonomous sessions."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._should_stop = False
        self.executor = TaskExecutor()

    async def run(self) -> None:
        """Execute the autonomous session."""
        if self.session_id in _running_executions:
            logger.warning(f"Execution {self.session_id} already running, skipping")
            return

        _running_executions.add(self.session_id)
        logger.info(f"ExecutionRunner started for session {self.session_id}")

        try:
            await self._execute_session()
        except Exception as e:
            logger.error(f"Execution runner failed for {self.session_id}: {e}")
            await asyncio.to_thread(self._mark_session_failed_sync, str(e))
        finally:
            _running_executions.discard(self.session_id)
            logger.info(f"ExecutionRunner finished for session {self.session_id}")

    async def _execute_session(self) -> None:
        """Execute the session through all batches."""
        # Update session status to EXECUTING
        await asyncio.to_thread(
            self._update_session_status_sync, SessionStatus.EXECUTING.value
        )

        while not self._should_stop:
            # Check session status
            session_status = await asyncio.to_thread(self._get_session_status_sync)

            if session_status is None:
                logger.error(f"Session {self.session_id} not found")
                break

            if session_status in [
                SessionStatus.PAUSED.value,
                SessionStatus.COMPLETE.value,
                SessionStatus.FAILED.value,
            ]:
                logger.info(f"Session {self.session_id} status: {session_status}")
                break

            # Get ready batches
            ready_batches = await asyncio.to_thread(self._get_ready_batches_sync)

            if not ready_batches:
                all_complete = await asyncio.to_thread(self._all_batches_complete_sync)
                if all_complete:
                    await asyncio.to_thread(self._mark_session_complete_sync)
                    break
                else:
                    await asyncio.sleep(2)
                    continue

            # Execute ready batches
            for batch_data in ready_batches:
                if self._should_stop:
                    break
                await self._execute_batch(batch_data)

            await asyncio.sleep(1)

        logger.info(f"Session {self.session_id} execution complete")

    async def _execute_batch(self, batch_data: dict) -> None:
        """Execute all tasks in a batch."""
        batch_id = batch_data["id"]
        batch_number = batch_data["batch_number"]

        logger.info(f"[Batch {batch_number}] Starting execution...")

        # Mark batch as executing
        await asyncio.to_thread(self._mark_batch_executing_sync, batch_id)

        # Get tasks for this batch
        tasks = await asyncio.to_thread(self._get_pending_tasks_sync, batch_id)

        if not tasks:
            logger.info(f"[Batch {batch_number}] No pending tasks")
            await asyncio.to_thread(self._mark_batch_complete_sync, batch_id)
            return

        # Get auto_merge setting
        auto_merge = await asyncio.to_thread(self._get_auto_merge_sync)

        # Execute tasks sequentially (to avoid git conflicts)
        success_count = 0
        fail_count = 0

        for task_data in tasks:
            if self._should_stop:
                break

            logger.info(f"[Task {task_data['task_number']}] Starting...")

            # Update task status
            await asyncio.to_thread(
                self._update_task_status_sync,
                task_data["id"],
                TaskStatus.IN_PROGRESS.value,
            )

            # Execute task
            extra = task_data.get("extra_data") or {}
            result = await self.executor.execute_task(
                task_number=task_data["task_number"],
                task_title=task_data["task_title"],
                implementation=extra.get("implementation", ""),
                files=extra.get("files", []),
                verification_steps=extra.get("verification_steps", []),
                batch_number=batch_number,
                auto_merge=auto_merge,
            )

            # Update task result
            if result.success:
                success_count += 1
                await asyncio.to_thread(
                    self._update_task_result_sync,
                    task_data["id"],
                    result,
                )
            else:
                fail_count += 1
                await asyncio.to_thread(
                    self._mark_task_failed_sync,
                    task_data["id"],
                    result.error or "Unknown error",
                )

            logger.info(
                f"[Task {task_data['task_number']}] "
                f"{'SUCCESS' if result.success else 'FAILED'} "
                f"(duration: {result.duration_seconds:.1f}s)"
            )

        # Mark batch complete or failed
        if fail_count > 0:
            await asyncio.to_thread(self._mark_batch_failed_sync, batch_id)
        else:
            await asyncio.to_thread(self._mark_batch_complete_sync, batch_id)

        # Update session task count
        await asyncio.to_thread(
            self._update_session_task_count_sync, success_count
        )

        logger.info(
            f"[Batch {batch_number}] Complete: "
            f"{success_count} succeeded, {fail_count} failed"
        )

    # ==========================================================================
    # Sync DB operations (run in thread pool)
    # ==========================================================================

    def _get_session_status_sync(self) -> str:
        """Get session status (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            return session.status if session else None

    def _update_session_status_sync(self, status: str) -> None:
        """Update session status (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            if session:
                session.status = status
                db.commit()

    def _get_ready_batches_sync(self) -> List[dict]:
        """Get ready batches (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(BatchExecution).where(
                    BatchExecution.session_id == self.session_id,
                    BatchExecution.status.in_([
                        BatchStatus.PENDING.value,
                        BatchStatus.READY.value,
                    ]),
                ).order_by(BatchExecution.batch_number)
            )
            batches = result.scalars().all()

            # Get completed batch numbers
            complete_result = db.execute(
                select(BatchExecution).where(
                    BatchExecution.session_id == self.session_id,
                    BatchExecution.status == BatchStatus.COMPLETE.value,
                )
            )
            completed = {b.batch_number for b in complete_result.scalars().all()}

            ready = []
            for batch in batches:
                deps = batch.extra_data.get("dependencies", []) if batch.extra_data else []
                if all(dep in completed for dep in deps):
                    ready.append({
                        "id": batch.id,
                        "batch_number": batch.batch_number,
                    })

            return ready

    def _all_batches_complete_sync(self) -> bool:
        """Check if all batches complete (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(BatchExecution).where(
                    BatchExecution.session_id == self.session_id,
                    BatchExecution.status.not_in([
                        BatchStatus.COMPLETE.value,
                        BatchStatus.FAILED.value,
                    ]),
                )
            )
            incomplete = result.scalars().all()
            return len(incomplete) == 0

    def _mark_session_complete_sync(self) -> None:
        """Mark session complete (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            if session:
                session.status = SessionStatus.COMPLETE.value
                session.completed_at = datetime.now(timezone.utc)
                db.commit()

    def _mark_session_failed_sync(self, error: str) -> None:
        """Mark session failed (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            if session:
                session.status = SessionStatus.FAILED.value
                session.extra_data = {**(session.extra_data or {}), "error": error}
                db.commit()

    def _mark_batch_executing_sync(self, batch_id: str) -> None:
        """Mark batch executing (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(BatchExecution).where(BatchExecution.id == batch_id)
            )
            batch = result.scalars().first()
            if batch:
                batch.status = BatchStatus.EXECUTING.value
                batch.started_at = datetime.now(timezone.utc)
                db.commit()

    def _mark_batch_complete_sync(self, batch_id: str) -> None:
        """Mark batch complete (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(BatchExecution).where(BatchExecution.id == batch_id)
            )
            batch = result.scalars().first()
            if batch:
                batch.status = BatchStatus.COMPLETE.value
                batch.completed_at = datetime.now(timezone.utc)
                db.commit()

    def _mark_batch_failed_sync(self, batch_id: str) -> None:
        """Mark batch failed (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(BatchExecution).where(BatchExecution.id == batch_id)
            )
            batch = result.scalars().first()
            if batch:
                batch.status = BatchStatus.FAILED.value
                batch.completed_at = datetime.now(timezone.utc)
                db.commit()

    def _get_pending_tasks_sync(self, batch_id: str) -> List[dict]:
        """Get pending tasks (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(TaskExecution).where(
                    TaskExecution.batch_execution_id == batch_id,
                    TaskExecution.status == TaskStatus.PENDING.value,
                )
            )
            tasks = result.scalars().all()
            return [
                {
                    "id": t.id,
                    "task_number": t.task_number,
                    "task_title": t.task_title,
                    "extra_data": t.extra_data,
                }
                for t in tasks
            ]

    def _get_auto_merge_sync(self) -> bool:
        """Get auto_merge setting (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            return session.auto_merge if session else True

    def _update_task_status_sync(self, task_id: str, status: str) -> None:
        """Update task status (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(TaskExecution).where(TaskExecution.id == task_id)
            )
            task = result.scalars().first()
            if task:
                task.status = status
                if status == TaskStatus.IN_PROGRESS.value:
                    task.started_at = datetime.now(timezone.utc)
                db.commit()

    def _update_task_result_sync(self, task_id: str, result: ExecutionResult) -> None:
        """Update task with execution result (sync)."""
        with get_sync_db() as db:
            db_result = db.execute(
                select(TaskExecution).where(TaskExecution.id == task_id)
            )
            task = db_result.scalars().first()
            if task:
                task.status = TaskStatus.MERGED.value if result.merged else TaskStatus.PR_CREATED.value
                task.branch_name = result.branch_name
                task.pr_number = result.pr_number
                task.pr_url = result.pr_url
                task.commits = result.commits
                task.completed_at = datetime.now(timezone.utc)
                db.commit()

    def _mark_task_failed_sync(self, task_id: str, error: str) -> None:
        """Mark task failed (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(TaskExecution).where(TaskExecution.id == task_id)
            )
            task = result.scalars().first()
            if task:
                task.status = TaskStatus.FAILED.value
                task.error = error
                task.completed_at = datetime.now(timezone.utc)
                db.commit()

    def _update_session_task_count_sync(self, completed: int) -> None:
        """Update session task count (sync)."""
        with get_sync_db() as db:
            result = db.execute(
                select(AutonomousSession).where(
                    AutonomousSession.id == self.session_id
                )
            )
            session = result.scalars().first()
            if session:
                session.tasks_completed = (session.tasks_completed or 0) + completed
                db.commit()

    def stop(self) -> None:
        """Signal the runner to stop."""
        self._should_stop = True


async def start_background_execution(session_id: str) -> None:
    """Start background execution for an autonomous session."""
    logger.info(f"Starting background execution for session {session_id}")

    runner = ExecutionRunner(session_id)

    # Create task and store reference
    task = asyncio.create_task(runner.run())
    _background_tasks.add(task)

    def cleanup_task(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        logger.debug(f"Background task cleaned up for session {session_id}")

    task.add_done_callback(cleanup_task)

    # Yield to event loop
    await asyncio.sleep(0)


def is_execution_running(session_id: str) -> bool:
    """Check if an execution is currently running."""
    return session_id in _running_executions
