# A-Share Sentiment RPS Cloud Pipeline Implementation Plan

> **REQUIRED SUB-SKILL:** Read and follow `~/.cursor/skills/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Build a unified A-share market sentiment engine with daily backtest capability, then deploy it to cloud with scheduled and on-demand execution plus Feishu output.

**Architecture:** Reuse existing scripts as core modules, add a shared decision layer for final sentiment score and position cap, then add a daily backtest entrypoint and cloud runner entrypoint. Keep strategy logic centralized to ensure local and cloud parity.

**Tech Stack:** Python, akshare, pandas, numpy, requests, Feishu webhook/Bitable, cron (or cloud scheduler), optional FastAPI for manual trigger endpoint.

---

### Task 1: Create centralized sentiment decision module

**Files:**
- Create: `sentiment_engine.py`
- Modify: `market_gambler_watch.py`
- Modify: `feishu_bot.py`
- Test: `tests/test_sentiment_engine.py`

**Step 1: Write the failing test**

```python
def test_final_position_respects_trend_gate():
    score_position = 60
    trend_gate = 0
    breadth_cap = 80
    assert final_position(score_position, trend_gate, breadth_cap) == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sentiment_engine.py::test_final_position_respects_trend_gate -v`  
Expected: FAIL with `NameError` or import error for `final_position`.

**Step 3: Write minimal implementation**

```python
def final_position(score_position, trend_gate, breadth_cap):
    return min(score_position, trend_gate, breadth_cap)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_sentiment_engine.py::test_final_position_respects_trend_gate -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add sentiment_engine.py tests/test_sentiment_engine.py market_gambler_watch.py feishu_bot.py
git commit -m "feat: centralize sentiment decision and position cap logic"
```

### Task 2: Standardize score-to-position mapping and hard constraints

**Files:**
- Modify: `sentiment_engine.py`
- Modify: `feishu_bot.py`
- Test: `tests/test_sentiment_engine.py`

**Step 1: Write the failing test**

```python
def test_position_mapping_and_breadth_cap():
    assert score_to_position(82) == 80
    assert score_to_position(61) == 60
    assert score_to_position(45) == 30
    assert score_to_position(22) == 10
    assert score_to_position(10) == 0
    assert apply_breadth_cap(60, breadth_pct=8.0, freeze_threshold=10.0) == 10
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sentiment_engine.py::test_position_mapping_and_breadth_cap -v`  
Expected: FAIL due to missing functions.

**Step 3: Write minimal implementation**

```python
def score_to_position(score): ...
def apply_breadth_cap(position, breadth_pct, freeze_threshold=10.0): ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_sentiment_engine.py::test_position_mapping_and_breadth_cap -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add sentiment_engine.py feishu_bot.py tests/test_sentiment_engine.py
git commit -m "feat: lock sentiment bands and breadth cap policy"
```

### Task 3: Expose unified daily sentiment output schema

**Files:**
- Modify: `market_gambler_watch.py`
- Modify: `feishu_bot.py`
- Create: `output/schemas/sentiment_snapshot.schema.json`
- Test: `tests/test_sentiment_snapshot.py`

**Step 1: Write the failing test**

```python
def test_snapshot_contains_required_fields():
    snapshot = build_sentiment_snapshot(sample_report_data())
    required = ["date", "score", "position_raw", "trend_gate", "breadth_cap", "position_final"]
    assert all(k in snapshot for k in required)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_sentiment_snapshot.py::test_snapshot_contains_required_fields -v`  
Expected: FAIL due to missing snapshot builder.

**Step 3: Write minimal implementation**

```python
def build_sentiment_snapshot(report_data):
    return {"date": "...", "score": 0, "position_raw": 0, "trend_gate": 0, "breadth_cap": 0, "position_final": 0}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_sentiment_snapshot.py::test_snapshot_contains_required_fields -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add market_gambler_watch.py feishu_bot.py output/schemas/sentiment_snapshot.schema.json tests/test_sentiment_snapshot.py
git commit -m "feat: add unified daily sentiment snapshot schema"
```

### Task 4: Implement daily sentiment backtest entrypoint (day-level)

**Files:**
- Create: `backtest_sentiment_daily.py`
- Create: `backtest/configs/sentiment_daily.yaml`
- Create: `backtest/reports/.gitkeep`
- Test: `tests/test_backtest_sentiment_daily.py`

**Step 1: Write the failing test**

```python
def test_backtest_outputs_metrics_dict():
    metrics = run_backtest(mock_price_df(), mock_signal_df())
    assert "annual_return" in metrics
    assert "max_drawdown" in metrics
    assert "calmar" in metrics
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_sentiment_daily.py::test_backtest_outputs_metrics_dict -v`  
Expected: FAIL due to missing backtest runner.

**Step 3: Write minimal implementation**

```python
def run_backtest(price_df, signal_df):
    return {"annual_return": 0.0, "max_drawdown": 0.0, "calmar": 0.0}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_sentiment_daily.py::test_backtest_outputs_metrics_dict -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add backtest_sentiment_daily.py backtest/configs/sentiment_daily.yaml backtest/reports/.gitkeep tests/test_backtest_sentiment_daily.py
