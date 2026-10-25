"""Low-winrate/high-payoff exits at portfolio level (research only).

Compares 20-slot equal-weight compounding across the 42 gate days:
  baseline fixed5 vs invalidation (hold until base floor breaks) vs hybrid
  (75% slots fixed5 + 25% invalidation). Uses per-trade ret/hold from the
  robust run; windows rarely overlap so sequential compounding is a good
  approximation (overlap checked explicitly).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT

SLOTS = 20
t = pd.read_csv(ROOT / "output/ice_optimize_robust_20260930/exit_trades.csv",
                encoding="utf-8-sig")
piv = t.pivot_table(index=["code", "sig_cal", "period"], columns="variant",
                    values=["ret", "hold"], aggfunc="first")
piv.columns = [f"{a}_{b}" for a, b in piv.columns]
piv = piv.reset_index()

# window overlap check for invalidation holds
days = np.sort(piv.sig_cal.unique())
inv = piv[["sig_cal", "hold_invalidation"]].groupby("sig_cal").max()
overlaps = sum(1 for a, b in zip(days[:-1], days[1:]) if b - a < inv.loc[a, "hold_invalidation"])
print(f"gate days: {len(days)}, 相邻窗口间隔<上窗最长持有: {overlaps} 次")

def compound(df: pd.DataFrame, ret_col: str, frac: float = 1.0) -> tuple[float, pd.Series]:
    eq = 1.0
    curve = {}
    for d, g in df.groupby("sig_cal"):
        sel = g.sort_values("code").head(SLOTS)
        n = len(sel)
        w = frac * min(n, SLOTS) / SLOTS          # 投入资金占比
        eq *= 1 + w * sel[ret_col].mean()
        curve[d] = eq
    return eq - 1, pd.Series(curve)

res = {}
for period in ["IS", "OOS", "ALL"]:
    sub = piv if period == "ALL" else piv[piv.period == period]
    out = {}
    for name, col in [("fixed5", "ret_fixed5"), ("invalidation", "ret_invalidation")]:
        r, c = compound(sub.dropna(subset=[col]), col)
        out[name] = r
    # hybrid: per day, 15 slots fixed5 + 5 slots invalidation (code-order split)
    sub2 = sub.dropna(subset=["ret_fixed5", "ret_invalidation"])
    eq = 1.0
    for d, g in sub2.groupby("sig_cal"):
        sel = g.sort_values("code").head(SLOTS)
        n = len(sel)
        w = min(n, SLOTS) / SLOTS
        mix = 0.75 * sel.ret_fixed5.mean() + 0.25 * sel.ret_invalidation.mean()
        eq *= 1 + w * mix
    out["hybrid_75_25"] = eq - 1
    res[period] = out
    print(f"\n[{period}] windows={sub.sig_cal.nunique()}")
    for k, v in out.items():
        print(f"  {k:16s} 组合累计 {v*100:+8.2f}%")

inv_tr = t[t.variant == "invalidation"]
print("\n=== invalidation 逐笔分布 ===")
print("exit_reason:", dict(inv_tr.exit_reason.value_counts()))
print("hold: 中位", inv_tr.hold.median(), " p90", inv_tr.hold.quantile(0.9), " max", inv_tr.hold.max())
qs = inv_tr.ret.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
print("ret 分位:", {f"p{int(k*100)}": round(v, 4) for k, v in qs.items()})
top = inv_tr.nlargest(max(1, int(len(inv_tr)*0.05)), "ret")
print(f"top5%({len(top)}笔)贡献: 均值合计 {top.ret.sum():+.3f} vs 全部合计 {inv_tr.ret.sum():+.3f}")
