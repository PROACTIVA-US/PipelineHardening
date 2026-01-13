# E2E Test 07: Large File (500+ lines)

Test handling of large file creation.

## Batch 1: Large File Creation

**Dependencies:** None
**Execution Mode:** local

### Task 1.1: Create Large File

**Files:**
- Create: test-artifacts/large.txt

**Implementation:**
Create `test-artifacts/large.txt` with 500 lines. Each line should follow this format:
"Line {n}: Lorem ipsum dolor sit amet, consectetur adipiscing elit."

Where {n} is the line number from 1 to 500.

**Verification:**
- wc -l test-artifacts/large.txt

**Commit:** Create large test file with 500 lines
