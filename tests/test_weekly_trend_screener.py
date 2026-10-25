import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rps_analyzer.weekly_trend_screener import WeeklyTrendConfig, WeeklyTrendScreener


def _stock_frame(code: str, dates: pd.DatetimeIndex, start: float, drift: float) -> pd.DataFrame:
    n = len(dates)
    closes = start * np.cumprod(np.full(n, 1 + drift))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": code,
            "open": closes * 0.995,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "vol": np.full(n, 10_000_000.0),
            "amount": np.full(n, 200_000_000.0),
        }
    )


def test_weekly_trend_selects_steady_trend_as_tier_a():
    dates = pd.bdate_range("2023-01-02", periods=720)
    frames = [_stock_frame("600001.SH", dates, 10.0, 0.0025)]
    for i in range(9):
        frames.append(_stock_frame(f"6001{i:02d}.SH", dates, 10.0, 0.0002 + i * 0.00005))
    market = pd.concat(frames, ignore_index=True)

    config = WeeklyTrendConfig(
        rps120w_min=90,
        min_weekly_amount=0,
        max_vs_ma20w=0.50,
        a_pullback_vs_ma20d_max=0.20,
    )
    result = WeeklyTrendScreener(config).screen(market, target_date="20250930", verbose=False)

    row = result[result["ts_code"] == "600001.SH"]
    assert not row.empty
    assert row.iloc[0]["TREND_TIER"] == "A"


def test_weekly_trend_excludes_overextended_weekly_move():
    dates = pd.bdate_range("2023-01-02", periods=720)
    frames = [_stock_frame("600001.SH", dates, 10.0, 0.006)]
    for i in range(9):
        frames.append(_stock_frame(f"6001{i:02d}.SH", dates, 10.0, 0.0002 + i * 0.00005))
    market = pd.concat(frames, ignore_index=True)

    config = WeeklyTrendConfig(rps120w_min=90, min_weekly_amount=0, max_vs_ma20w=0.10)
    result = WeeklyTrendScreener(config).screen(market, target_date="20250930", verbose=False)

    assert "600001.SH" not in set(result["ts_code"])
