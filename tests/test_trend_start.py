import numpy as np
import pandas as pd
import pytest

from trend_start.engine import (
    BUY_COST,
    SELL_COST_BASE,
    StockBars,
    chain,
    chandelier_trigger,
    event_rows,
    execution_flags,
    first_close_below,
    outcomes,
    portfolio,
    sell_cost,
    sell_next_open,
)
from trend_start.signals import StartParams, cross_section_rps, stock_signals


def bars(close, open_=None, high=None, low=None, ma20=None, code="600000.SH", start="2024-01-02"):
    c = np.asarray(close, dtype=float)
    o = c.copy() if open_ is None else np.asarray(open_, dtype=float)
    h = np.maximum(o, c) if high is None else np.asarray(high, dtype=float)
    lo = np.minimum(o, c) if low is None else np.asarray(low, dtype=float)
    m = np.full(len(c), 10.0) if ma20 is None else np.asarray(ma20, dtype=float)
    dates = pd.bdate_range(start, periods=len(c)).to_numpy().astype("datetime64[D]")
    return StockBars(code, np.arange(len(c)), dates, o, h, lo, c, m)


def test_exit_timing_open_vs_close():
    # entry at bar1 open 11; bar3 closes 9.5 < MA20 10
    b = bars([11, 11, 10.2, 9.5, 9.8, 9.9], open_=[11, 11, 10.5, 10, 9.6, 9.8])
    out = outcomes(b)
    assert out.buyable[1]
    assert out.exit_bar["open"][1] == 4 and out.exit_px["open"][1] == pytest.approx(9.6)
    assert out.exit_bar["close"][1] == 3 and out.exit_px["close"][1] == pytest.approx(9.5)
    sc = sell_cost(b.dates[3:4])[0]
    assert out.ret["close"][1] == pytest.approx(9.5 * (1 - sc) / (11 * (1 + BUY_COST)) - 1)


def test_t_plus_one_when_entry_bar_already_below_ma20():
    b = bars([11, 9.5, 9.7, 9.8], open_=[11, 11, 9.6, 9.7])
    out = outcomes(b)
    assert out.exit_bar["close"][1] == 2 and out.exit_px["close"][1] == pytest.approx(9.6)


def test_limit_up_open_is_not_buyable():
    b = bars([10, 11, 11.5], open_=[10, 11, 11.2], ma20=[9, 9, 9])
    assert not outcomes(b).buyable[1]


def test_locked_limit_down_rolls_sale():
    # bar2 close<MA20; bar3 one-price limit-down; bar4 sells at open
    c = [11, 11, 9.9, 8.91, 8.8]
    o = [11, 11, 10.5, 8.91, 8.7]
    b = bars(c, open_=o, high=[11, 11, 10.5, 8.91, 8.9], low=[11, 11, 9.9, 8.91, 8.6])
    out = outcomes(b)
    assert out.exit_bar["open"][1] == 4 and out.exit_px["open"][1] == pytest.approx(8.7)


def test_unfinished_trade_marked_at_last_close():
    b = bars([11, 11, 12, 13], open_=[11, 11, 12, 13])
    out = outcomes(b)
    assert out.unfinished["open"][1] and out.exit_px["open"][1] == 13


def test_two_close_confirmation_and_fixed_horizon():
    c = [11, 11, 9.8, 10.5, 9.7, 9.6, 9.5]
    o = [11, 11, 10.5, 9.9, 10.4, 9.65, 9.55]
    b = bars(c, open_=o)
    out = outcomes(b)
    assert out.exit_bar["open"][1] == 3
    assert out.exit_bar["open2"][1] == 6 and out.exit_px["open2"][1] == pytest.approx(9.55)
    long = bars(np.linspace(11, 13, 30), open_=np.linspace(11, 13, 30))
    fwd = outcomes(long).ret["fwd20"]
    sc = sell_cost(long.dates[20:21])[0]
    assert fwd[1] == pytest.approx(long.c[20] * (1 - sc) / (long.o[1] * (1 + BUY_COST)) - 1)
    assert np.isnan(fwd[15])


def test_stamp_duty_cut():
    d = np.array(["2023-08-25", "2023-08-28"], dtype="datetime64[D]")
    assert sell_cost(d) == pytest.approx([SELL_COST_BASE + 0.001, SELL_COST_BASE + 0.0005])


def test_cross_section_rps_definition():
    rng = np.random.default_rng(1)
    n, m = 6, 300
    close = np.cumprod(1 + rng.normal(0, 0.02, (n, m)), axis=1).astype(np.float32)
    close[5, :100] = np.nan  # too short history for the early sessions
    rps = cross_section_rps(close, periods=(50,))["RPS50"]
    t = 280
    r = close[:5, t] / close[:5, t - 50] - 1
    assert np.isnan(rps[5, t])  # only 180 bars at t
    expected = pd.Series(r).rank(pct=True).to_numpy() * 100
    assert rps[:5, t] == pytest.approx(expected, rel=1e-5)
    assert np.isnan(rps[:, 249]).all()


def synthetic_stock(n=420, seed=3):
    rng = np.random.default_rng(seed)
    c = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    o = c * (1 + rng.normal(0, 0.005, n))
    h = np.maximum(o, c) * 1.01
    lo = np.minimum(o, c) * 0.99
    v = rng.uniform(1e7, 3e7, n)
    return pd.DataFrame({
        "open": o, "high": h, "low": lo, "close": c, "vol": v, "amount": v * c,
        "RPS50": rng.uniform(0, 100, n), "RPS120": rng.uniform(0, 100, n),
        "RPS250": rng.uniform(0, 100, n), "RS": rng.uniform(0, 100, n),
    })


