from __future__ import annotations

import numpy as np
import pandas as pd

from rps_analyzer.fixed_window_drawdown import (
    FixedWindowDrawdownConfig,
    screen_fixed_window_drawdown,
    screen_reference_close_window_breakout,
    screen_reference_high_breakout,
)


DATES = pd.bdate_range("2026-05-01", "2026-08-06")


def _frame(code: str, *, low_before_high: bool = False, invalid: bool = False) -> pd.DataFrame:
    close = np.linspace(80.0, 110.0, len(DATES))
    frame = pd.DataFrame(
        {
            "trade_date": DATES,
            "ts_code": code,
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "vol": 1_000_000.0,
            "amount": close * 1_000_000.0,
        }
    )
    july = frame["trade_date"].between("2026-07-01", "2026-07-31")
    frame.loc[july, ["open", "high", "low", "close"]] = [97.0, 99.0, 96.0, 98.0]
    if low_before_high:
        low_date, high_date = pd.Timestamp("2026-07-02"), pd.Timestamp("2026-07-28")
    else:
        high_date, low_date = pd.Timestamp("2026-07-02"), pd.Timestamp("2026-07-15")
    frame.loc[frame["trade_date"].eq(high_date), ["open", "high", "low", "close"]] = [99, 100, 98, 99]
    frame.loc[frame["trade_date"].eq(low_date), ["open", "high", "low", "close"]] = [66, 67, 65, 66]
    frame.loc[frame["trade_date"].eq("2026-07-21"), ["open", "high", "low", "close"]] = [79, 81, 78, 80]
    august = frame["trade_date"].between("2026-08-03", "2026-08-06")
    frame.loc[august, "close"] = [90, 94, 98, 102]
    frame.loc[august, "open"] = frame.loc[august, "close"] * 0.998
    frame.loc[august, "high"] = frame.loc[august, "close"] * 1.01
    frame.loc[august, "low"] = frame.loc[august, "close"] * 0.99
    frame["amount"] = frame["close"] * frame["vol"]
    if invalid:
        frame.loc[frame["trade_date"].eq("2026-07-10"), "high"] = 1.0
    return frame


def _config() -> FixedWindowDrawdownConfig:
    return FixedWindowDrawdownConfig(target_date_coverage=0.5)


def test_selects_chronological_drawdown_and_recovery():
    result = screen_fixed_window_drawdown(_frame("600001.SH"), _config())

    assert len(result) == 1
    row = result.iloc[0]
    assert row["july_max_drawdown_pct"] == 35.0
    assert row["drawdown_peak_date"] == pd.Timestamp("2026-07-02")
    assert row["drawdown_trough_date"] == pd.Timestamp("2026-07-15")
    assert row["close"] > row["reference_high_20260721"]
    assert row["ma5"] > row["ma20"]


def test_does_not_treat_low_before_high_as_drawdown():
    result = screen_fixed_window_drawdown(_frame("600002.SH", low_before_high=True), _config())

    assert result.empty


def test_invalid_ohlc_code_is_excluded_and_audited():
    good = _frame("600003.SH")
    bad = _frame("600004.SH", invalid=True)
    result = screen_fixed_window_drawdown(pd.concat([good, bad], ignore_index=True), _config())

    assert set(result["ts_code"]) == {"600003.SH"}
    assert result.attrs["audit"]["invalid_codes_excluded"] == 1


def test_requires_target_close_above_reference_and_ma5_above_ma20():
    frame = _frame("600005.SH")
    august = frame["trade_date"].between("2026-08-03", "2026-08-06")
    frame.loc[august, ["open", "high", "low", "close"]] = [74, 76, 73, 75]
    frame.loc[august, "amount"] = frame.loc[august, "close"] * frame.loc[august, "vol"]

    result = screen_fixed_window_drawdown(frame, _config())

    assert result.empty


def test_reference_high_breakout_requires_cross_today_not_prior_breakout():
    frame = _frame("600006.SH")
    august = frame["trade_date"].between("2026-08-03", "2026-08-06")
    frame.loc[august, "close"] = [75, 78, 80, 85]
    frame.loc[august, "open"] = frame.loc[august, "close"] * 0.998
    frame.loc[august, "high"] = frame.loc[august, "close"] * 1.01
    frame.loc[august, "low"] = frame.loc[august, "close"] * 0.99
    frame.loc[august, "amount"] = frame.loc[august, "close"] * frame.loc[august, "vol"]

    result = screen_reference_high_breakout(frame, _config())

    assert len(result) == 1
    assert bool(result.iloc[0]["first_breakout_today"])
    assert result.iloc[0]["prev_close"] <= result.iloc[0]["reference_high_20260721"]
    assert result.iloc[0]["close"] > result.iloc[0]["reference_high_20260721"]


def test_reference_high_breakout_rejects_stock_already_above_line_yesterday():
    frame = _frame("600007.SH")

    result = screen_reference_high_breakout(frame, _config())

    assert result.empty


def test_reference_close_window_accepts_cross_inside_window_and_requires_hold():
    frame = _frame("600008.SH")
    ref = frame["trade_date"].eq("2026-07-21")
    frame.loc[ref, ["open", "high", "low", "close"]] = [79, 81, 78, 80]
    august = frame["trade_date"].between("2026-08-03", "2026-08-06")
    frame.loc[august, "close"] = [79, 82, 83, 84]
    frame.loc[august, "open"] = frame.loc[august, "close"] * 0.98
    frame.loc[august, "high"] = frame.loc[august, "close"] * 1.01
    frame.loc[august, "low"] = frame.loc[august, "open"] * 0.99
    frame.loc[august, "vol"] = [1_000_000, 2_000_000, 1_000_000, 1_000_000]
    frame.loc[august, "amount"] = frame.loc[august, "close"] * frame.loc[august, "vol"]

    result = screen_reference_close_window_breakout(frame, _config())

    assert len(result) == 1
    assert result.iloc[0]["break_date"] == pd.Timestamp("2026-08-04")
    assert bool(result.iloc[0]["held_above_reference_since_break"])


def test_reference_close_window_rejects_failed_breakout_by_target_date():
    frame = _frame("600009.SH")
    ref = frame["trade_date"].eq("2026-07-21")
    frame.loc[ref, ["open", "high", "low", "close"]] = [79, 81, 78, 80]
    august = frame["trade_date"].between("2026-08-03", "2026-08-06")
    frame.loc[august, "close"] = [79, 82, 81, 78]
    frame.loc[august, "open"] = frame.loc[august, "close"] * 0.98
    frame.loc[august, "high"] = frame.loc[august, "close"] * 1.01
    frame.loc[august, "low"] = frame.loc[august, "open"] * 0.99
    frame.loc[august, "amount"] = frame.loc[august, "close"] * frame.loc[august, "vol"]

    result = screen_reference_close_window_breakout(frame, _config())

    assert result.empty
