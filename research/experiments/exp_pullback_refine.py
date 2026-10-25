"""Refinement test: liquidity gate and sector-cluster filter on U3+early.

The first forward list (2026-09-30 week) was dominated by illiquid
small-caps with young trends — visually unlike the winner charts
(liquid main-line leaders). Test two candidate filters on the
U3+early-stage universe (fwd8w, next-day open entry, causal):

  L: liquidity  amt20 >= threshold (3e8 / 5e8 / 1e9)
  C: sector cluster  >=N pool members share the same TDX level-2
     industry in the same week (main-line resonance)

Statistics only; window is contaminated history (rule-shaping, not proof).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from research.experiments.exp_pullback import T0, T1, build_ctx
from research.experiments.exp_sector_crowding import load_industry
from run_pattern_regime_backtest import ROOT


def main() -> None:
    ctx = build_ctx()
    wc, wl, r = ctx["wc"], ctx["wl"], ctx["r120w"]
    ma10, ma20, hi52 = ctx["ma10"], ctx["ma20"], ctx["hi52"]
    nw, m, codes = ctx["nw"], ctx["m"], ctx["codes"]

    z = np.load(ROOT / "output/daily_research_cache_20260930/panel.npz",
                mmap_mode="r")
    full_dates = pd.DatetimeIndex(z["dates"])
    keep = full_dates >= pd.Timestamp("2022-06-01")
    amount = np.asarray(z["amount"][:, keep], dtype=np.float32)
    amt20w = np.full((len(codes), nw), np.nan)
    for w, j in enumerate(ctx["last_day"]):
        if j >= 19:
            amt20w[:, w] = np.nanmean(amount[:, j - 19:j + 1], axis=1)

    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0

    code2hy, _ = load_industry()
    hy = np.array([code2hy.get(c.split(".")[0], "") for c in codes])

    # U3 + early-stage base mask per week
    base = ((r >= 90) & (wc > ma20) & (ma10 > ma20)
            & (wl <= ma20 * 1.03) & (wc >= ma20 * 0.97)
            & (wc / hi52 >= 0.70) & (ret52 <= 1.5) & ~ctx["excl"][:, None])
    base = np.isfinite(base.astype(float)) & base

    buckets: dict[str, list[float]] = {}

    def add(tag: str, vals: np.ndarray) -> None:
        buckets.setdefault(tag, []).append(vals)

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
        pool = base[:, wi] & np.isfinite(f8)
        if pool.sum() == 0:
            continue
        add("全部池", f8[pool])
        for thr, tag in ((3e8, "amt≥3亿"), (5e8, "amt≥5亿"), (1e9, "amt≥10亿")):
            m_liq = pool & (amt20w[:, wi] >= thr)
            add(tag, f8[m_liq])
            add(tag.replace("≥", "<"), f8[pool & ~(amt20w[:, wi] >= thr)])
        # sector cluster: industry counts inside this week's pool
        pool_idx = np.where(pool)[0]
        if len(pool_idx):
            hy_pool = hy[pool_idx]
            uniq, cnts = np.unique(hy_pool, return_counts=True)
            cnt_map = dict(zip(uniq.tolist(), cnts.tolist(), strict=True))
            same = np.array([cnt_map.get(h, 0) for h in hy_pool])
            g = f8[pool_idx]
            add("板块≥3只同池", g[same >= 3])
            add("板块≤2只同池", g[same <= 2])

    print(f"{'口径':14s} {'n':>6s} {'mean8w':>8s} {'win':>6s} {'med8w':>8s}")
    for k, chunks in buckets.items():
        g = np.concatenate([c for c in chunks if len(c)])
        if len(g) < 30:
            print(f"{k:14s} {len(g):6d}  (过少)")
            continue
        print(f"{k:14s} {len(g):6d} {np.nanmean(g) * 100:+7.2f}% "
              f"{(g > 0).mean() * 100:5.1f}% {np.nanmedian(g) * 100:+7.2f}%")


if __name__ == "__main__":
    main()
