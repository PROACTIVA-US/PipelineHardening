"""Pydantic schemas for autonomous execution API."""

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, List


class StartAutonomousRequest(BaseModel):
    """Request to start autonomous execution."""
    plan_path: str = Field(..., description="Path to plan file")
    start_batch: int = Field(1, ge=0, description="First batch to execute")
    end_batch: int = Field(6, ge=1, description="Last batch to execute")
    execution_mode: str = Field("local", description="Execution mode: local")
    auto_merge: bool = Field(True, description="Automatically merge approved PRs")


class TaskExecutionResponse(BaseModel):
    """Response model for task execution."""
    id: str
    task_number: str
    task_title: str
    branch_name: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    status: str
    commits: List[str] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BatchExecutionResponse(BaseModel):
    """Response model for batch execution."""
    id: str
    batch_number: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tasks: List[TaskExecutionResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ActivePR(BaseModel):
    """Active PR information."""
    task: str
    pr_number: int
    status: str
    url: str


class AutonomousStatusResponse(BaseModel):
    """Response for autonomous execution status."""
    execution_id: str
    status: str
    current_batch: Optional[int] = None
    total_batches: int
    tasks_completed: int
    tasks_total: int
    active_prs: List[ActivePR] = []
    started_at: datetime
    completed_at: Optional[datetime] = None


class StartAutonomousResponse(BaseModel):
    """Response when starting autonomous execution."""
    execution_id: str
    status: str
    batches_scheduled: List[int]
