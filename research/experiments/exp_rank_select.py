"""Experiment: candidate ranking vs code-order selection for ice-point entries.

Research only. Baseline: on gate days with >20 candidates the portfolio fills
slots by code order (arbitrary). Variants rank candidates by signals known at
the entry close: liquidity (amount_ma20), momentum (mom20/mom60), distance to
250d high, volatility. Metric: per-gate-day mean fixed5 ret of selected 20.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.panel import load_panel

SNAP = ROOT / "output/daily_research_cache_20260930"
SLOTS = 20
OOS = pd.Timestamp("2022-01-01")


def roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    cs = np.cumsum(np.insert(a, 0, 0.0, axis=1), axis=1)
    out = (cs[:, w:] - cs[:, :-w]) / w
    pad = np.full((a.shape[0], w - 1), np.nan)
    return np.concatenate([pad, out], axis=1)


def main() -> None:
    verify_snapshot(SNAP)
    panel = load_panel(SNAP)
    dates = pd.DatetimeIndex(panel.dates)
    close = panel.field("close").astype(float)
    amount = panel.field("amount").astype(float)
    high = panel.field("high").astype(float)

    feat = {
        "liq20": roll_mean(amount, 20),
        "mom20": close / roll_mean(close, 20) - 1,
        "hi250": close / pd.DataFrame(high).T.rolling(250, min_periods=60).max().T.to_numpy(),
    }
    # 250d rolling max via pandas (axis layout: stocks x days)
    tr = pd.read_csv(ROOT / "output/ice_portfolio_20260930/portfolio_trades_all.csv",
                     encoding="utf-8-sig")
    tr = tr[tr.variant == "fixed5"].copy()
    tr["entry_dt"] = dates[tr.entry_cal.to_numpy()]
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    day_col = {j: j for j in range(len(dates))}
    tr["i"] = tr.code.map(code_row)
    tr["j"] = tr.entry_cal.map(day_col)
    tr = tr.dropna(subset=["i", "j"])
    tr["i"] = tr.i.astype(int); tr["j"] = tr.j.astype(int)
    for name, f in feat.items():
        tr[name] = f[tr.i.to_numpy(), tr.j.to_numpy()]

    days = tr.groupby("entry_cal").size()
    multi = days[days > SLOTS].index
    print(f"gate days total {len(days)}, with >{SLOTS} candidates: {len(multi)}")

    rows = []
    for dcal, g in tr.groupby("entry_cal"):
        rec = {"entry_cal": dcal, "n": len(g), "oos": dates[dcal] >= OOS,
               "all_mean": g.ret.mean()}
        base = g.sort_values("code").head(SLOTS)
        rec["base_code"] = base.ret.mean()
        for name in feat:
            for asc, tag in [(False, "desc"), (True, "asc")]:
                sel = g.sort_values(name, ascending=asc).head(SLOTS)
                rec[f"{name}_{tag}"] = sel.ret.mean()
        rows.append(rec)
    r = pd.DataFrame(rows)

    cols = ["base_code"] + [f"{n}_{t}" for n in feat for t in ("desc", "asc")]
    for label, sub in [("ALL", r), ("IS", r[~r.oos]), ("OOS", r[r.oos])]:
        m = sub[cols].mean() * 100
        w = (sub[cols] > 0).mean() * 100
        print(f"\n[{label}] windows={len(sub)}  (每窗选中20只的均笔收益%, 胜率%)")
        for c in cols:
            print(f"  {c:12s} mean {m[c]:+6.2f}  win {w[c]:5.1f}")
        print(f"  all_cands   mean {sub['all_mean'].mean()*100:+6.2f}")
    r.to_csv(ROOT / "output/exp_rank_select.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
