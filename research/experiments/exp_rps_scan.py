"""Quick evidence scan: does RPS+momentum trend-chasing work on this panel?

Per day, rank all valid stocks by trailing return (RPS120/250), split deciles,
measure fwd5/fwd20 excess vs equal-weight market. IS/OOS split. Pure numpy,
no lookahead: ranking uses closes up to t, forward from t close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.panel import load_panel

SNAP = ROOT / "output/daily_research_cache_20260930"

def main() -> None:
    verify_snapshot(SNAP)
    panel = load_panel(SNAP)
    c = pd.DataFrame(panel.field("close").astype(float)).T.ffill().T.to_numpy()
    amt = panel.field("amount").astype(float)
    n_stk, n_day = c.shape
    dates = pd.DatetimeIndex(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))

    # liquidity floor: 20d avg amount > 30M CNY, listed >= 250d (valid trailing window)
    amt20 = pd.DataFrame(amt).T.rolling(20).mean().T.to_numpy()
    fwd = {h: np.full_like(c, np.nan) for h in (5, 20)}
    for h in fwd:
        fwd[h][:, :-h] = c[:, h:] / c[:, :-h] - 1

    rows = []
    for lb in (120, 250):
        rps = np.full_like(c, np.nan)
        rps[:, lb:] = c[:, lb:] / c[:, :-lb] - 1
        valid = np.isfinite(rps) & np.isfinite(amt20) & (amt20 > 3e7)
        for h in fwd:
            f = fwd[h]
            for tag, sl in (("IS", slice(250, oos0)), ("OOS", slice(oos0, n_day - h - 1))):
                idx = np.arange(n_day)[sl]
                dec_ret = {d: [] for d in range(10)}
                mkt = []
                for j in idx[::2]:            # every other day: speed, kills overlap correlation
                    v = valid[:, j] & np.isfinite(f[:, j])
                    if v.sum() < 500:
                        continue
                    r = rps[v, j]
                    q = pd.qcut(pd.Series(r).rank(method="first"), 10, labels=False)
                    fr = f[v, j]
                    for d in range(10):
                        dec_ret[d].append(np.nanmean(fr[q == d]))
                    mkt.append(np.nanmean(fr))
                for d in range(10):
                    rows.append({"lookback": lb, "fwd": h, "period": tag, "decile": d,
                                 "mean_pct": np.nanmean(dec_ret[d]) * 100,
                                 "excess_pct": (np.nanmean(dec_ret[d]) - np.nanmean(mkt)) * 100})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/exp_rps_scan.csv", index=False, encoding="utf-8-sig")
    for (lb, h, tag), g in out.groupby(["lookback", "fwd", "period"]):
        g = g.sort_values("decile")
        ex = " ".join(f"D{int(r.decile)}:{r.excess_pct:+.2f}" for r in g.itertuples())
        print(f"RPS{lb} fwd{h} {tag}: {ex}")

if __name__ == "__main__":
    main()
