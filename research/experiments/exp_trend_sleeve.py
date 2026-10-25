"""Trend sleeve: regime-gated circle-pool pullback, separate capital (research only).

Hypothesis: the pullback channel (circle-pool stock, low touches MA20, close
holds) was falsified UNGATED (OOS -0.51%/trade). Gate it with an index trend
regime and give it its own slot pool so long holds cannot starve the ice
sleeve. Entries only on trend-regime days; exits follow exit rules regardless
(measures how badly positions get caught at regime flips).

No new fills are simulated: reuses frozen pool_trades.csv (channel='pullback',
4 exit variants, costs + T+1 + limit-down roll already inside ret).
Statistics only; no buy/sell instructions.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.slot_portfolio import slot_compound

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"
EXITS = ("fixed5", "fixed10", "ma20_break", "trail_s04_t08")


def regime_masks(idx: pd.Series) -> dict[str, pd.Series]:
    ma20 = idx.rolling(20).mean()
    ma60 = idx.rolling(60).mean()
    return {
        "ungated": pd.Series(True, index=idx.index),
        "trend_ma20": (idx > ma20) & (ma20 >= ma20.shift(5)),
        "trend_ma20_60": (idx > ma20) & (ma20 >= ma20.shift(5)) & (idx > ma60),
    }


def metrics(eq: pd.Series) -> dict[str, float]:
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    return {"total_pct": total * 100, "cagr_pct": ((1 + total) ** (1 / years) - 1) * 100,
            "mdd_pct": float((eq / eq.cummax() - 1).min()) * 100}


def sleeve_curve(trades: pd.DataFrame, slots: int, w0: int, w1: int,
                 dates: pd.DatetimeIndex) -> tuple[pd.Series, int, int]:
    s = trades[(trades.entry_cal >= w0) & (trades.entry_cal <= w1)]
    tr = s.rename(columns={"entry_cal": "sig_cal"})[["sig_cal", "code", "ret", "hold"]]
    r = slot_compound(tr, slots=slots, cal_end=w1)
    eq = pd.Series(r.equity_by_day).sort_index()
    full = pd.Series(1.0, index=dates[w0:w1 + 1])
    if len(eq):
        eq.index = dates[eq.index.to_numpy()]
        full.update(eq)
    return full.ffill(), r.filled, r.skipped_locked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--trend-slots", type=int, default=10)
    ap.add_argument("--ice-slots", type=int, default=20)
    args = ap.parse_args()

    z = np.load(SNAPSHOT / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    idx = pd.Series(z["index_close"].astype(float), index=dates).ffill()
    w0 = int(dates.searchsorted(pd.Timestamp(args.start)))
    w1 = len(dates) - 1
    print(f"窗口: {dates[w0].date()} -> {dates[w1].date()}  ({w1 - w0 + 1} 个交易日)")

    regimes = regime_masks(idx)
    day_regime = {name: pd.Series(m.to_numpy(), index=np.arange(len(dates)))
                  for name, m in regimes.items()}

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    pb = pool[pool.channel == "pullback"].copy()

    # ---- per-trade: does the regime gate fix the falsified pullback channel?
    print("\n=== 每笔口径：回踩通道 × 环境闸（窗口内）===")
    print(f"{'exit':<14}{'gate':<14}{'n':>6}{'mean%':>8}{'win%':>7}{'med_hold':>9}")
    for v in EXITS:
        g0 = pb[(pb.variant == v) & (pb.entry_cal >= w0)]
        for gname, rmask in day_regime.items():
            g = g0[g0.entry_cal.map(rmask).fillna(False)]
            if len(g) == 0:
                continue
            print(f"{v:<14}{gname:<14}{len(g):>6}{g.ret.mean() * 100:>8.2f}"
                  f"{(g.ret > 0).mean() * 100:>7.1f}{g.hold.median():>9.0f}")

    # ---- sleeve curves per exit variant under the two trend gates
    print("\n=== 趋势分账（独立 10 槽）窗口组合口径 ===")
    rows = []
    curves: dict[str, pd.Series] = {}
    for v in EXITS:
        for gname in ("trend_ma20", "trend_ma20_60"):
            rmask = day_regime[gname]
            g = pb[(pb.variant == v)
                   & pb.entry_cal.map(rmask).fillna(False)]
            eq, filled, skipped = sleeve_curve(g, args.trend_slots, w0, w1, dates)
            m = metrics(eq)
            key = f"{v}|{gname}"
            curves[key] = eq
            rows.append({"sleeve": key, **m, "filled": filled,
                         "skipped": skipped})
            print(f"{key:<28} 累计 {m['total_pct']:+6.1f}%  年化 {m['cagr_pct']:+6.1f}%"
                  f"  回撤 {m['mdd_pct']:6.1f}%  成交 {filled} 跳过 {skipped}")

    # ---- ice sleeve (existing F combo) for the 50/50 blend
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")

    def prio(df: pd.DataFrame, p: int) -> pd.DataFrame:
        d = df.copy()
        d["prio"] = p
        return d

    f_parts = [
        prio(pool[(pool.channel == "ice") & (pool.variant == "fixed5")], 0),
        prio(dual[(dual.engine == "ice") & (dual.variant == "fixed5")], 1),
        prio(dual[(dual.engine == "chase") & (dual.variant == "fixed3")], 2),
    ]
    f = pd.concat(f_parts, ignore_index=True)
    s = f[(f.entry_cal >= w0) & (f.entry_cal <= w1)]
    tr = s.rename(columns={"entry_cal": "sig_cal"})[["sig_cal", "code", "ret", "hold", "prio"]]
    rf = slot_compound(tr, slots=args.ice_slots, cal_end=w1)
    eqf = pd.Series(rf.equity_by_day).sort_index()
    ice = pd.Series(1.0, index=dates[w0:w1 + 1])
    if len(eqf):
        eqf.index = dates[eqf.index.to_numpy()]
        ice.update(eqf)
    ice = ice.ffill()
    print(f"\n[F 冰点分账 20槽] 累计 {metrics(ice)['total_pct']:+.1f}%  "
          f"回撤 {metrics(ice)['mdd_pct']:.1f}%")

    # ---- 50/50 blend: ice sleeve + each trend sleeve
    print("\n=== 50/50 双引擎混合（日频再平衡口径）===")
    out_rows = []
    for key, teq in curves.items():
        blend = 0.5 * ice + 0.5 * teq
        m = metrics(blend)
        out_rows.append({"blend": f"F+{key}", **m})
        print(f"F + {key:<26} 累计 {m['total_pct']:+6.1f}%  年化 {m['cagr_pct']:+6.1f}%"
              f"  回撤 {m['mdd_pct']:6.1f}%")
    mi = metrics(idx.iloc[w0:w1 + 1] / idx.iloc[w0])
    print(f"[沪深300] 累计 {mi['total_pct']:+.1f}%  回撤 {mi['mdd_pct']:.1f}%")

    # ---- regime-flip autopsy: what happened to trend sleeves after the top?
    top = idx.iloc[w0:w1 + 1].idxmax()
    print(f"\n=== 拐点解剖：指数顶 {top.date()} 之后 ===")
    for key, teq in curves.items():
        after = teq[teq.index >= top]
        if len(after) > 1:
            dd = float((after / after.cummax() - 1).min()) * 100
            chg = (after.iloc[-1] / after.iloc[0] - 1) * 100
            print(f"{key:<28} 顶后变动 {chg:+6.1f}%  顶后最大回撤 {dd:6.1f}%")

    # ---- monthly rhythm of the best sleeve vs ice sleeve
    best = "trail_s04_t08|trend_ma20_60"
    if best in curves:
        mo = pd.DataFrame({"ice": ice, "trend": curves[best],
                           "blend": 0.5 * ice + 0.5 * curves[best]})
        mret = mo.groupby(mo.index.to_period("M")).last().pct_change() * 100
        mret.iloc[0] = (mo.groupby(mo.index.to_period("M")).last().iloc[0] - 1) * 100
        print(f"\n=== 月度节奏（ice=F冰点, trend={best}）===")
        print(mret.round(1).to_string())

    out = ROOT / "output/trend_sleeve_20261004"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "trend_sleeves.csv", index=False,
                              encoding="utf-8-sig")
    pd.DataFrame(out_rows).to_csv(out / "blends.csv", index=False,
                                  encoding="utf-8-sig")
    pd.DataFrame({"ice": ice, **curves}).to_csv(out / "curves.csv",
                                                encoding="utf-8-sig")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
