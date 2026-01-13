"""Pytest configuration and fixtures for E2E testing."""

import os
import pytest
import asyncio
from pathlib import Path
from typing import AsyncGenerator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add backend to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.database import Base


# Test database
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_pipeline.db"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()

    # Remove test database file
    db_path = Path("./test_pipeline.db")
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create database session for tests."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def project_root() -> Path:
    """Get project root path."""
    return Path(__file__).parent.parent


@pytest.fixture
def test_plan_path(project_root: Path) -> str:
    """Get path to test plan."""
    return str(project_root / "docs" / "plans" / "test-plan-01.md")


@pytest.fixture
def test_artifacts_dir(project_root: Path) -> Path:
    """Get test artifacts directory."""
    artifacts_dir = project_root / "test-artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    return artifacts_dir


@pytest.fixture
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create async HTTP client for API testing."""
    async with httpx.AsyncClient(base_url="http://localhost:8001") as client:
        yield client


@pytest.fixture
def github_token() -> str:
    """Get GitHub token from environment."""
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        pytest.skip("GITHUB_TOKEN not set")
    return token
