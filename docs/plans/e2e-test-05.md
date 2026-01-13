# E2E Test 05: Intentional Rollback

Test partial failure handling and cleanup.

## Batch 1: Partial Failure Test

**Dependencies:** None
**Execution Mode:** local

### Task 1.1: Create Then Fail

**Files:**
- Create: test-artifacts/temp.txt
- Create: /invalid/path/test.txt

**Implementation:**
First create `test-artifacts/temp.txt` with content "Temporary file", then attempt to write to `/invalid/path/test.txt`.

**Verification:**
- cat test-artifacts/temp.txt
- cat /invalid/path/test.txt

**Commit:** Create temp file and attempt invalid write
