"""Baseline check: is the pullback rule's +4.2%/8w actually an edge?

Computes, over the same window (entry weeks 2024-10-01..2026-08-31):
  U0: unconditional fwd8w for every stock-week (all weeks, all stocks)
  U1: fwd8w conditional on env (CSI300 weekly >= its MA20w) only
  U2: fwd8w conditional on RPS120>=90 only (leader filter, no pullback)
  U3: fwd8w conditional on RPS120>=90 + weekly low touched MA20w zone
      (the G5-style pullback without volume filter)
Entry px = next trading day open, exit = close 8 weeks later. Causal.
Statistics only.
"""
from __future__ import annotations

import numpy as np
from research.experiments.exp_pullback import T0, T1, build_ctx


def main() -> None:
    ctx = build_ctx()
    wc, wl, r = ctx["wc"], ctx["wl"], ctx["r120w"]
    ma10, ma20, hi52 = ctx["ma10"], ctx["ma20"], ctx["hi52"]
    nw, m = ctx["nw"], ctx["m"]

    rets = {k: [] for k in ("U0_all", "U1_env", "U2_rps90", "U3_pullback")}
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
        ok = np.isfinite(f8) & ~ctx["excl"]
        rets["U0_all"].append(f8[ok])
        if ctx["env_ok"][wi]:
            rets["U1_env"].append(f8[ok])
        rps_ok = ok & (r[:, wi] >= 90)
        rets["U2_rps90"].append(f8[rps_ok])
        pb = (rps_ok & (wl[:, wi] <= ma20[:, wi] * 1.03)
              & (wc[:, wi] >= ma20[:, wi] * 0.97)
              & (wc[:, wi] > ma20[:, wi]) & (ma10[:, wi] > ma20[:, wi])
              & (wc[:, wi] / hi52[:, wi] >= 0.70))
        rets["U3_pullback"].append(f8[pb])

    print(f"{'口径':14s} {'n':>7s} {'mean8w':>8s} {'win':>6s} {'med8w':>8s}")
    for k, chunks in rets.items():
        g = np.concatenate([c for c in chunks if len(c)])
        print(f"{k:14s} {len(g):7d} {np.nanmean(g) * 100:+7.2f}% "
              f"{(g > 0).mean() * 100:5.1f}% {np.nanmedian(g) * 100:+7.2f}%")


if __name__ == "__main__":
    main()
