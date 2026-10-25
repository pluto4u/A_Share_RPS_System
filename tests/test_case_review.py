from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trend_start.case_review import CaseWindow, describe_daily, ma20_episodes


def frame() -> pd.DataFrame:
    close = np.r_[np.full(30, 10.0), [9.0, 8.0, 9.0, 11.0, 12.0]]
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-06-01", periods=len(close)).strftime("%Y-%m-%d"),
            "close": close,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "vol": np.full(len(close), 100.0),
            "RPS50": np.full(len(close), 60.0),
            "RPS120": np.full(len(close), 99.0),
            "RPS250": np.full(len(close), 98.0),
        }
    )


def test_high_long_rps_does_not_hide_price_break() -> None:
    d = describe_daily(frame())
    assert d.iloc[30].cross_below_ma20
    assert d.iloc[30].RPS120 == 99
    assert d.iloc[30].below_ma20
    assert d.iloc[33].cross_above_ma20


def test_daily_features_do_not_use_future() -> None:
    source = frame()
    first = describe_daily(source)
    source.loc[33:, "close"] = 500
    source.loc[33:, "high"] = 501
    second = describe_daily(source)
    pd.testing.assert_frame_equal(first.iloc[:33], second.iloc[:33])


def test_episode_dates_and_hindsight_flag() -> None:
    d = describe_daily(frame())
    episodes = ma20_episodes(d, CaseWindow())
    assert len(episodes) == 1
    row = episodes.iloc[0]
    assert row.traded_bars_below == 3
    assert row.recovery_date == d.iloc[33].date
    assert row.review_trough_date == d.iloc[31].date
    assert row.review_peak_to_trough_pct == pytest.approx(-20)
    assert row.trough_uses_episode_future_bars


def test_unfinished_has_no_fabricated_recovery() -> None:
    d = describe_daily(frame().iloc[:33])
    row = ma20_episodes(d, CaseWindow()).iloc[0]
    assert row.unfinished
    assert row.recovery_date is None
    assert np.isnan(row.RPS120_recovery)


def test_missing_rps_not_zero_filled() -> None:
    source = frame()
    source["RPS50"] = np.nan
    d = describe_daily(source)
    assert d.RPS50.isna().all()
    assert np.isnan(ma20_episodes(d, CaseWindow()).iloc[0].RPS50_episode_min)


@pytest.mark.parametrize("bad", ["missing_field", "duplicate_date", "bad_rank", "bad_price"])
def test_errors_explicit(bad: str) -> None:
    source = frame()
    if bad == "missing_field":
        source = source.drop(columns="vol")
    elif bad == "duplicate_date":
        source.loc[1, "date"] = source.loc[0, "date"]
    elif bad == "bad_rank":
        source.loc[1, "RPS120"] = 101
    else:
        source.loc[1, "close"] = 0
    with pytest.raises(ValueError):
        describe_daily(source)
