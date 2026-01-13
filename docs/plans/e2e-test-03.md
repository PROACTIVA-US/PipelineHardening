# E2E Test 03: Sequential Tasks

Test executing multiple tasks in sequence.

## Batch 1: Sequential Task Execution

**Dependencies:** None
**Execution Mode:** local

### Task 1.1: Create First File

**Files:**
- Create: test-artifacts/first.txt

**Implementation:**
Create `test-artifacts/first.txt` with content: "Task 1 complete"

**Verification:**
- cat test-artifacts/first.txt

**Commit:** Create first.txt

### Task 1.2: Create Second File

**Files:**
- Create: test-artifacts/second.txt

**Implementation:**
Create `test-artifacts/second.txt` with content: "Task 2 complete - after Task 1"

**Verification:**
- cat test-artifacts/second.txt

**Commit:** Create second.txt
