"""
E2E Tests for Pipeline Hardening

These tests verify the complete autonomous execution pipeline:
1. Plan parsing
2. Task execution via Claude CLI
3. PR creation and merging
4. State tracking
"""

import pytest
import asyncio
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.plan_parser import PlanParser
from app.services.task_executor import TaskExecutor, ExecutionResult
from app.services.batch_orchestrator import BatchOrchestrator
from app.models.autonomous import SessionStatus, BatchStatus, TaskStatus


class TestPlanParser:
    """Tests for plan parser."""

    def test_parse_test_plan(self, test_plan_path: str):
        """Test parsing the test plan."""
        parser = PlanParser(test_plan_path)
        batches = parser.parse()

        assert len(batches) >= 1
        assert batches[0].number == 1
        assert len(batches[0].tasks) >= 1

    def test_parse_tasks(self, test_plan_path: str):
        """Test task extraction."""
        parser = PlanParser(test_plan_path)
        batches = parser.parse()

        task = batches[0].tasks[0]
        assert task.number == "1.1"
        assert "hello" in task.title.lower() or "file" in task.title.lower()
        assert len(task.files) >= 1

    def test_parse_batch_dependencies(self, test_plan_path: str):
        """Test batch dependency parsing."""
        parser = PlanParser(test_plan_path)
        batches = parser.parse()

        # Batch 1 should have no dependencies
        assert batches[0].dependencies == []

        # Batch 2 should depend on batch 1
        if len(batches) > 1:
            assert 1 in batches[1].dependencies


class TestTaskExecutor:
    """Tests for task executor (mock mode)."""

    @pytest.fixture
    def executor(self, project_root: Path) -> TaskExecutor:
        """Create task executor."""
        return TaskExecutor(
            repo_path=str(project_root),
            github_token="mock_token",  # Will fail on actual GitHub calls
            repo_owner="test",
            repo_name="PipelineHardening",
        )

    def test_generate_branch_name(self, executor: TaskExecutor):
        """Test branch name generation."""
        branch = executor._generate_branch_name(1, "1.1")
        assert branch == "feature/batch-1-task-1-1"

        branch = executor._generate_branch_name(2, "2.3.1")
        assert branch == "feature/batch-2-task-2-3-1"

    def test_build_prompt(self, executor: TaskExecutor):
        """Test prompt building."""
        prompt = executor._build_prompt(
            task_number="1.1",
            task_title="Create Test File",
            implementation="Create a file with test content.",
            files=["test.txt"],
            verification_steps=["cat test.txt"],
        )

        assert "Task 1.1" in prompt
        assert "Create Test File" in prompt
        assert "test.txt" in prompt
        assert "cat test.txt" in prompt


class TestBatchOrchestrator:
    """Tests for batch orchestrator."""

    @pytest.mark.asyncio
    async def test_start_execution(self, db_session, test_plan_path: str):
        """Test starting an execution session."""
        orchestrator = BatchOrchestrator(db_session)

        session = await orchestrator.start_execution(
            plan_path=test_plan_path,
            start_batch=1,
            end_batch=2,
            execution_mode="local",
            auto_merge=True,
        )

        assert session.id.startswith("exec_")
        assert session.status == SessionStatus.STARTED.value
        assert session.tasks_total >= 1

    @pytest.mark.asyncio
    async def test_get_session_status(self, db_session, test_plan_path: str):
        """Test getting session status."""
        orchestrator = BatchOrchestrator(db_session)

        session = await orchestrator.start_execution(
            plan_path=test_plan_path,
            start_batch=1,
            end_batch=1,
        )

        status = await orchestrator.get_session_status(session.id)

        assert status is not None
        assert status["execution_id"] == session.id
        assert status["total_batches"] == 1

    @pytest.mark.asyncio
    async def test_get_ready_batches(self, db_session, test_plan_path: str):
        """Test getting ready batches."""
        orchestrator = BatchOrchestrator(db_session)

        session = await orchestrator.start_execution(
            plan_path=test_plan_path,
            start_batch=1,
            end_batch=2,
        )

        ready = await orchestrator.get_ready_batches(session.id)

        # Batch 1 should be ready (no dependencies)
        assert len(ready) >= 1
        assert ready[0].batch_number == 1


class TestE2EIntegration:
    """Full E2E integration tests."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running server and GitHub token")
    async def test_full_pipeline_execution(
        self,
        api_client,
        test_plan_path: str,
        github_token: str,
    ):
        """Test complete pipeline execution (requires server running)."""
        # Start execution
        response = await api_client.post(
            "/api/v1/autonomous/start",
            json={
                "plan_path": test_plan_path,
                "start_batch": 1,
                "end_batch": 1,
                "execution_mode": "local",
                "auto_merge": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        execution_id = data["execution_id"]

        # Poll for completion
        for _ in range(60):  # 60 second timeout
            status_response = await api_client.get(
                f"/api/v1/autonomous/{execution_id}/status"
            )
            status = status_response.json()

            if status["status"] in ["complete", "failed"]:
                break

            await asyncio.sleep(1)

        assert status["status"] == "complete"


# Run specific tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
