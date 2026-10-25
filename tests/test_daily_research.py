from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from trend_start.panel import Panel
from trend_start.research import StudyConfig, linked_retest, metrics, simulate, validate_panel


def panel(close: list[list[float]], opens: list[list[float]] | None = None) -> Panel:
    c = np.array(close, dtype=float)
    o = c.copy() if opens is None else np.array(opens, dtype=float)
    n, m = c.shape
    data = {
        "close": c,
        "open": o,
        "high": np.maximum(c, o),
        "low": np.minimum(c, o),
        "vol": np.where(np.isnan(c), np.nan, 1000),
        "amount": np.where(np.isnan(c), np.nan, 10000),
    }
    return Panel(
        pd.bdate_range("2024-01-02", periods=m).to_numpy().astype("datetime64[D]"),
        np.array([f"600{i:03d}.SH" for i in range(n)]),
        data,
        np.full(m, 100.0),
    )


def run(p: Panel, sig: np.ndarray, **kw: object):  # type: ignore[no-untyped-def]
    cfg = StudyConfig(
        capital=1000, slots=1, commission=0, slippage=0, main_limit=0.1, max_entry_gap=0.2, **kw
    )
    return simulate(
        p, sig, np.full(sig.shape, 90.0), np.full(sig.shape, 9.0), cfg, 0, len(p.dates) - 1
    )


def test_cash_value_and_mark_not_fictional_liquidation():
    p = panel([[10, 10, 10.2, 11]])
    sig = np.array([[True, False, False, False]])
    result = run(p, sig)
    assert result.daily.equity.iloc[-1] == pytest.approx(1100)
    assert result.trades.empty
    assert result.open_positions.market_value.iloc[0] == pytest.approx(1100)
    np.testing.assert_allclose(result.daily.equity, result.daily.cash + result.daily.market_value)
    assert metrics(result, 1000)["total_return_pct"] == pytest.approx(10)


def test_close_signal_is_next_open_and_t_plus_one():
    p = panel([[10, 9.2, 9.0, 9.0]], [[10, 10, 9.5, 9.0]])
    result = run(p, np.array([[True, False, False, False]]))
    assert result.trades.entry_date.iloc[0] == str(p.dates[1])
    assert result.trades.exit_date.iloc[0] == str(p.dates[2])
    assert result.trades.exit_price_adjusted.iloc[0] == pytest.approx(9.5)
    assert result.daily.equity.iloc[-1] == pytest.approx(950 * (1 - 0.0005))


def test_down_open_is_not_optimistically_filled_at_close():
    p = panel([[10, 9.2, 9.1, 9.2]], [[10, 10, 8.28, 9.1]])
    result = run(p, np.array([[True, False, False, False]]))
    assert result.trades.exit_date.iloc[0] == str(p.dates[3])
    assert (result.orders.status == "deferred").sum() == 1


def test_suspended_entry_cancelled_and_exit_remains_pending():
    p = panel([[10, np.nan, 10, 9.2, np.nan, 9.1]], [[10, np.nan, 10, 10, np.nan, 9.1]])
    sig = np.array([[True, False, True, False, False, False]])
    result = run(p, sig)
    assert len(result.trades) == 1
    assert result.trades.entry_date.iloc[0] == str(p.dates[3])
    assert result.trades.exit_date.iloc[0] == str(p.dates[5])
    assert "no_next_session_bar" in result.orders.reason.to_list()
    assert "no_daily_bar" in result.orders.reason.to_list()


def test_slot_selection_uses_yesterday_strength():
    p = panel([[10, 10, 10], [10, 10, 10]])
    sig = np.array([[True, False, False], [True, False, False]])
    strength = np.array([[90, 100, 100], [99, 1, 1]], dtype=float)
    cfg = StudyConfig(capital=1000, slots=1, commission=0, slippage=0)
    result = simulate(p, sig, strength, np.full(sig.shape, 9), cfg, 0, 2)
    assert result.open_positions.code.to_list() == ["600001.SH"]


def test_prefix_execution_invariant_to_future_prices():
    p = panel([[10, 10, 10.2, 10.4, 10.5, 10.6]])
    sig = np.array([[True, False, False, False, False, False]])
    base = run(p, sig)
    for f in ("open", "high", "low", "close"):
        p.data[f][:, 4:] *= 5
    altered = run(p, sig)
    pd.testing.assert_frame_equal(base.daily.iloc[:4], altered.daily.iloc[:4])


def test_signal_on_last_day_does_not_fill():
    p = panel([[10, 10, 10]])
    result = run(p, np.array([[False, False, True]]))
    assert result.open_positions.empty and result.trades.empty
    assert result.daily.equity.iloc[-1] == 1000


def test_invalid_data_fails_explicitly():
    p = panel([[10, 11]])
    validate_panel(p)
    p.data["low"][0, 1] = 12
    with pytest.raises(ValueError, match="OHLC"):
        validate_panel(p)
    with pytest.raises(ValidationError):
        StudyConfig(slots=0)


def retest_fixture() -> tuple[pd.DataFrame, np.ndarray]:
    c = np.r_[np.full(20, 10.0), 10.6, 10.5, 10.4, 10.7, 10.9]
    h = c + 0.1
    lo = c - 0.1
    lo[22] = 10.11  # quiet touch of the frozen 10.1 pivot
    vol = np.full(len(c), 100.0)
    vol[20] = 300
    d = pd.DataFrame({"close": c, "high": h, "low": lo, "vol": vol, "RPS120": 90.0})
    breakout = np.zeros(len(c), dtype=bool)
    breakout[20] = True
    return d, breakout


def test_event_frozen_pivot_later_touch_later_confirm():
    d, breakout = retest_fixture()
    result = linked_retest(d, breakout, StudyConfig())
    assert result.state.iloc[20] == "breakout"
    assert result.state.iloc[22] == "touched"
    assert result.state.iloc[23] == "confirmed"
    assert result.event_bar.iloc[23] == 20
    assert result.frozen_pivot.iloc[23] == pytest.approx(10.1)
    assert result.confirmed.sum() == 1


def test_retest_failure_and_no_future_leak():
    d, breakout = retest_fixture()
    before = linked_retest(d, breakout, StudyConfig())
    d.loc[23:, ["close", "high", "low"]] *= 2
    after = linked_retest(d, breakout, StudyConfig())
    pd.testing.assert_frame_equal(before.iloc[:23], after.iloc[:23])
    d.loc[22, "close"] = 9.0
    failed = linked_retest(d, breakout, StudyConfig())
    assert failed.state.iloc[22] == "invalid"
    assert not failed.confirmed.any()
