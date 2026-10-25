from project_paths import script_file
"""Deterministic formula boundaries and local RPS reader; no network."""

import importlib.util
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rps_analyzer.right_confirmation import drawdown_series, evaluate, tri_and

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "confirmation_runner", script_file("run_right_confirmation.py")
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def rising():
    c = np.linspace(10, 40, 320)
    return pd.DataFrame(
        {
            "close": c,
            "high": c + 0.1,
            "low": c - 0.1,
            "vol": np.ones(320) * 1e7,
            "amount": np.ones(320) * 2e8,
        }
    )


def test_missing_turnover_is_unknown_not_pass():
    result = evaluate(rising(), 96.0, 95.0, None)
    assert result["SXHCG5"] is None
    assert result["baseline"] is None
    assert result["confirmed"] is False


def test_price_references_use_documented_windows():
    frame = rising()
    frame.loc[frame.index[-1], "high"] = 1000.0
    frame.loc[frame.index[-1], "low"] = 1.0
    result = evaluate(frame, 96.0, 95.0, 1.0)
    assert result["ma20_reference"] == pytest.approx(frame.close.tail(20).mean())
    assert result["prior_high20"] == pytest.approx(frame.high.iloc[-21:-1].max())
    assert result["prior_low10"] == pytest.approx(frame.low.iloc[-11:-1].min())


def test_watchlist_hides_failed_missing_and_extended_stages():
    from rps_analyzer.watchlist_view import watchlist

    table = pd.DataFrame(
        {
            "stage": [
                "趋势观察",
                "缩量整理观察",
                "放量突破确认",
                "回踩再确认",
                "数据待核验",
                "偏离过大观察",
                "未满足确认基础",
            ]
        }
    )
    assert len(watchlist(table)) == 4


def test_thresholds_are_strict_and_volacc_not_required():
    assert evaluate(rising(), 90.0, 90.0, 1.0)["SXHCG1"] is False
    result = evaluate(rising(), 96.0, 95.0, 1.0)
    assert result["baseline"] is True
    assert result["volacc_defined_not_used"] is False
    assert evaluate(rising(), 96.0, 95.0, 10.0)["SXHCG5"] is False


def test_count_zero_sees_old_drawdown():
    frame = rising()
    frame.loc[30, "low"] = frame.loc[30, "high"] * 0.6
    result = evaluate(frame, 96.0, 95.0, 1.0)
    assert result["high_age20"] == 0
    assert result["SXHCG3"] is False
    assert result["corrected_base"] is True


def test_dynamic_drawdown_matches_independent_scalar_reference():
    rng = np.random.default_rng(10)
    high = rng.uniform(10.0, 20.0, 130)
    low = high * rng.uniform(0.6, 0.99, 130)
    ages, dd, counts = drawdown_series(high, low)
    expected = []
    for t in range(len(high)):
        indices = list(range(max(0, t - 19), t + 1))
        peak = max(indices, key=lambda i: (high[i], i))
        age = t - peak
        trough = low[t] if age == 0 else min(low[t - age + 1 : t + 1])
        expected.append((high[peak] - trough) / high[peak])
        history = expected if age == 0 else expected[-age:]
        assert ages[t] == age
        assert dd[t] == pytest.approx(expected[-1])
        assert counts[t] == sum(x > 0.25 for x in history)


def test_latest_high_tie_and_high_bar_low_excluded():
    ages, dd, _ = drawdown_series([10.0, 12.0, 12.0, 11.0], [9.0, 1.0, 10.0, 10.0])
    assert ages[-1] == 1
    assert dd[-1] == pytest.approx(2 / 12)


def test_three_valued_logic():
    assert tri_and([True, None]) is None
    assert tri_and([False, None]) is False


def test_extdata_exact_date_and_divide_by_ten(tmp_path):
    idx = bytearray(29)
    struct.pack_into("<H", idx, 0, 1)
    idx[2:8] = b"688536"
    struct.pack_into("<I", idx, 25, 2)
    (tmp_path / "extdata_6.idx").write_bytes(idx)
    (tmp_path / "extdata_6.dat").write_bytes(
        struct.pack("<IIfIIf", 20260923, 0, 981.0, 20260924, 0, 983.0)
    )
    assert runner.extdata(tmp_path, 6, 20260924) == {"688536.SH": 98.3}
    assert runner.extdata(tmp_path, 6, 20260925) == {}


def test_short_history_blocks():
    with pytest.raises(ValueError, match="280"):
        evaluate(rising().tail(250), 96.0, 95.0, 1.0)


def test_real_confirmation_path_and_no_current_bar_in_reference():
    d = rising()
    closes = np.r_[np.linspace(39.0, 39.8, 20), 40.8]
    d.loc[d.index[-21:], "close"] = closes
    d.loc[d.index[-21:], "high"] = closes + 0.1
    d.loc[d.index[-21:], "low"] = closes - 0.1
    d.loc[d.index[-6:-1], "vol"] = 6e6
    d.loc[d.index[-1], ["high", "low", "vol"]] = [41.0, 40.0, 17e6]
    result = evaluate(d, 96.0, 95.0, 1.0)
    assert result["stage"] == "放量突破确认"
    assert result["confirmed"] is True
    assert result["volume_ratio"] == pytest.approx(17 / 9)
    assert evaluate(d, 96.0, 95.0, None)["confirmed"] is False
