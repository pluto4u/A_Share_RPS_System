"""Offline unit tests for trend_start.pattern_backtest — synthetic bars, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
from trend_start.engine import BUY_COST, sell_cost
from trend_start.pattern_backtest import (
    ExitConfig,
    control_fwd_returns,
    entry_bars,
    portfolio_from_trades,
    simulate_trade,
)


def bars(n: int = 12, start: str = "2024-01-01") -> dict:
    dates = pd.date_range(start, periods=n, freq="B").to_numpy().astype("datetime64[D]")
    cal = np.arange(n)
    c = np.linspace(10, 11, n)
    o = c - 0.02
    h = c + 0.05
    lo = c - 0.05
    return {"dates": dates, "cal": cal, "o": o, "h": h, "lo": lo, "c": c}


def test_entry_bars_extracts_all_kinds() -> None:
    events = pd.DataFrame(
        {"breakout": [True, False, False, False],
         "quiet_touch": [False, True, False, False],
         "confirmed": [False, False, True, False],
         "loud_touch": [False, False, False, True]},
        index=[0, 1, 2, 3],
    )
    assert entry_bars(events) == [(0, "breakout"), (1, "quiet"), (2, "confirmed"), (3, "loud")]


def test_tail_entry_buys_at_signal_close_with_cost() -> None:
    b = bars()
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="tail",
                         pivot=10.0, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=b["c"], cal=b["cal"], dates=b["dates"],
                         exit_cfg=ExitConfig(max_hold=5))
    assert rec is not None
    assert rec["entry_bar"] == 2
    assert rec["entry_px"] == b["c"][2]
    assert rec["exit_reason"] == "time_stop"
    assert rec["exit_bar"] == 2 + 5
    expected = b["c"][7] * (1 - sell_cost(b["dates"])[7]) / (b["c"][2] * (1 + BUY_COST)) - 1
    assert rec["ret"] == expected
    assert rec["fwd2"] == b["c"][4] * (1 - sell_cost(b["dates"])[4]) / (
        b["c"][2] * (1 + BUY_COST)) - 1


def test_next_open_entry_buys_next_session_open() -> None:
    b = bars()
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="next_open",
                         pivot=11.5, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=b["c"], cal=b["cal"], dates=b["dates"],
                         exit_cfg=ExitConfig(max_hold=5))
    assert rec is not None
    assert rec["entry_bar"] == 3
    assert rec["entry_px"] == b["o"][3]


def test_next_open_entry_blocked_by_suspension_gap() -> None:
    b = bars()
    cal = b["cal"].copy()
    cal[3:] += 5  # stock suspended for 5 sessions after bar 2
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="next_open",
                         pivot=11.5, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=b["c"], cal=cal, dates=b["dates"])
    assert rec is None


def test_invalidation_sells_next_open_and_respects_t1() -> None:
    b = bars()
    c = b["c"].copy()
    c[4] = 8.0  # close below floor at bar 4
    b["c"] = c
    b["h"] = np.maximum(b["h"], c)
    b["lo"] = np.minimum(b["lo"], c)
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="tail",
                         pivot=10.0, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=c, cal=b["cal"], dates=b["dates"],
                         exit_cfg=ExitConfig(max_hold=10))
    assert rec is not None
    assert rec["exit_reason"] == "invalidation"
    assert rec["exit_bar"] == 5            # flagged at 4, sold next bar open
    assert rec["exit_px"] == b["o"][5]
    assert not rec["exit_at_close"]


def test_two_closes_below_pivot_band_invalidate() -> None:
    b = bars()
    c = b["c"].copy()
    pivot = c[2] * 1.01
    c[4] = pivot * 0.97   # below pivot*(1-0.02), above floor
    c[5] = pivot * 0.97
    floor = 8.0
    b["c"] = c
    b["h"] = np.maximum(b["h"], c)
    b["lo"] = np.minimum(b["lo"], c)
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="tail",
                         pivot=pivot, floor=floor, o=b["o"], h=b["h"], lo=b["lo"],
                         c=c, cal=b["cal"], dates=b["dates"],
                         exit_cfg=ExitConfig(max_hold=10))
    assert rec is not None
    assert rec["exit_reason"] == "invalidation"
    assert rec["exit_bar"] == 6


def test_limit_up_open_blocks_next_open_entry() -> None:
    b = bars(n=6)
    b["o"][3] = b["c"][2] * 1.10  # limit-up open (10% board)
    b["h"][3] = max(b["h"][3], b["o"][3])
    rec = simulate_trade("600000.SH", sig=2, kind="breakout", timing="next_open",
                         pivot=11.5, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=b["c"], cal=b["cal"], dates=b["dates"])
    assert rec is None


def test_one_price_limit_down_rolls_sale() -> None:
    b = bars(n=8)
    c = b["c"].copy()
    c[4] = 8.0  # invalidation at bar 4
    b["c"] = c
    # bar 5: one-price limit-down, cannot sell
    b["o"][5] = b["h"][5] = b["lo"][5] = c[5] = c[4] * 0.9
    b["h"] = np.maximum(b["h"], c)
    b["lo"] = np.minimum(b["lo"], c)
    rec = simulate_trade("600000.SH", sig=2, kind="quiet", timing="tail",
                         pivot=10.0, floor=9.0, o=b["o"], h=b["h"], lo=b["lo"],
                         c=c, cal=b["cal"], dates=b["dates"],
                         exit_cfg=ExitConfig(max_hold=10))
    assert rec is not None
    assert rec["exit_bar"] == 6            # rolled past the locked bar
    assert rec["exit_px"] == b["o"][6]


def test_control_fwd_returns_matches_manual() -> None:
    dates = pd.date_range("2024-01-01", periods=8, freq="B").to_numpy().astype("datetime64[D]")
    close = np.array([
        [10, 10, 11, 11, 12, 12, 13, 13],
        [20, np.nan, 20, 21, 21, 22, 22, 23],
    ])
    ctl = control_fwd_returns(close, dates, horizons=(2,))
    sc = sell_cost(dates)
    manual0 = np.mean([
        close[0, 2] * (1 - sc[2]) / (close[0, 0] * (1 + BUY_COST)) - 1,
        close[1, 2] * (1 - sc[2]) / (close[1, 0] * (1 + BUY_COST)) - 1,
    ])
    assert abs(ctl[2][0] - manual0) < 1e-12
    manual2 = np.mean([
        close[0, 4] * (1 - sc[4]) / (close[0, 2] * (1 + BUY_COST)) - 1,
        close[1, 4] * (1 - sc[4]) / (close[1, 2] * (1 + BUY_COST)) - 1,
    ])
    assert abs(ctl[2][2] - manual2) < 1e-12
    assert np.isnan(ctl[2][-1])           # no forward bar


def test_portfolio_gate_mult_blocks_and_scales() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="B").to_numpy().astype("datetime64[D]")
    close_ff = np.tile(np.linspace(10, 11, 10), (2, 1))
    code_row = {"600000.SH": 0, "600001.SH": 1}
    trades = pd.DataFrame([
        {"code": "600000.SH", "sig_cal": 1, "entry_cal": 1, "entry_px": close_ff[0, 1],
         "exit_cal": 3, "exit_px": close_ff[0, 3], "exit_at_close": True},
        {"code": "600001.SH", "sig_cal": 2, "entry_cal": 2, "entry_px": close_ff[1, 2],
         "exit_cal": 4, "exit_px": close_ff[1, 4], "exit_at_close": True},
    ])
    curve_all, done_all = portfolio_from_trades(trades, close_ff, code_row, dates, 0, 9)
    curve_g0, done_g0 = portfolio_from_trades(trades, close_ff, code_row, dates, 0, 9,
                                              gate_mult={1: 1.0, 2: 0.0})
    assert len(done_all) == 2
    assert len(done_g0) == 1              # gate mult 0 blocks the second entry
    assert curve_g0.iloc[-1] != curve_all.iloc[-1]