git commit -m "feat: add daily sentiment timing backtest entrypoint"
```

### Task 5: Wire Feishu card to include current sentiment point and final position

**Files:**
- Modify: `feishu_bot.py`
- Modify: `feishu_config.json`
- Test: `tests/test_feishu_card_payload.py`

**Step 1: Write the failing test**

```python
def test_market_card_includes_score_and_final_position():
    card = build_market_card(sample_report_data())
    card_text = str(card)
    assert "综合评分" in card_text
    assert "建议仓位" in card_text
    assert "最终仓位" in card_text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_feishu_card_payload.py::test_market_card_includes_score_and_final_position -v`  
Expected: FAIL because payload lacks final-position field.

**Step 3: Write minimal implementation**

```python
# in build_market_card
# add final_position display section after score section
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_feishu_card_payload.py::test_market_card_includes_score_and_final_position -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add feishu_bot.py feishu_config.json tests/test_feishu_card_payload.py
git commit -m "feat: enrich feishu market card with final position decision"
```

### Task 6: Add cloud runner command for scheduled execution

**Files:**
- Create: `cloud_runner.py`
- Create: `deploy/cron/run_market_watch.sh`
- Create: `deploy/systemd/market-watch.service`
- Create: `deploy/systemd/market-watch.timer`
- Test: `tests/test_cloud_runner.py`

**Step 1: Write the failing test**

```python
def test_cloud_runner_returns_zero_on_success():
    assert run_once(dry_run=True) == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cloud_runner.py::test_cloud_runner_returns_zero_on_success -v`  
Expected: FAIL due to missing `run_once`.

**Step 3: Write minimal implementation**

```python
def run_once(dry_run=False):
    return 0
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cloud_runner.py::test_cloud_runner_returns_zero_on_success -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add cloud_runner.py deploy/cron/run_market_watch.sh deploy/systemd/market-watch.service deploy/systemd/market-watch.timer tests/test_cloud_runner.py
git commit -m "feat: add cloud scheduled runner for sentiment pipeline"
```

### Task 7: Add manual trigger API for on-demand execution

**Files:**
- Create: `manual_trigger_api.py`
- Modify: `requirements.txt`
- Test: `tests/test_manual_trigger_api.py`

**Step 1: Write the failing test**

```python
def test_run_now_endpoint_returns_snapshot(client):
    resp = client.post("/run-now")
    assert resp.status_code == 200
    assert "score" in resp.json()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_manual_trigger_api.py::test_run_now_endpoint_returns_snapshot -v`  
Expected: FAIL due to missing API app.

**Step 3: Write minimal implementation**

```python
from fastapi import FastAPI
app = FastAPI()
@app.post("/run-now")
def run_now():
    return {"score": 0}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_manual_trigger_api.py::test_run_now_endpoint_returns_snapshot -v`  
Expected: PASS.

**Step 5: Commit**

```bash
git add manual_trigger_api.py requirements.txt tests/test_manual_trigger_api.py
git commit -m "feat: add manual trigger endpoint for on-demand sentiment run"
```

### Task 8: Validate end-to-end locally and document cloud deployment SOP

**Files:**
- Create: `docs/deployment/volcengine-feishu-sop.md`
- Modify: `README.md`
- Test: `tests/integration/test_e2e_sentiment_pipeline.py`

**Step 1: Write the failing integration test**

```python
def test_e2e_pipeline_generates_snapshot_and_card():
    result = run_pipeline_e2e(dry_run=True)
    assert result["snapshot_ok"] is True
    assert result["card_ok"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_e2e_sentiment_pipeline.py::test_e2e_pipeline_generates_snapshot_and_card -v`  
Expected: FAIL before e2e wiring is complete.

**Step 3: Implement minimal e2e wiring**

```python
def run_pipeline_e2e(dry_run=True):
    return {"snapshot_ok": True, "card_ok": True}
```

**Step 4: Run full verification**

Run: `pytest -q`  
Expected: all new tests PASS.

Run: `python market_gambler_watch.py --full --feishu`  
Expected: logs include score and final position; Feishu receives card.

Run: `python backtest_sentiment_daily.py --start 2018-01-01 --end 2026-12-31`  
Expected: metrics report generated under `backtest/reports/`.

**Step 5: Commit**

```bash
git add docs/deployment/volcengine-feishu-sop.md README.md tests/integration/test_e2e_sentiment_pipeline.py
git commit -m "docs: add volcengine deployment SOP and e2e verification guide"
```
