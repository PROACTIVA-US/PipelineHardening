"""API endpoints for autonomous batch execution."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.schemas.autonomous import (
    StartAutonomousRequest,
    StartAutonomousResponse,
    AutonomousStatusResponse,
    BatchExecutionResponse,
    TaskExecutionResponse,
)
from app.services.batch_orchestrator import BatchOrchestrator, OrchestratorError
from app.services.execution_runner import start_background_execution
from app.models.autonomous import BatchExecution, TaskExecution

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/autonomous", tags=["autonomous"])


@router.post("/start", response_model=StartAutonomousResponse)
async def start_autonomous_execution(
    request: StartAutonomousRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a new autonomous execution session.

    Parses the plan, creates execution structure, and begins orchestration.
    """
    try:
        orchestrator = BatchOrchestrator(db)

        session = await orchestrator.start_execution(
            plan_path=request.plan_path,
            start_batch=request.start_batch,
            end_batch=request.end_batch,
            execution_mode=request.execution_mode,
            auto_merge=request.auto_merge,
        )

        batches_scheduled = list(range(request.start_batch, request.end_batch + 1))

        # Trigger background execution
        logger.info(f"Started execution {session.id}, triggering background execution...")
        await start_background_execution(session.id)

        return StartAutonomousResponse(
            execution_id=session.id,
            status=session.status,
            batches_scheduled=batches_scheduled,
        )

    except OrchestratorError as e:
        logger.error(f"Orchestrator error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error starting execution: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start autonomous execution",
        )


@router.get("/{execution_id}/status", response_model=AutonomousStatusResponse)
async def get_execution_status(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get current status of an autonomous execution session."""
    try:
        orchestrator = BatchOrchestrator(db)
        status_data = await orchestrator.get_session_status(execution_id)

        if not status_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found",
            )

        return AutonomousStatusResponse(**status_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting execution status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get execution status",
        )


@router.get("/{execution_id}/batches", response_model=List[BatchExecutionResponse])
async def get_batches(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all batches for an execution session."""
    try:
        result = await db.execute(
            select(BatchExecution)
            .where(BatchExecution.session_id == execution_id)
            .options(selectinload(BatchExecution.tasks))
            .order_by(BatchExecution.batch_number)
        )
        batches = result.scalars().all()

        if not batches:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No batches found for execution {execution_id}",
            )

        return [
            BatchExecutionResponse(
                id=batch.id,
                batch_number=batch.batch_number,
                status=batch.status,
                started_at=batch.started_at,
                completed_at=batch.completed_at,
                tasks=[
                    TaskExecutionResponse.model_validate(task)
                    for task in batch.tasks
                ],
            )
            for batch in batches
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batches: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get batches",
        )


@router.get("/{execution_id}/tasks/{task_number}", response_model=TaskExecutionResponse)
async def get_task(
    execution_id: str,
    task_number: str,
    db: AsyncSession = Depends(get_db),
):
    """Get details of a specific task."""
    try:
        result = await db.execute(
            select(TaskExecution).where(
                TaskExecution.id.like(f"{execution_id}%"),
                TaskExecution.task_number == task_number,
            )
        )
        task = result.scalars().first()

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_number} not found in execution {execution_id}",
            )

        return TaskExecutionResponse.model_validate(task)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get task",
        )
