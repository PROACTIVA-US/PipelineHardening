# E2E Test 02: Multi-File Task

Test creating multiple files in a single task.

## Batch 1: Multi-File Creation

**Dependencies:** None
**Execution Mode:** local

### Task 1.1: Create Multiple Files

**Files:**
- Create: test-artifacts/file-a.txt
- Create: test-artifacts/file-b.txt
- Create: test-artifacts/file-c.txt

**Implementation:**
Create these three files:

1. `test-artifacts/file-a.txt` with content: "File A content"
2. `test-artifacts/file-b.txt` with content: "File B content"
3. `test-artifacts/file-c.txt` with content: "File C content"

**Verification:**
- cat test-artifacts/file-a.txt
- cat test-artifacts/file-b.txt
- cat test-artifacts/file-c.txt

**Commit:** Create multiple test files
