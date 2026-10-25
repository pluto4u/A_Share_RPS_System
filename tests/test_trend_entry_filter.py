import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rps_analyzer.trend_entry_filter import TrendEntryConfig, filter_trend_entries


def _daily_frame(code: str, closes: np.ndarray) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": code,
            "open": closes * 0.998,
            "high": closes * 1.03,
            "low": closes * 0.99,
            "close": closes,
            "vol": np.full(len(closes), 10_000_000.0),
            "amount": np.full(len(closes), 200_000_000.0),
        }
    )


def _trend_pool(code: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [code],
            "TREND_TIER": ["A"],
            "W_RPS_120": [96.0],
            "trend_score": [80.0],
        }
    )


def test_trend_entry_ready_for_orderly_pullback_above_ma20():
    closes = np.linspace(100, 130, 90)
    closes[-5:] = [132, 131, 129, 128, 127]
    daily = _daily_frame("600001.SH", closes)

    cfg = TrendEntryConfig(
        max_ready_vs_ma20d=0.30,
        max_ready_ret20d=1.00,
        max_ret5d=0.50,
        max_vol3_ratio_ready=2.0,
        max_range5_ready=0.50,
    )
    result = filter_trend_entries(daily, _trend_pool("600001.SH"), config=cfg, verbose=False)

    assert not result.empty
    assert result.iloc[0]["ENTRY_TIER"] == "READY"


def test_trend_entry_excludes_break_below_ma20():
    closes = np.linspace(100, 130, 90)
    closes[-5:] = [120, 116, 112, 108, 104]
    daily = _daily_frame("600001.SH", closes)

    cfg = TrendEntryConfig(
        max_ready_vs_ma20d=0.50,
        max_watch_vs_ma20d=0.50,
        max_ready_ret20d=1.00,
        max_watch_ret20d=1.00,
        max_ret5d=0.50,
        max_vol3_ratio_ready=2.0,
        max_vol3_ratio_watch=2.0,
        max_range5_ready=0.80,
        max_range5_watch=0.80,
    )
    result = filter_trend_entries(daily, _trend_pool("600001.SH"), config=cfg, verbose=False)

    assert result.empty
