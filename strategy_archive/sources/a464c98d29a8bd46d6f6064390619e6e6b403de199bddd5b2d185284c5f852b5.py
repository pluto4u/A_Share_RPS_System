"""Causal entry signals, evaluated on each stock's own traded bars at close t.

Every window used for a decision at bar t ends at t (or t-1 where noted);
nothing reads t+1. Rolling means are over traded bars, like TDX MA().
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rps_analyzer.right_confirmation_v2 import LayeredConfig, shape_series

LIQ_AMOUNT = 1e8
RPS_MIN_BARS = 251


@dataclass(frozen=True)
class StartParams:
    """Proposed trend-start signal; only these fields are searched in-sample."""

    rps_now: float = 90.0
    fresh_below: float = 80.0  # min RPS120 over prior 20 bars must be below this; >100 disables
    vol_mult: float = 1.5
    new_high_bars: int = 60
    max_bias20: float = 0.15


def cross_section_rps(panel_close: np.ndarray, periods: tuple[int, ...] = (50, 120, 250)) -> dict[str, np.ndarray]:
    """Percentile rank of N-traded-bar return among stocks with >=251 bars that session.

    RPS = average ascending rank / N * 100, same definition as run_daily_rps.
    Also the IBD-style weighted RS (0.4*r63 + 0.2*r126 + 0.2*r189 + 0.2*r252).
    """
    n, m = panel_close.shape
    out = {f"RPS{p}": np.full((n, m), np.nan, dtype=np.float32) for p in periods}
    out["RS"] = np.full((n, m), np.nan, dtype=np.float32)
    for i in range(n):
        cols = np.flatnonzero(~np.isnan(panel_close[i]))
        if len(cols) < RPS_MIN_BARS:
            continue
        c = panel_close[i, cols].astype(np.float64)
        eligible = np.arange(len(c)) >= RPS_MIN_BARS - 1
        for p in periods:
            r = np.full(len(c), np.nan)
            r[p:] = c[p:] / c[:-p] - 1
            r[~eligible] = np.nan
            out[f"RPS{p}"][i, cols] = r
        w = np.full(len(c), np.nan)
        k = 252
        if len(c) > k:
            w[k:] = (
                0.4 * (c[k:] / c[k - 63 : -63] - 1)
                + 0.2 * (c[k:] / c[k - 126 : -126] - 1)
                + 0.2 * (c[k:] / c[k - 189 : -189] - 1)
                + 0.2 * (c[k:] / c[:-k] - 1)
            )
        out["RS"][i, cols] = w
    for key, mat in out.items():
        ranked = pd.DataFrame(mat).rank(axis=0, method="average", pct=True) * 100
        out[key] = ranked.to_numpy(dtype=np.float32)
    return out


def _rolling_close_drawdown(c: np.ndarray, window: int = 20) -> np.ndarray:
    """max(1 - C/cummax(C)) inside each trailing window, as in run_daily_rps."""
    res = np.full(len(c), np.nan)
    if len(c) < window:
        return res
    w = np.lib.stride_tricks.sliding_window_view(c, window)
    res[window - 1 :] = (1 - w / np.maximum.accumulate(w, axis=1)).max(axis=1) * 100
    return res


def stock_signals(d: pd.DataFrame, params: list[StartParams]) -> pd.DataFrame:
    """d: one stock's traded bars with open/high/low/close/vol/amount/RPS50/RPS120/RPS250/RS."""
    c, h, lo, v, amt = d.close, d.high, d.low, d.vol, d.amount
    r120, r250 = d.RPS120, d.RPS250
    bars = pd.Series(np.arange(len(d)) + 1, index=d.index)
    ma = {n: c.rolling(n).mean() for n in (20, 50, 60, 120, 150, 200, 250)}
    liq = amt.rolling(20).mean() >= LIQ_AMOUNT
    out = pd.DataFrame(index=d.index)

    # A_trend: exact run_daily_rps gates (the current "趋势进行中" list).
    w = c.rolling(250)
    delta = c.diff()
    down, up = (delta < 0).astype(float), (delta > 0).astype(float)
    down_mean = (v * down).rolling(20).sum() / down.rolling(20).sum().replace(0, np.nan)
    up_mean = (v * up).rolling(20).sum() / up.rolling(20).sum().replace(0, np.nan)
    dd20 = pd.Series(_rolling_close_drawdown(c.to_numpy()), index=d.index)
    out["A_trend"] = (
        (bars >= RPS_MIN_BARS)
        & (c > ma[20]) & (ma[20] > ma[60]) & (ma[60] > ma[120])
        & (ma[60] > ma[60].shift(5))
        & ((1 - c / w.max()) * 100 <= 25)
        & ((c / w.min() - 1) * 100 >= 30)
        & (dd20 <= 15)
        & (down_mean < up_mean)
        & (r120 >= 87) & (r250 >= 87)
    )

    # A_start: V2 pool + same-day contraction breakout (the current "趋势刚启动").
    cfg = LayeredConfig()
    shapes = shape_series(d, cfg)
    atr = shapes["atr"]
    pool = (
        (bars >= 280)
        & (r120 + r250 > cfg.rps_sum) & (r120 >= cfg.rps120_min) & (r250 >= cfg.rps250_min)
        & ((c > ma[200]).astype(int).rolling(30).sum() >= 20)
        & ((c > ma[250]).astype(int).rolling(30).sum() >= 20)
        & (c > ma[20]) & (ma[20] > ma[60])
        & (ma[20] > ma[20].shift(5)) & (ma[60] > ma[60].shift(5))
        & ((amt > 1e8).astype(int).rolling(10).sum() >= 7)
        & (c / c.rolling(120).max() >= cfg.near_high)
        & (atr > 0) & (c / ma[20] - 1 <= cfg.max_bias20) & (c - ma[20] <= cfg.max_atr_distance * atr)
    )
    out["A_start"] = pool & shapes["breakout"].fillna(False).to_numpy()

    # B: Minervini Trend Template + RS>=70 + tight-base pivot breakout on volume.
    tt = (
        (bars >= 260)
        & (c > ma[150]) & (c > ma[200]) & (ma[150] > ma[200])
        & (ma[200] > ma[200].shift(20))
        & (ma[50] > ma[150]) & (ma[50] > ma[200]) & (c > ma[50])
        & (c >= 1.3 * w.min()) & (c >= 0.75 * w.max())
        & (d.RS >= 70)
    )
    pivot20 = h.shift(1).rolling(20).max()
    depth = pivot20 / lo.shift(1).rolling(20).min() - 1
    vol50 = v.shift(1).rolling(50).mean()
    out["B_minervini"] = tt & (c > pivot20) & (depth <= 0.25) & (v >= 1.5 * vol50) & liq

    # C: Turtle system-2 55-bar Donchian breakout filtered by RPS120>=80.
    out["C_turtle55"] = (bars >= RPS_MIN_BARS) & (c > h.shift(1).rolling(55).max()) & (r120 >= 80) & liq

    # W: Weinstein stage-2 start. 30 weeks = 150 sessions. The cross is the entry;
    # the published exit is a close back through the 30-week average.
    out["W_stage2"] = (
        (bars >= 200) & liq
        & (c > ma[150]) & (c.shift(1) <= ma[150].shift(1))
        & (ma[150] > ma[150].shift(20))
        & (v >= 1.5 * vol50)
        & (r120 >= 70)
        & (c / ma[150] - 1 <= 0.15)
    )

    # D: proposed "RPS fresh start": strength just arrived + new closing high on volume.
    vol20 = v.shift(1).rolling(20).mean()
    prior_min_rps = r120.shift(1).rolling(20).min()
    base_d = (
        (bars >= RPS_MIN_BARS) & liq
        & (c > ma[20]) & (ma[20] > ma[60]) & (ma[20] > ma[20].shift(5))
    )
    for p in params:
        key = f"D_r{p.rps_now:g}_f{p.fresh_below:g}_v{p.vol_mult:g}"
        fresh = prior_min_rps < p.fresh_below if p.fresh_below <= 100 else True
        out[key] = (
            base_d
            & (r120 >= p.rps_now) & fresh
            & (c > c.shift(1).rolling(p.new_high_bars).max())
            & (v >= p.vol_mult * vol20)
            & (c / ma[20] - 1 <= p.max_bias20)
        )

    # Follow-on re-entry after an MA20 exit (used only when a prior trade exists).
    out["REENTRY"] = (
        (bars >= RPS_MIN_BARS) & liq
        & (c > ma[20]) & (c.shift(1) <= ma[20].shift(1))
        & (ma[60] > ma[60].shift(5)) & (r120 >= 80) & (v >= vol20)
    )
    out["ELIGIBLE"] = (bars >= RPS_MIN_BARS) & liq & (c > ma[20])
    out = out.fillna(False).astype(bool)
    out["MA20"] = ma[20]
    out["MA50"] = ma[50]
    out["MA60"] = ma[60]
    out["MA150"] = ma[150]
    out["ATR"] = atr
    out["DON20"] = lo.shift(1).rolling(20).min()
    out["PIV20"] = h.shift(1).rolling(20).max()
    out["PIV55"] = h.shift(1).rolling(55).max()
    out["PIV60"] = c.shift(1).rolling(60).max()
    out["lateness60"] = c / c.shift(1).rolling(60).min() - 1
    out["bias20"] = c / ma[20] - 1
    return out
