"""Regime review: environment features vs strategy edge, by year.

For each calendar year: index trend/volume/breadth environment, signal-day
counts (ice / repair / chase), and per-engine per-trade edge of the frozen
F combo. Goal: find which environment features are NECESSARY for the edge
and which regime shifts kill it. Research only; no trade instructions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    ice_ok = gate & ~hot
    amount_surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    repair = ice0.repair.to_numpy(dtype=bool)
    chase = (repair & (amount_surge >= 1.05)
             & (sent.zt_count.to_numpy() >= 20)
             & (sent.max_streak.to_numpy() > 2))

    idx = np.asarray(panel.index_close, dtype=float)
    idx_s = pd.Series(idx, index=dates)
    idx_ma250 = idx_s.rolling(250).mean()
    amt = sent.total_amount.to_numpy()
    amt_s = pd.Series(amt, index=dates)
    amt60 = amt_s.rolling(60).mean()
    years = dates.year.to_numpy()

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")
    col = "entry_cal" if "entry_cal" in dual.columns else "sig_cal"
    tr = {
        "poolice": pool[(pool.channel == "ice")
                        & (pool.variant == "fixed5")][["entry_cal", "ret"]],
        "pattern": dual[(dual.engine == "ice")
                        & (dual.variant == "fixed5")][[col, "ret"]]
        .rename(columns={col: "entry_cal"}),
        "chase": dual[(dual.engine == "chase")
                      & (dual.variant == "fixed3")][[col, "ret"]]
        .rename(columns={col: "entry_cal"}),
    }
    for d in tr.values():
        d["year"] = years[d.entry_cal.to_numpy()]

    rows = []
    for y in sorted(set(years)):
        m = years == y
        if m.sum() < 60:
            continue
        ii = np.flatnonzero(m)
        row = {
            "year": y,
            "idx_ret_pct": round((idx[ii[-1]] / idx[ii[0]] - 1) * 100, 1),
            "pct_above_ma250": round(
                float((idx_s.iloc[ii] > idx_ma250.iloc[ii]).mean() * 100), 0),
            "amt_vs_60d": round(float(np.nanmean(amt[ii] / amt60.iloc[ii])), 2),
            "adv_share_mean": round(
                float(sent.advance_share.to_numpy()[ii].mean()), 2),
            "ice_days": int(ice_ok[ii].sum()),
            "repair_days": int(repair[ii].sum()),
            "chase_days": int(chase[ii].sum()),
        }
        for name, d in tr.items():
            g = d[d.year == y]
            row[f"{name}_n"] = len(g)
            row[f"{name}_bp"] = (round(g.ret.mean() * 1e4, 0)
                                 if len(g) >= 10 else np.nan)
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/final_combo_20261001/regime_yearly.csv",
               index=False, encoding="utf-8-sig")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
