"""
Plan Parser - Extracts tasks from markdown plans.

Supports format:
## Batch N: Title

### Task N.M: Task Title

**Files:**
- Create: path/to/file.py

**Implementation:**
...instructions...
"""

import re
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Represents a task from the plan."""
    number: str  # "1.1", "1.2"
    title: str
    batch_number: int
    files: List[str]
    implementation: str
    verification_steps: List[str]
    dependencies: List[str]


@dataclass
class Batch:
    """Represents a batch from the plan."""
    number: int
    title: str
    execution_mode: str
    dependencies: List[int]
    tasks: List[Task]
    description: str


class PlanParseError(Exception):
    """Raised when plan parsing fails."""
    pass


class PlanParser:
    """Parses structured markdown implementation plans."""

    def __init__(self, plan_path: str):
        self.plan_path = Path(plan_path)
        if not self.plan_path.exists():
            raise PlanParseError(f"Plan file not found: {plan_path}")

        self.content = self.plan_path.read_text()
        self.batches: List[Batch] = []

    def parse(self) -> List[Batch]:
        """Parse the plan file and extract all batches and tasks."""
        batch_pattern = r'#{2,3} Batch (\d+(?:\.\d+)?):\s*([^\n]+)'
        batch_matches = list(re.finditer(batch_pattern, self.content))

        if not batch_matches:
            raise PlanParseError("No batches found in plan")

        for i, match in enumerate(batch_matches):
            batch_num_str = match.group(1)
            batch_num = int(batch_num_str.split('.')[0]) if '.' in batch_num_str else int(batch_num_str)
            batch_title = match.group(2).strip()

            start_pos = match.end()
            end_pos = batch_matches[i + 1].start() if i + 1 < len(batch_matches) else len(self.content)
            batch_content = self.content[start_pos:end_pos]

            try:
                batch = self._parse_batch(batch_num, batch_title, batch_content)
                self.batches.append(batch)
            except Exception as e:
                logger.error(f"Failed to parse Batch {batch_num}: {e}")
                raise PlanParseError(f"Batch {batch_num} parsing failed: {e}") from e

        logger.info(f"Parsed {len(self.batches)} batches with {sum(len(b.tasks) for b in self.batches)} total tasks")
        return self.batches

    def _parse_batch(self, batch_num: int, title: str, content: str) -> Batch:
        """Parse a single batch section."""
        execution_mode = self._extract_field(content, r'\*\*Execution Mode:\*\*\s*`?(\w+)`?', default="local")
        dependencies = self._extract_dependency_batches(content)

        task_pattern = r'#{3,4} Task [\d.]+:'
        first_task_match = re.search(task_pattern, content)
        description = content[:first_task_match.start()].strip() if first_task_match else content.strip()

        tasks = self._parse_tasks(batch_num, content)

        return Batch(
            number=batch_num,
            title=title,
            execution_mode=execution_mode,
            dependencies=dependencies,
            tasks=tasks,
            description=description
        )

    def _parse_tasks(self, batch_num: int, content: str) -> List[Task]:
        """Parse all tasks within a batch."""
        task_pattern = r'#{3,4} Task ([\d.a-z]+):\s*([^\n]+)'
        task_matches = list(re.finditer(task_pattern, content))

        tasks = []
        for i, match in enumerate(task_matches):
            task_num = match.group(1)
            task_title = match.group(2).strip()

            start_pos = match.end()
            end_pos = task_matches[i + 1].start() if i + 1 < len(task_matches) else len(content)
            task_content = content[start_pos:end_pos]

            try:
                task = self._parse_task(task_num, task_title, batch_num, task_content)
                tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to parse Task {task_num}: {e}")

        tasks.sort(key=lambda t: self._task_sort_key(t.number))
        return tasks

    def _task_sort_key(self, task_num: str) -> Tuple:
        """Create sort key for task numbers."""
        parts = []
        for part in task_num.split('.'):
            match = re.match(r'(\d+)([a-z]?)', part)
            if match:
                num = int(match.group(1))
                suffix = match.group(2) or ''
                parts.append((num, suffix))
            else:
                parts.append((0, part))
        return tuple(parts)

    def _parse_task(self, task_num: str, title: str, batch_num: int, content: str) -> Task:
        """Parse a single task."""
        files = self._extract_files(content)
        verification = self._extract_verification_steps(content)
        dependencies = self._extract_task_dependencies(content)

        return Task(
            number=task_num,
            title=title,
            batch_number=batch_num,
            files=files,
            implementation=content.strip(),
            verification_steps=verification,
            dependencies=dependencies
        )

    def _extract_field(self, content: str, pattern: str, default: str = "") -> str:
        """Extract a single field using regex."""
        match = re.search(pattern, content, re.IGNORECASE)
        return match.group(1).strip() if match else default

    def _extract_dependency_batches(self, content: str) -> List[int]:
        """Extract batch dependencies."""
        pattern = r'\*\*Dependencies:\*\*\s*(.+)'
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            return []

        dep_text = match.group(1).lower()
        if 'none' in dep_text:
            return []

        batch_nums = re.findall(r'batch\s+(\d+)', dep_text, re.IGNORECASE)
        return [int(num) for num in batch_nums]

    def _extract_files(self, content: str) -> List[str]:
        """Extract file paths from **Files:** section."""
        files = []
        in_files_section = False

        for line in content.split('\n'):
            if '**Files:**' in line or '**Files to Create:**' in line:
                in_files_section = True
                continue

            inline_match = re.search(r'\*\*File:\*\*\s*`?([^`\n]+)`?', line)
            if inline_match:
                path = inline_match.group(1).strip()
                if path and path not in files:
                    files.append(path)
                continue

            if in_files_section:
                if line.startswith('**'):
                    break
                if line.strip().startswith('-'):
                    path_match = re.search(r'-\s+(?:Create|Modify|Update):\s*`?([^`\n]+)`?', line)
                    if path_match:
                        files.append(path_match.group(1).strip())
                    else:
                        simple_match = re.search(r'-\s+`?([^`\n]+)`?', line)
                        if simple_match:
                            path = simple_match.group(1).strip()
                            if path and not path.startswith('*'):
                                files.append(path)

        return files

    def _extract_verification_steps(self, content: str) -> List[str]:
        """Extract verification steps from task."""
        steps = []
        in_verification = False

        for line in content.split('\n'):
            if '**Verification' in line or '**Test' in line:
                in_verification = True
                continue
            if in_verification:
                if line.startswith('**'):
                    break
                if line.strip().startswith('-') or line.strip().startswith('1.'):
                    steps.append(line.strip().lstrip('-').lstrip('1234567890.').strip())

        # Default verification
        if not steps:
            steps = ["pytest -xvs", "ruff check ."]

        return steps

    def _extract_task_dependencies(self, content: str) -> List[str]:
        """Extract task dependencies."""
        pattern = r'\*\*Depends on:\*\*\s*Task\s+([\d.]+)'
        matches = re.findall(pattern, content, re.IGNORECASE)
        return matches

    def get_batch(self, batch_num: int) -> Optional[Batch]:
        """Get a specific batch by number."""
        for batch in self.batches:
            if batch.number == batch_num:
                return batch
        return None

    def get_task(self, task_num: str) -> Optional[Task]:
        """Get a specific task by number."""
        for batch in self.batches:
            for task in batch.tasks:
                if task.number == task_num:
                    return task
        return None
