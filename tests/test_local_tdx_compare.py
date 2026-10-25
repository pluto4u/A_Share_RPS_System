from project_paths import script_file
"""Offline tests: dated bars, adjustments, fail-closed gates and set comparison."""

import importlib.util
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_compare", script_file("run_local_tdx_compare.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def raw():
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24"]),
            **{x: [10.0, 9.0, 9.5] for x in module.PRICE},
            "vol": [100.0, 200.0, 300.0],
            "amount": [1000.0, 1800.0, 2850.0],
        }
    )


def events(category=1):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-23"]),
            "category": [category],
            **dict(zip(module.EVENT_FIELDS, [[10.0], [0.0], [0.0], [0.0]], strict=True)),
        }
    )


def test_cash_adjustment_and_raw_volume():
    d, n = module.adjust(raw(), events(), pd.Timestamp("2026-09-24"))
    np.testing.assert_allclose(d.close, [9.0, 9.0, 9.5])
    np.testing.assert_allclose(d.vol, raw().vol)
    np.testing.assert_allclose(d.amount, raw().amount)
    assert n == 1


def test_future_action_and_future_bar_excluded():
    d, n = module.adjust(raw(), events(), pd.Timestamp("2026-09-22"))
    assert len(d) == 1 and d.close.iloc[0] == 10 and n == 0


def test_bonus_and_rights():
    ev = events()
    ev[module.EVENT_FIELDS] = [0.0, 5.0, 10.0, 2.0]
    d, _ = module.adjust(raw(), ev, pd.Timestamp("2026-09-24"))
    assert d.close.iloc[0] == pytest.approx(110 / 22)


def test_unsupported_action_blocks():
    with pytest.raises(ValueError, match="unsupported"):
        module.adjust(raw(), events(11), pd.Timestamp("2026-09-24"))


def test_missing_target_is_not_zero_candidates(tmp_path):
    folder = tmp_path / "vipdoc/sh/lday"
    folder.mkdir(parents=True)
    (folder / "sh600000.day").write_bytes(
        struct.pack("<IIIIIfII", 20260520, 1000, 1000, 1000, 1000, 10000.0, 1000, 0)
    )
    dates = [pd.Timestamp("2026-09-23"), pd.Timestamp("2026-09-24")]
    assert not module.preflight(tmp_path, dates, tmp_path)
    audit = json.loads((tmp_path / "preflight.json").read_text(encoding="utf-8"))
    assert audit["candidate_counts"] is None
    assert audit["date_coverage_counts"] == {"2026-09-23": 0, "2026-09-24": 0}


def test_comparison_sets():
    a = pd.DataFrame({"ts_code": ["a", "b"], "name": ["A", "B"], "RPS_120": [70.0, 80.0]})
    b = pd.DataFrame({"ts_code": ["b", "c"], "name": ["B", "C"], "RPS_120": [85.0, 90.0]})
    d = module.compare(a, b).set_index("ts_code")
    assert d.change.to_dict() == {"a": "退出", "b": "持续", "c": "新增"}
    assert d.loc["b", "rps120_delta"] == 5


def test_memory_efficient_rps_matches_original():
    from rps_analyzer.rps_calculator import calculate_rps

    dates = pd.date_range("2025-01-01", periods=280)
    frames = [pd.DataFrame({"ts_code": code, "trade_date": dates,
                           "close": np.arange(280) * slope + 10.})
              for code, slope in [("a", .1), ("b", .2), ("c", .2)]]
    data = pd.concat(frames, ignore_index=True)
    expected = calculate_rps(data, verbose=False)
    data["ts_code"] = data.ts_code.astype("category")
    module.rps_inplace(data)
    for period in (50, 120, 250):
        np.testing.assert_allclose(data[f"RPS_{period}"], expected[f"RPS_{period}"], equal_nan=True)
