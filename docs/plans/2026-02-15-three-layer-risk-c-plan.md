# Three-Layer Risk C Plan Implementation Plan

> **REQUIRED SUB-SKILL:** Read and follow `~/.cursor/skills/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Add a unified three-layer risk engine and wire it into both daily backtest and live market monitoring in one implementation pass.

**Architecture:** Introduce a new `risk_engine.py` module that computes portfolio-level, strategy-level, and execution-level caps. Reuse its output in `backtest_sentiment_daily.py` for deterministic simulation and in `market_gambler_watch.py` for runtime status display. Keep existing sentiment scoring intact and apply risk controls as an additional cap layer.

**Tech Stack:** Python, pandas, numpy, pytest

---

### Task 1: Add risk engine core

**Files:**
- Create: `risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Step 1: Write the failing test**

Define tests for:
- no guard triggered -> `risk_state=normal`
- daily loss breach -> halt new positions
- drawdown breach -> hard cap to low position
- streak breach -> reduced cap with explicit reason

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py -v`  
Expected: FAIL due to missing module.

**Step 3: Write minimal implementation**

Implement:
- `RiskConfig` defaults
- `evaluate_risk_controls(...)`
- stable return schema: `risk_state`, `halt_new_entries`, `position_cap_after_risk`, `halt_reason`, `flags`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk_engine.py -v`  
Expected: PASS.

### Task 2: Upgrade daily backtest with risk controls and richer metrics

**Files:**
- Modify: `backtest_sentiment_daily.py`
- Modify: `tests/test_backtest_sentiment_daily.py`

**Step 1: Write the failing test**

Add tests for:
- output includes `win_rate`, `profit_factor`, `max_loss_streak`
- risk cap reduces effective exposure when breach context is supplied

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_sentiment_daily.py -v`  
Expected: FAIL on missing metrics.

**Step 3: Write minimal implementation**

Add:
- optional `risk_context_df` input
- per-day risk cap using `evaluate_risk_controls`
- new metric calculations from strategy returns

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_sentiment_daily.py -v`  
Expected: PASS.

### Task 3: Wire risk status into live monitor output

**Files:**
- Modify: `market_gambler_watch.py`

**Step 1: Add runtime risk context**

Derive simple runtime context from existing sentiment data:
- daily return proxy (`money_effect`)
- drawdown proxy (optional)
- loss streak proxy (optional, conservative defaults)

**Step 2: Print risk section**

Add a dedicated output section showing:
- risk state
- cap after risk
- halt reason

**Step 3: Verify script still runs**

Run: `python market_gambler_watch.py`  
Expected: report includes new risk section.

### Task 4: Regression verification

**Files:**
- No additional code changes unless failures appear

**Step 1: Run focused test set**

Run:
- `pytest tests/test_risk_engine.py -v`
- `pytest tests/test_backtest_sentiment_daily.py -v`
- `pytest tests/test_sentiment_engine.py -v`

Expected: PASS.

**Step 2: Run full test suite if fast**

Run: `pytest -q`  
Expected: no regressions.