def test_signals_do_not_read_future_bars():
    d = synthetic_stock()
    params = [StartParams(), StartParams(rps_now=85, fresh_below=101, vol_mult=2)]
    base = stock_signals(d, params)
    t = 350
    changed = d.copy()
    changed.loc[t + 1 :, ["open", "high", "low", "close"]] *= 3
    changed.loc[t + 1 :, ["vol", "amount", "RPS120", "RPS250", "RS"]] = 1.0
    alt = stock_signals(changed, params)
    pd.testing.assert_frame_equal(base.iloc[: t + 1], alt.iloc[: t + 1])


def events_frame(rows):
    cols = ["code", "sig_bar", "sig_cal", "entry_cal", "entry_px", "exit_bar_open",
            "exit_cal_open", "exit_px_open", "exit_at_close_open", "unfinished_open", "rps120"]
    return pd.DataFrame(rows, columns=cols)


def test_chain_no_overlap_and_reentry_window():
    ev = events_frame([
        ["A", 10, 10, 11, 1, 20, 20, 1, False, False, 90],
        ["A", 15, 15, 16, 1, 25, 25, 1, False, False, 90],  # overlaps -> dropped
        ["A", 21, 21, 22, 1, 30, 30, 1, False, False, 90],
    ])
    re = events_frame([
        ["A", 40, 40, 41, 1, 50, 50, 1, False, False, 80],  # 10 bars after exit 30 -> kept
        ["B", 40, 40, 41, 1, 50, 50, 1, False, False, 80],  # no prior B trade -> dropped
        ["A", 90, 90, 91, 1, 95, 95, 1, False, False, 80],  # 40 bars after exit 50 -> dropped
    ])
    out = chain(ev, "open", re)
    assert list(zip(out.code, out.sig_bar)) == [("A", 10), ("A", 21), ("A", 40)]


def test_portfolio_slot_cap_and_accounting():
    dates = pd.bdate_range("2024-01-02", periods=10).to_numpy().astype("datetime64[D]")
    close = np.ones((3, 10))
    close[0, 3:] = 2.0
    rows = [
        [c, 0, 0, 1, 1.0, 4, 4, 2.0 if c == "A" else 1.0, False, False, rps]
        for c, rps in (("A", 99), ("B", 98), ("C", 97))
    ]
    curve, trades = portfolio(events_frame(rows), "open", close, {"A": 0, "B": 1, "C": 2},
                              dates, 0, 9, slots=2)
    assert set(trades.code) == {"A", "B"}
    a = trades.set_index("code").loc["A"]
    assert a.pf_ret == pytest.approx(2 * (1 - sell_cost(dates[4:5])[0]) / (1 + BUY_COST) - 1)
    assert curve.iloc[-1] == pytest.approx(1 + 0.5 * a.pf_ret + 0.5 * trades.set_index("code").loc["B"].pf_ret)


def test_market_regimes_equal_weight_index():
    from types import SimpleNamespace

    from run_trend_start_backtest import market_regimes

    close = np.array([[10.0] * 70 + [11.0] * 10, [20.0] * 70 + [np.nan] + [22.0] * 9])
    panel = SimpleNamespace(index_close=np.linspace(100, 120, 80), field=lambda name: close)
    regimes, frame = market_regimes(panel)
    assert frame.EW.iloc[0] == 1.0
    assert frame.EW.iloc[70] == pytest.approx(1.1)  # only the stock with both bars counts
    assert frame.EW.iloc[-1] == pytest.approx(1.1)
    assert regimes["EW>MA60"][70] and not regimes["EW>MA60"][30]
    assert regimes["HS300>MA60"][-1]


def test_ma60_exit_ignores_a_ma20_break():
    c = np.array([12, 12, 12, 10.5, 10.2, 9.5, 9.4], dtype=float)
    b = bars(c, open_=c, ma20=np.full(len(c), 11.0))
    b.ma60 = np.full(len(c), 9.6)
    out = outcomes(b)
    assert out.exit_bar["open"][1] == 4  # 10.5 < MA20, sold next open
    assert out.exit_bar["ma60"][1] == 6  # 9.5 < MA60, one session later
    assert "ma60" not in outcomes(bars(c)).exit_bar


def test_constant_stop_and_chandelier_use_only_the_past():
    c = np.array([10, 10, 10.2, 10.1, 9.1, 9.0], dtype=float)
    h = np.array([10, 10.2, 10.4, 10.3, 9.3, 9.2], dtype=float)
    b = bars(c, open_=c, high=h, low=c * 0.99)
    entry = np.array([1])
    assert first_close_below(b.c, entry, np.array([9.2]))[0] == 4  # 10*0.92 = 9.2
    k, px, _, unf, ret = sell_next_open(b, np.array([4]), execution_flags(b), b.o[entry])
    assert k[0] == 5 and px[0] == 9.0 and not unf[0]
    sc = sell_cost(b.dates[5:6])[0]
    assert ret[0] == pytest.approx(9.0 * (1 - sc) / (10 * (1 + BUY_COST)) - 1)
    atr = np.full(len(c), 0.3)
    assert chandelier_trigger(h, c, atr, entry, mult=3)[0] == 4  # stop = 10.4 - 0.9 = 9.5
    raised = h.copy()
    raised[5] = 30
    assert chandelier_trigger(raised, c, atr, entry, mult=3)[0] == 4


def test_event_rows_skip_unbuyable():
    b = bars([10, 11, 11.5, 12], open_=[10, 11, 11.2, 12], ma20=[9, 9, 9, 9])
    out = outcomes(b)
    sig = np.array([True, True, False, False])
    rows = event_rows(b, out, sig, "X", {"rps120": np.full(4, 90.0)})
    assert [r["sig_bar"] for r in rows] == [1]
