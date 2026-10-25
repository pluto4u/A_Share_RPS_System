"""Cross-regime + parameter-perturbation proof for the pullback rule.

The user's question: what would make the positive expectation a
"certainty with probability"? Answer: (1) the edge must appear in EVERY
distinct market regime, not just the window that inspired the rule;
(2) it must survive parameter perturbation (else overfit);
(3) significance must be deflated for the variants we tried.

Eras (causal, weekly, next-day-open entry, 8w hold, net of costs):
  A 2017-01..2018-12  slow-bull into bear
  B 2019-01..2021-12  structural bull
  C 2022-01..2024-09  weak/bear chop
  D 2024-10..2026-08  current cycle (the contaminated in-sample window)

Part 1: canonical params per era, edge vs era's own U0 baseline.
Part 2: 27-combo perturbation grid on eras B and D; report sign
stability. Statistics only, no buy/sell advice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from research.experiments.exp_pullback import st_exclusions
from research.experiments.exp_weekly_rps import pct_rank_col, rolling_mean
from run_pattern_regime_backtest import ROOT
from trend_start.engine import BUY_COST, sell_cost

ERAS = {"A_17-18慢牛转熊": ("2017-01-01", "2018-12-31"),
        "B_19-21结构牛": ("2019-01-01", "2021-12-31"),
        "C_22-24弱势": ("2022-01-01", "2024-09-30"),
        "D_24-26本轮": ("2024-10-01", "2026-08-31")}

GRID_RPS = (85, 90, 95)
GRID_TOUCH = (1.02, 1.03, 1.05)
GRID_GATE = (1.0, 1.5, 2.0)


def load_weekly():
    z = np.load(ROOT / "output/daily_research_cache_20260930/panel.npz",
                mmap_mode="r")
    dates = pd.DatetimeIndex(z["dates"])
    keep = dates >= pd.Timestamp("2015-09-01")
    dates = dates[keep]
    close = np.asarray(z["close"][:, keep], dtype=np.float32)
    open_ = np.asarray(z["open"][:, keep], dtype=np.float32)
    high = np.asarray(z["high"][:, keep], dtype=np.float32)
    low = np.asarray(z["low"][:, keep], dtype=np.float32)
    codes = np.array([str(c) for c in z["codes"]])
    n, m = close.shape
    print(f"[PANEL] {n} x {m}, {dates[0].date()}..{dates[-1].date()}")

    wk = dates.to_period("W-FRI")
    wk_uniq = wk.unique().sort_values()
    pos = {p: i for i, p in enumerate(wk_uniq)}
    wid = np.array([pos[p] for p in wk])
    nw = len(wk_uniq)
    last_day = np.full(nw, -1)
    for j, w in enumerate(wid):
        last_day[w] = j
    wdates = dates[last_day]
    wc = close[:, last_day]
    wl = np.full((n, nw), np.inf, dtype=np.float32)
    np.minimum.at(wl, (slice(None), wid), np.where(np.isfinite(low), low,
                                                   np.inf))
    wl[np.isinf(wl)] = np.nan
    ma10 = rolling_mean(wc, 10)
    ma20 = rolling_mean(wc, 20)
    hi52 = np.full_like(wc, np.nan)
    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        hi52[ok, w] = win[ok].max(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0

    excl = st_exclusions(codes, close, high, low)
    close_x = close.copy()
    close_x[excl] = np.nan
    r120w = np.full((n, nw), np.nan)
    for w, j in enumerate(last_day):
        if j >= 120:
            with np.errstate(divide="ignore", invalid="ignore"):
                r120w[:, w] = pct_rank_col(close_x[:, j]
                                           / close_x[:, j - 120] - 1)
    sc = sell_cost(dates)
    return dict(wc=wc, wl=wl, ma10=ma10, ma20=ma20, hi52=hi52, ret52=ret52,
                r120w=r120w, last_day=last_day, wdates=wdates, dates=dates,
                close=close, open_=open_, excl=excl, sc=sc, nw=nw, m=m)


def era_returns(ctx, t0, t1, rps_min, touch, gate):
    """Weekly pool net means + U0 baseline net means for one era."""
    pool_means, u0_means = [], []
    for wi in range(ctx["nw"] - 9):
        wd = ctx["wdates"][wi]
        if wd < pd.Timestamp(t0) or wd > pd.Timestamp(t1):
            continue
        j = ctx["last_day"][wi]
        if j + 1 >= ctx["m"]:
            continue
        x_j = ctx["last_day"][wi + 8]
        e = ctx["open_"][:, j + 1]
        x = ctx["close"][:, x_j]
        with np.errstate(divide="ignore", invalid="ignore"):
            net = x * (1 - ctx["sc"][x_j]) / (e * (1 + BUY_COST)) - 1
        ok = np.isfinite(net) & ~ctx["excl"]
        with np.errstate(divide="ignore", invalid="ignore"):
            pool = ((ctx["r120w"][:, wi] >= rps_min)
                    & (ctx["wc"][:, wi] > ctx["ma20"][:, wi])
                    & (ctx["ma10"][:, wi] > ctx["ma20"][:, wi])
                    & (ctx["wl"][:, wi] <= ctx["ma20"][:, wi] * touch)
                    & (ctx["wc"][:, wi] >= ctx["ma20"][:, wi] * 0.97)
                    & (ctx["wc"][:, wi] / ctx["hi52"][:, wi] >= 0.70)
                    & (ctx["ret52"][:, wi] <= gate))
        pool = np.isfinite(pool.astype(float)) & pool & ok
        if ok.sum() > 100 and pool.sum() >= 5:
            u0_means.append(float(np.mean(net[ok])))
            pool_means.append(float(np.mean(net[pool])))
    return np.array(pool_means), np.array(u0_means)


def report(tag, pool, u0):
    if len(pool) < 5:
        print(f"{tag}: 信号周不足 ({len(pool)})")
        return
    edge = pool.mean() - u0.mean()
    sub = pool[::8] - u0[::8]
    t_sub = (sub.mean() / (sub.std() / np.sqrt(len(sub)))
             if len(sub) > 3 and sub.std() > 0 else float("nan"))
    print(f"{tag}: 周数{len(pool):3d}  池{pool.mean() * 100:+6.2f}%  "
          f"U0{u0.mean() * 100:+6.2f}%  edge{edge * 100:+5.2f}pp  "
          f"edge中位{np.median(pool - u0) * 100:+5.2f}pp  "
          f"edge为正周占比{(pool > u0).mean() * 100:.0f}%  "
          f"非重叠t={t_sub:.2f}")


def main() -> None:
    ctx = load_weekly()
    print("\n== Part 1: 规范参数跨环境 ==")
    for name, (t0, t1) in ERAS.items():
        pool, u0 = era_returns(ctx, t0, t1, 90, 1.03, 1.5)
        report(name, pool, u0)

    print("\n== Part 2: 参数扰动 (B + D 两个环境, 27 组) ==")
    for era_name in ("B_19-21结构牛", "D_24-26本轮"):
        t0, t1 = ERAS[era_name]
        edges = []
        for rps in GRID_RPS:
            for touch in GRID_TOUCH:
                for gate in GRID_GATE:
                    pool, u0 = era_returns(ctx, t0, t1, rps, touch, gate)
                    if len(pool) >= 5:
                        edges.append(pool.mean() - u0.mean())
        edges = np.array(edges) * 100
        print(f"{era_name}: 有效{len(edges)}/27组  "
              f"edge>0 占比 {(edges > 0).mean() * 100:.0f}%  "
              f"edge 分布 p10 {np.percentile(edges, 10):+.2f}pp  "
              f"中位 {np.median(edges):+.2f}pp  "
              f"p90 {np.percentile(edges, 90):+.2f}pp")


if __name__ == "__main__":
    main()
