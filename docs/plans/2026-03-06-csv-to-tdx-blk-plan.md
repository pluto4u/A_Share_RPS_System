# CSV To TDX BLK Implementation Plan

> **REQUIRED SUB-SKILL:** Read and follow `~/.cursor/skills/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Add a standalone script that converts screener CSV output into a TDX `.blk` custom block file with sensible defaults and optional CLI overrides.

**Architecture:** Create a small root-level CLI module with reusable helper functions for code conversion, latest-CSV discovery, default block naming, and `.blk` writing. Add focused pytest coverage for code conversion, default naming, file generation, and error handling.

**Tech Stack:** Python, pandas, pytest, pathlib

---

### Task 1: Add failing tests for conversion behavior

**Files:**
- Create: `tests/test_csv_to_tdx_blk.py`
- Test: `tests/test_csv_to_tdx_blk.py`

**Step 1: Write the failing test**

```python
def test_ts_code_to_blk_code():
    from csv_to_tdx_blk import to_tdx_blk_code
    assert to_tdx_blk_code("600519.SH") == "1600519"
```

**Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_csv_to_tdx_blk.py -q`
Expected: FAIL with import error or missing function.

### Task 2: Implement standalone converter script

**Files:**
- Create: `csv_to_tdx_blk.py`
- Modify: `tests/test_csv_to_tdx_blk.py`

**Step 1: Write minimal implementation**

```python
def to_tdx_blk_code(ts_code: str) -> str:
    ...
```

**Step 2: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_csv_to_tdx_blk.py -q`
Expected: PASS

### Task 3: Verify CLI output and lint

**Files:**
- Modify: `csv_to_tdx_blk.py`
- Test: `tests/test_csv_to_tdx_blk.py`

**Step 1: Run tests**

Run: `py -3 -m pytest tests/test_csv_to_tdx_blk.py -q`

**Step 2: Run lint diagnostics**

Use IDE lint diagnostics for `csv_to_tdx_blk.py`.
