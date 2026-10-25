"""Slot-aware compounding for low-winrate exits (research only).

Cash accounting with 20 slots: entry at sig day close, slot locked until
entry+hold (trading days). Open positions marked at cost (conservative for
sizing). When a gate day fires, only free slots can be filled.
"""
from __future__ import annotations

import heapq
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


def simulate(df: pd.DataFrame, pick, ret_col: str, hold_col: str,
             label: str) -> dict:
    """pick(g, free) -> selected frame of <=free rows."""
    cash, locked = 1.0, 0.0
    open_pos: list[tuple[int, float, float]] = []  # (exit_cal, cost, ret)
    n_filled, n_skipped = 0, 0
    last_cal = int(df.sig_cal.max())
    for d in range(int(df.sig_cal.min()), last_cal + 1):
        while open_pos and open_pos[0][0] <= d:      # exits first
            _, cost, ret = heapq.heappop(open_pos)
            cash += cost * (1 + ret)
            locked -= cost
        g = df[df.sig_cal == d]
        if len(g):
            equity = cash + locked
            free = SLOTS - len(open_pos)
            sel = pick(g.sort_values("code"), free)
            n_filled += len(sel)
            n_skipped += max(0, min(len(g), SLOTS) - len(sel))
            for r in sel.itertuples():
                cost = equity / SLOTS
                if cost > cash:
                    cost = cash
                cash -= cost
                locked += cost
                ret = getattr(r, ret_col)
                hold = int(getattr(r, hold_col))
                heapq.heappush(open_pos, (min(d + hold, last_cal + 10_000), cost, ret))
    # close remaining at last known ret (marked via their realized ret)
    final = cash + sum(c * (1 + r) for _, c, r in open_pos)
    unfinished = len(open_pos)
    return {"label": label, "total_ret_pct": (final - 1) * 100,
            "filled": n_filled, "skipped_locked": n_skipped,
            "open_at_end": unfinished}


def pick_fixed5(g: pd.DataFrame, free: int) -> pd.DataFrame:
    return g.dropna(subset=["ret_fixed5"]).head(free)


def pick_inv(g: pd.DataFrame, free: int) -> pd.DataFrame:
    return g.dropna(subset=["ret_invalidation"]).head(free)


def make_pick_hybrid(w_inv: float):
    def pick(g: pd.DataFrame, free: int) -> pd.DataFrame:
        g = g.dropna(subset=["ret_fixed5", "ret_invalidation"])
        n_inv = int(round(free * w_inv))
        sel_inv = g.head(n_inv).copy()
        sel_inv["_use"] = "inv"
        sel_f5 = g.iloc[n_inv:free].copy()
        sel_f5["_use"] = "f5"
        return pd.concat([sel_inv, sel_f5])
    return pick


def hybrid_ret(r):
    return r.ret_invalidation if r._use == "inv" else r.ret_fixed5


def hybrid_hold(r):
    return r.hold_invalidation if r._use == "inv" else r.hold_fixed5


for period in ["IS", "OOS", "ALL"]:
    sub = piv if period == "ALL" else piv[piv.period == period]
    print(f"\n[{period}] windows={sub.sig_cal.nunique()}")
    for label, pick, rc, hc in [
        ("fixed5", pick_fixed5, "ret_fixed5", "hold_fixed5"),
        ("invalidation", pick_inv, "ret_invalidation", "hold_invalidation"),
    ]:
        st = simulate(sub, pick, rc, hc, label)
        print(f"  {st['label']:16s} 累计 {st['total_ret_pct']:+8.2f}%  "
              f"成交 {st['filled']} 因锁仓放弃 {st['skipped_locked']} 期末未平 {st['open_at_end']}")
    # hybrid 75/25 via unified columns
    sub2 = sub.dropna(subset=["ret_fixed5", "ret_invalidation"]).copy()
    pick = make_pick_hybrid(0.25)
    # adapt: give picked rows unified fields via itertuples hack
    def pick_wrap(g, free, _p=pick):
        s = _p(g, free)
        s = s.copy()
        s["ret_u"] = np.where(s._use == "inv", s.ret_invalidation, s.ret_fixed5)
        s["hold_u"] = np.where(s._use == "inv", s.hold_invalidation, s.hold_fixed5)
        return s
    st = simulate(sub2, pick_wrap, "ret_u", "hold_u", "hybrid75_25")
    print(f"  {st['label']:16s} 累计 {st['total_ret_pct']:+8.2f}%  "
          f"成交 {st['filled']} 因锁仓放弃 {st['skipped_locked']} 期末未平 {st['open_at_end']}")

# 稳健性：失效退出剔除未平仓后逐笔均值
inv = t[t.variant == "invalidation"]
print("\n失效退出逐笔: 全部均值", round(inv.ret.mean()*100, 2),
      "% | 剔除unfinished:", round(inv[inv.exit_reason != 'unfinished'].ret.mean()*100, 2),
      "% | 胜率(剔除后):", round((inv[inv.exit_reason != 'unfinished'].ret > 0).mean()*100, 1), "%")
