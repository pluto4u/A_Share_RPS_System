# Intraday Sentiment Loop Implementation Plan

> **REQUIRED SUB-SKILL:** Read and follow `~/.cursor/skills/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Add 15-minute intraday periodic sentiment judgments with Feishu push on every run, only during trading sessions.

**Architecture:** Keep `market_gambler_watch.py` as the single entrypoint and extract one-run execution into a reusable function. Add a loop runner with trading-session guard and graceful error handling so the process can run continuously intraday.

**Tech Stack:** Python 3.12, argparse, datetime, pytest

---

### Task 1: Add trading-session utility and loop orchestration functions

**Files:**
- Modify: `market_gambler_watch.py`
- Test: `tests/test_intraday_loop.py`

**Step 1: Write the failing test**

Add tests for:
- `is_trading_session()` true in 09:30-11:30 and 13:00-15:00 weekdays
- false in lunch break and weekends

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_loop.py -k trading_session -v`  
Expected: FAIL with missing function

**Step 3: Write minimal implementation**

Implement:
- `is_trading_session(now=None)`
- `run_loop(...)` skeleton that supports interval and optional session-only guard

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_loop.py -k trading_session -v`  
Expected: PASS

---

### Task 2: Extract single-run behavior and wire loop mode

**Files:**
- Modify: `market_gambler_watch.py`
- Test: `tests/test_intraday_loop.py`

**Step 1: Write the failing test**

Add tests for:
- in `session_only=True` and non-trading time, loop does not call `run_once`
- in trading time, loop calls `run_once`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_loop.py -k loop -v`  
Expected: FAIL

**Step 3: Write minimal implementation**

Implement:
- `run_once(full=False, feishu=False)` from current `main()` body
- `main()` parse new args:
  - `--loop`
  - `--interval-minutes` (default 15)
  - `--session-only`
- In loop mode call `run_loop(...)`; otherwise call `run_once(...)`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_loop.py -k loop -v`  
Expected: PASS

---

### Task 3: Regression verification for existing behavior

**Files:**
- Existing tests only

**Step 1: Run focused regressions**

Run:
- `pytest tests/test_sentiment_engine.py -v`
- `pytest tests/test_feishu_card_payload.py -v`

Expected: PASS

**Step 2: Smoke-check CLI help**

Run: `python market_gambler_watch.py -h`  
Expected: contains `--loop`, `--interval-minutes`, `--session-only`

---

### Task 4: Documentation update

**Files:**
- Modify: `README.md`

**Step 1: Add intraday periodic command examples**

Document:
- default 15m intraday loop
- full mode intraday loop

**Step 2: Verify markdown snippet correctness**

Run: manual read-through

