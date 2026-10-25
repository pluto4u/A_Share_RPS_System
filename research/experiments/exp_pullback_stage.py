"""Split U3 pullback trades by prior extension (early vs late stage).

Hypothesis from the four named stocks: early/mid-trend pullbacks pay,
late-stage ones (stock already +150% over 52 weeks, or trend older than
40 weeks) fail (e.g. 中际旭创 2026-07-03 -> -24.5%/8w).

U3 base: RPS120>=90 (>=3 of last 4w), close>MA20w, MA10w>MA20w,
weekly low <= MA20w*1.03, close >= MA20w*0.97, close/52w-high >= 0.70.
Split A: 52-week return of the stock <= +150% vs > +150%.
Split B: weeks since close first crossed above MA20w <= 40 vs > 40.
Entry next-day open, exit close 8w later. Causal, statistics only.
"""
from __future__ import annotations

import numpy as np
from research.experiments.exp_pullback import T0, T1, build_ctx


def main() -> None:
    ctx = build_ctx()
    wc, wl, r = ctx["wc"], ctx["wl"], ctx["r120w"]
    ma10, ma20, hi52 = ctx["ma10"], ctx["ma20"], ctx["hi52"]
    nw, m = ctx["nw"], ctx["m"]

    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0
    # weeks since first close above MA20 (run length of current uptrend)
    above = np.isfinite(ma20) & (wc > ma20)
    runlen = np.zeros_like(wc, dtype=float)
    for w in range(1, nw):
        runlen[:, w] = np.where(above[:, w], runlen[:, w - 1] + 1, 0)

    buckets = {"早中期(ret52<=1.5)": [], "晚期(ret52>1.5)": [],
               "趋势<=40周": [], "趋势>40周": []}
    for wi in range(nw - 9):
        wd = ctx["wdates"][wi]
        if wd < T0 or wd > T1:
            continue
        j = ctx["last_day"][wi]
        if j + 1 >= m:
            continue
        e = ctx["open_"][:, j + 1]
        x = ctx["close"][:, ctx["last_day"][wi + 8]]
        with np.errstate(divide="ignore", invalid="ignore"):
            f8 = x / e - 1
        hi = (r[:, wi] >= 90)
        pb = (hi & (wl[:, wi] <= ma20[:, wi] * 1.03)
              & (wc[:, wi] >= ma20[:, wi] * 0.97)
              & (wc[:, wi] > ma20[:, wi]) & (ma10[:, wi] > ma20[:, wi])
              & (wc[:, wi] / hi52[:, wi] >= 0.70) & np.isfinite(f8)
              & ~ctx["excl"])
        buckets["早中期(ret52<=1.5)"].append(f8[pb & (ret52[:, wi] <= 1.5)])
        buckets["晚期(ret52>1.5)"].append(f8[pb & (ret52[:, wi] > 1.5)])
        buckets["趋势<=40周"].append(f8[pb & (runlen[:, wi] <= 40)])
        buckets["趋势>40周"].append(f8[pb & (runlen[:, wi] > 40)])

    print(f"{'分层':22s} {'n':>6s} {'mean8w':>8s} {'win':>6s} {'med8w':>8s}")
    for k, chunks in buckets.items():
        g = np.concatenate([c for c in chunks if len(c)])
        print(f"{k:22s} {len(g):6d} {np.nanmean(g) * 100:+7.2f}% "
              f"{(g > 0).mean() * 100:5.1f}% {np.nanmedian(g) * 100:+7.2f}%")


if __name__ == "__main__":
    main()
