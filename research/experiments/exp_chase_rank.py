"""Chase-engine candidate ranking experiment (research only).

Chase days avg 68 candidates vs 20 slots: selection actually binds here.
Rank by signals known at entry close: liq20, mom20, mom60, hi250 proximity,
breakout extension (entry_px/pivot). Metric: per-day top-20 mean ret + slot
compounding, IS/OOS, exits fixed3 and fixed5.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

SNAP = ROOT / "output/daily_research_cache_20260930"
SLOTS = 20


def roll_mean(a, w):
    cs = np.cumsum(np.insert(a, 0, 0.0, axis=1), axis=1)
    return np.concatenate([np.full((a.shape[0], w - 1), np.nan),
                           (cs[:, w:] - cs[:, :-w]) / w], axis=1)


def main() -> None:
    verify_snapshot(SNAP)
    panel = load_panel(SNAP)
    dates = pd.DatetimeIndex(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))
    close = pd.DataFrame(panel.field("close").astype(float)).T.ffill().T.to_numpy()
    amount = panel.field("amount").astype(float)
    high = pd.DataFrame(panel.field("high").astype(float)).T.ffill().T.to_numpy()

    feats = {
        "liq20": roll_mean(amount, 20),
        "mom20": close / roll_mean(close, 20) - 1,
        "mom60": close / roll_mean(close, 60) - 1,
        "hi250": close / pd.DataFrame(high).T.rolling(250, min_periods=60).max().T.to_numpy(),
    }
    t = pd.read_csv(ROOT / "output/dual_engine_20261001/dual_trades.csv",
                    encoding="utf-8-sig")
    t = t[t.engine == "chase"].copy()
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    t["i"] = t.code.map(code_row)
    for name, f in feats.items():
        t[name] = f[t.i.to_numpy(), t.entry_cal.to_numpy()]

    rankers = ["code"] + [f"{n}_{d}" for n in feats for d in ("desc", "asc")]
    rows = []
    for variant in ("fixed3", "fixed5"):
        v = t[t.variant == variant]
        for period, (lo_d, hi_d) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9)}.items():
            sub = v[(v.entry_cal >= lo_d) & (v.entry_cal <= hi_d)]
            for rk in rankers:
                picked = []
                for d, g in sub.groupby("entry_cal"):
                    if rk == "code":
                        sel = g.sort_values("code")
                    else:
                        n, dr = rk.rsplit("_", 1)
                        sel = g.sort_values(n, ascending=(dr == "asc"))
                    picked.append(sel.head(SLOTS))
                p = pd.concat(picked)
                r = slot_compound(p.rename(columns={"entry_cal": "sig_cal"})
                                  [["sig_cal", "code", "ret", "hold"]], slots=SLOTS)
                rows.append({"variant": variant, "period": period, "rank": rk,
                             "mean_ret_pct": p.ret.mean() * 100,
                             "win_pct": (p.ret > 0).mean() * 100,
                             "pf_ret_pct": r.total_ret * 100, "filled": r.filled})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/exp_chase_rank.csv", index=False, encoding="utf-8-sig")
    piv = out.pivot_table(index="rank", columns=["variant", "period"],
                          values="pf_ret_pct")
    print(piv.round(2).to_string())
    print()
    piv2 = out.pivot_table(index="rank", columns=["variant", "period"],
                           values="mean_ret_pct")
    print(piv2.round(2).to_string())


if __name__ == "__main__":
    main()
