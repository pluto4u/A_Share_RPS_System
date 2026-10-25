from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "a-share-rotation-trader" / "scripts" / "run_integrated_decision.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("integrated_decision_qfq", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_project_root_and_default_db_are_resolved_from_both_skill_layouts() -> None:
    assert MODULE.ROOT == ROOT
    assert MODULE.DEFAULT_HITHINK_DB == ROOT / "data" / "hithink" / "market.duckdb"


def test_build_snapshot_wires_qfq_loader_into_indicators_and_rps() -> None:
    source = inspect.getsource(MODULE.build_snapshot)
    assert "load_hithink_qfq" in source
    assert "add_indicators(price_daily)" in source
    assert "calculate_rps(price_daily" in source
    assert "load_tail(" not in source


def test_hithink_qfq_loader_matches_frozen_rps_snapshot() -> None:
    db = ROOT / "data" / "hithink" / "market.duckdb"
    trusted_path = ROOT / "output" / "hithink_rps_latest.csv"
    if not db.exists() or not trusted_path.exists() or shutil.which("hithink-finance.cmd") is None:
        pytest.skip("本机没有同花顺生产库、冻结 RPS 快照或 hithink-finance CLI")

    daily, meta = MODULE.load_hithink_qfq(
        db, pd.Timestamp("2026-08-28"), n_bars=320, min_bars=260)
    assert meta["date_max"] == "2026-08-28"
    assert meta["symbols"] >= 5200
    assert meta["duplicate_rows"] == 0
    assert meta["null_prices"] == 0
    assert meta["invalid_prices"] == 0
    assert meta["invalid_ranges"] == 0
    assert not daily.duplicated(["ts_code", "trade_date"]).any()
    assert (daily[["vol", "amount"]] >= 0).all().all()

    bars = daily.drop(columns=["name"])
    calculated = MODULE.calculate_rps(
        bars, periods=[50, 120, 250], verbose=False)
    latest_date = calculated["trade_date"].max()
    latest = calculated[calculated["trade_date"] == latest_date][
        ["ts_code", "RPS_50", "RPS_120", "RPS_250"]
    ]
    trusted = pd.read_csv(trusted_path, usecols=[
        "ts_code", "RPS_50", "RPS_120", "RPS_250"])
    check = latest.merge(trusted, on="ts_code", suffixes=("_new", "_trusted"),
                         validate="one_to_one")
    assert len(check) >= 5200
    for period in (50, 120, 250):
        valid = check[[f"RPS_{period}_new", f"RPS_{period}_trusted"]].dropna()
        diff = (valid[f"RPS_{period}_new"] - valid[f"RPS_{period}_trusted"]).abs()
        assert len(valid) >= 5000
        assert float(diff.max()) <= 1e-10


def test_candidate_history_gate_is_applied_after_rps_universe() -> None:
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    frame = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 260,
        "trade_date": dates,
        "open": range(1, 261),
        "high": range(2, 262),
        "low": range(1, 261),
        "close": range(1, 261),
        "vol": [1000.0] * 260,
        "amount": [2.0e8] * 260,
    })
    work = MODULE.add_indicators(frame)
    assert int(work.iloc[-1]["bars_count"]) == 260
    assert int(work.iloc[-2]["bars_count"]) == 259
