"""
Batch Orchestrator - Manages autonomous batch execution.

Responsibilities:
1. Parse plan and create execution records
2. Manage batch dependencies
3. Track progress
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autonomous import (
    AutonomousSession,
    BatchExecution,
    TaskExecution,
    SessionStatus,
    BatchStatus,
    TaskStatus,
)
from app.services.plan_parser import PlanParser, Batch, Task

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    """Base exception for orchestrator errors."""
    pass


class BatchOrchestrator:
    """Orchestrates autonomous batch execution."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def start_execution(
        self,
        plan_path: str,
        start_batch: int = 1,
        end_batch: int = 6,
        execution_mode: str = "local",
        auto_merge: bool = True,
    ) -> AutonomousSession:
        """
        Start a new autonomous execution session.

        Args:
            plan_path: Path to plan markdown file
            start_batch: First batch to execute
            end_batch: Last batch to execute
            execution_mode: "local" (only mode supported for now)
            auto_merge: Automatically merge approved PRs

        Returns:
            Created AutonomousSession object
        """
        try:
            # Parse the plan
            logger.info(f"Parsing plan: {plan_path}")
            parser = PlanParser(plan_path)
            batches = parser.parse()

            # Filter batches by range
            batches = [b for b in batches if start_batch <= b.number <= end_batch]
            if not batches:
                raise OrchestratorError(f"No batches found in range {start_batch}-{end_batch}")

            # Count total tasks
            tasks_total = sum(len(b.tasks) for b in batches)

            # Create session record
            session_id = f"exec_{uuid.uuid4().hex[:8]}"
            session = AutonomousSession(
                id=session_id,
                plan_path=plan_path,
                start_batch=start_batch,
                end_batch=end_batch,
                execution_mode=execution_mode,
                status=SessionStatus.STARTED.value,
                current_batch=start_batch,
                tasks_completed=0,
                tasks_total=tasks_total,
                auto_merge=auto_merge,
                extra_data={
                    "parsed_batches": len(batches),
                    "execution_started": datetime.now(timezone.utc).isoformat(),
                },
            )

            self.db.add(session)

            # Create batch execution records
            for batch in batches:
                await self._create_batch_execution(session_id, plan_path, batch)

            await self.db.commit()
            await self.db.refresh(session)

            logger.info(f"Created execution session {session_id} with {len(batches)} batches, {tasks_total} tasks")
            return session

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to start execution: {e}")
            raise OrchestratorError(f"Execution start failed: {e}") from e

    async def _create_batch_execution(
        self,
        session_id: str,
        plan_path: str,
        batch: Batch,
    ) -> BatchExecution:
        """Create BatchExecution and TaskExecution records for a batch."""
        batch_id = f"{session_id}_batch_{batch.number}"

        status = BatchStatus.READY.value if len(batch.dependencies) == 0 else BatchStatus.PENDING.value

        batch_exec = BatchExecution(
            id=batch_id,
            session_id=session_id,
            plan_path=plan_path,
            batch_number=batch.number,
            status=status,
            extra_data={
                "title": batch.title,
                "execution_mode": batch.execution_mode,
                "dependencies": batch.dependencies,
                "task_count": len(batch.tasks),
            },
        )

        self.db.add(batch_exec)

        # Create task execution records
        for task in batch.tasks:
            await self._create_task_execution(batch_id, task)

        return batch_exec

    async def _create_task_execution(self, batch_id: str, task: Task) -> TaskExecution:
        """Create a TaskExecution record for a task."""
        task_id = f"{batch_id}_task_{task.number.replace('.', '_')}"

        task_exec = TaskExecution(
            id=task_id,
            batch_execution_id=batch_id,
            task_number=task.number,
            task_title=task.title,
            status=TaskStatus.PENDING.value,
            extra_data={
                "files": task.files,
                "verification_steps": task.verification_steps,
                "dependencies": task.dependencies,
                "implementation": task.implementation,
            },
        )

        self.db.add(task_exec)
        return task_exec

    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of an execution session."""
        try:
            result = await self.db.execute(
                select(AutonomousSession).where(AutonomousSession.id == session_id)
            )
            session = result.scalars().first()

            if not session:
                return None

            # Get active PRs
            pr_result = await self.db.execute(
                select(TaskExecution).where(
                    TaskExecution.id.like(f"{session_id}%"),
                    TaskExecution.pr_number.isnot(None),
                    TaskExecution.status == TaskStatus.PR_CREATED.value,
                )
            )
            active_prs = pr_result.scalars().all()

            return {
                "execution_id": session.id,
                "status": session.status,
                "current_batch": session.current_batch,
                "total_batches": session.end_batch - session.start_batch + 1,
                "tasks_completed": session.tasks_completed,
                "tasks_total": session.tasks_total,
                "active_prs": [
                    {
                        "task": pr.task_number,
                        "pr_number": pr.pr_number,
                        "status": pr.status,
                        "url": pr.pr_url,
                    }
                    for pr in active_prs
                ],
                "started_at": session.started_at,
                "completed_at": session.completed_at,
            }

        except Exception as e:
            logger.error(f"Failed to get session status: {e}")
            return None

    async def get_ready_batches(self, session_id: str) -> List[BatchExecution]:
        """Get batches that are ready to execute."""
        try:
            result = await self.db.execute(
                select(BatchExecution).where(BatchExecution.session_id == session_id)
            )
            batches = result.scalars().all()

            completed = {
                b.batch_number
                for b in batches
                if b.status == BatchStatus.COMPLETE.value
            }

            ready = []
            for batch in batches:
                if batch.status in [BatchStatus.EXECUTING.value, BatchStatus.COMPLETE.value]:
                    continue

                dependencies = batch.extra_data.get("dependencies", [])
                if all(dep in completed for dep in dependencies):
                    ready.append(batch)

            return ready

        except Exception as e:
            logger.error(f"Failed to get ready batches: {e}")
            return []

    async def mark_batch_executing(self, batch_id: str) -> bool:
        """Mark a batch as executing."""
        try:
            result = await self.db.execute(
                select(BatchExecution).where(BatchExecution.id == batch_id)
            )
            batch = result.scalars().first()

            if not batch:
                return False

            batch.status = BatchStatus.EXECUTING.value
            batch.started_at = datetime.now(timezone.utc)
            await self.db.commit()

            logger.info(f"Batch {batch.batch_number} marked as executing")
            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to mark batch executing: {e}")
            return False

    async def mark_batch_complete(self, batch_id: str) -> bool:
        """Mark a batch as complete."""
        try:
            result = await self.db.execute(
                select(BatchExecution).where(BatchExecution.id == batch_id)
            )
            batch = result.scalars().first()

            if not batch:
                return False

            batch.status = BatchStatus.COMPLETE.value
            batch.completed_at = datetime.now(timezone.utc)
            await self.db.commit()

            logger.info(f"Batch {batch.batch_number} marked as complete")
            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to mark batch complete: {e}")
            return False

    async def mark_task_complete(
        self,
        task_id: str,
        pr_number: Optional[int] = None,
        pr_url: Optional[str] = None,
        merged: bool = False,
    ) -> bool:
        """Mark a task as complete."""
        try:
            result = await self.db.execute(
                select(TaskExecution).where(TaskExecution.id == task_id)
            )
            task = result.scalars().first()

            if not task:
                return False

            task.status = TaskStatus.MERGED.value if merged else TaskStatus.PR_CREATED.value
            task.pr_number = pr_number
            task.pr_url = pr_url
            task.completed_at = datetime.now(timezone.utc)
            await self.db.commit()

            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to mark task complete: {e}")
            return False

    async def mark_task_failed(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        try:
            result = await self.db.execute(
                select(TaskExecution).where(TaskExecution.id == task_id)
            )
            task = result.scalars().first()

            if not task:
                return False

            task.status = TaskStatus.FAILED.value
            task.error = error
            task.completed_at = datetime.now(timezone.utc)
            await self.db.commit()

            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to mark task failed: {e}")
            return False

    async def mark_session_complete(self, session_id: str) -> bool:
        """Mark the session as complete."""
        try:
            result = await self.db.execute(
                select(AutonomousSession).where(AutonomousSession.id == session_id)
            )
            session = result.scalars().first()

            if not session:
                return False

            session.status = SessionStatus.COMPLETE.value
            session.completed_at = datetime.now(timezone.utc)
            await self.db.commit()

            logger.info(f"Session {session_id} marked as complete")
            return True

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to mark session complete: {e}")
            return False
