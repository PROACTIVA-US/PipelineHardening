"""Data models for parallel test execution system."""

import enum
from sqlalchemy import Column, String, Integer, DateTime, Text, Float, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class ParallelSessionStatus(enum.Enum):
    """Status of a parallel test session."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"


class ParallelTestStatus(enum.Enum):
    """Status of a test execution within parallel session."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    RETRYING = "retrying"


class ParallelTestSession(Base):
    """Tracks a parallel test execution session."""
    __tablename__ = "parallel_test_sessions"

    id = Column(String, primary_key=True)
    num_workers = Column(Integer, nullable=False)
    status = Column(String, nullable=False, index=True)

    # Timing
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Float)

    # Summary stats
    tests_total = Column(Integer, default=0)
    tests_passed = Column(Integer, default=0)
    tests_failed = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)

    # Configuration
    worktree_base_dir = Column(String)
    max_retries = Column(Integer, default=2)

    # Relationships
    test_executions = relationship("ParallelTestExecution", back_populates="session", cascade="all, delete-orphan")


class ParallelTestExecution(Base):
    """Individual test execution within parallel session."""
    __tablename__ = "parallel_test_executions"

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("parallel_test_sessions.id", ondelete="CASCADE"), nullable=False, index=True)

    # Test info
    plan_file = Column(String, nullable=False)
    batch_range = Column(String, nullable=False)

    # Execution info
    worktree_id = Column(String)
    worker_id = Column(String)
    status = Column(String, nullable=False, index=True)
    retry_count = Column(Integer, default=0)

    # Results
    tasks_passed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)
    error = Column(Text)
    report_path = Column(String)

    # Timing
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Float)

    # Relationships
    session = relationship("ParallelTestSession", back_populates="test_executions")
