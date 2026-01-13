"""Data models for autonomous batch execution system."""

import enum
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class BatchStatus(enum.Enum):
    """Status of a batch execution."""
    PENDING = "pending"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskStatus(enum.Enum):
    """Status of a task execution."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PR_CREATED = "pr_created"
    MERGED = "merged"
    FAILED = "failed"


class SessionStatus(enum.Enum):
    """Status of an autonomous execution session."""
    STARTED = "started"
    PAUSED = "paused"
    EXECUTING = "executing"
    COMPLETE = "complete"
    FAILED = "failed"


class AutonomousSession(Base):
    """Represents a complete autonomous execution session."""
    __tablename__ = "autonomous_sessions"

    id = Column(String, primary_key=True)
    plan_path = Column(String, nullable=False)
    start_batch = Column(Integer, nullable=False)
    end_batch = Column(Integer, nullable=False)
    execution_mode = Column(String, nullable=False)  # local
    status = Column(String, nullable=False, index=True)
    current_batch = Column(Integer)
    tasks_completed = Column(Integer, default=0)
    tasks_total = Column(Integer, default=0)
    auto_merge = Column(Boolean, default=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    extra_data = Column(JSON, default={})


class BatchExecution(Base):
    """Represents execution of a single batch from a plan."""
    __tablename__ = "batch_executions"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    plan_path = Column(String, nullable=False)
    batch_number = Column(Integer, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    extra_data = Column(JSON, default={})

    tasks = relationship("TaskExecution", back_populates="batch", cascade="all, delete-orphan")


class TaskExecution(Base):
    """Represents execution of a single task within a batch."""
    __tablename__ = "task_executions"

    id = Column(String, primary_key=True)
    batch_execution_id = Column(String, ForeignKey("batch_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    task_number = Column(String, nullable=False, index=True)
    task_title = Column(String, nullable=False)
    branch_name = Column(String)
    pr_number = Column(Integer, index=True)
    pr_url = Column(String)
    status = Column(String, nullable=False, index=True)
    commits = Column(JSON, default=[])
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    error = Column(Text)
    extra_data = Column(JSON, default={})

    batch = relationship("BatchExecution", back_populates="tasks")
