#!/usr/bin/env python3
"""
Manual test script for pipeline execution.

Usage:
    python scripts/test_execution.py

Requires:
    - GITHUB_TOKEN environment variable
    - Server running (or use --no-server for direct execution)
"""

import asyncio
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.plan_parser import PlanParser
from app.services.task_executor import TaskExecutor


async def test_plan_parsing():
    """Test plan parsing."""
    print("\n=== Testing Plan Parser ===")
    plan_path = Path(__file__).parent.parent / "docs" / "plans" / "test-plan-01.md"

    parser = PlanParser(str(plan_path))
    batches = parser.parse()

    print(f"Parsed {len(batches)} batches:")
    for batch in batches:
        print(f"  Batch {batch.number}: {batch.title}")
        print(f"    Dependencies: {batch.dependencies}")
        print(f"    Tasks: {len(batch.tasks)}")
        for task in batch.tasks:
            print(f"      - {task.number}: {task.title}")
            print(f"        Files: {task.files}")

    return batches


async def test_branch_creation():
    """Test branch creation (dry run)."""
    print("\n=== Testing Branch Creation ===")

    executor = TaskExecutor(
        repo_path=str(Path(__file__).parent.parent),
    )

    branch_name = executor._generate_branch_name(1, "1.1")
    print(f"Generated branch name: {branch_name}")

    # Note: Not actually creating the branch to avoid git changes
    print("(Skipping actual branch creation)")


async def test_prompt_building():
    """Test prompt building."""
    print("\n=== Testing Prompt Building ===")

    executor = TaskExecutor()

    prompt = executor._build_prompt(
        task_number="1.1",
        task_title="Create Hello World File",
        implementation="Create a simple text file with greeting content.",
        files=["test-artifacts/hello.txt"],
        verification_steps=["cat test-artifacts/hello.txt"],
    )

    print("Generated prompt:")
    print("-" * 40)
    print(prompt[:500])
    if len(prompt) > 500:
        print(f"... ({len(prompt)} chars total)")
    print("-" * 40)


async def main():
    """Run all tests."""
    print("Pipeline Hardening - Manual Test Script")
    print("=" * 50)

    # Check environment
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("\nWARNING: GITHUB_TOKEN not set. PR operations will fail.")
        print("Set it with: export GITHUB_TOKEN=your_token")

    try:
        # Test plan parsing
        batches = await test_plan_parsing()

        # Test branch creation
        await test_branch_creation()

        # Test prompt building
        await test_prompt_building()

        print("\n" + "=" * 50)
        print("All tests passed!")
        print("\nNext steps:")
        print("1. Set GITHUB_TOKEN environment variable")
        print("2. Run server: ./scripts/run_server.sh")
        print("3. Use API to start execution")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
