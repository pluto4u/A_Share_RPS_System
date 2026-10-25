"""Actionable scheme: drop->quiet-base->retest + RPS admission (research only).

User-specified window: entries in 2024-10-01..2026-08-31 (in-window tuning
caveat applies). Setup = existing causal chain (drop>=25%, base 10-30d
width<=18% dry<=0.75, volume breakout, quiet retest tail entry).
Admission layers tested: none / RPS120>=70 / >=80 / RPS120+250>=160 /
close>MA50 / MA20 rising. Sell rules compared via fwd5/10/20 and the
native invalidation exit. Operability: 5 slots, liquidity-first fill.
Statistics only; no buy/sell instructions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

T0, T1 = "2024-10-01", "2026-08-31"
BIG = 10_000_000


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    codes = [str(c) for c in panel.codes]
    close = pd.DataFrame(panel.field("close").astype(float), index=codes).T
    amount = pd.DataFrame(panel.field("amount").astype(float), index=codes).T

    ret120 = close.pct_change(120)
    ret250 = close.pct_change(250)
    rps120 = ret120.rank(axis=1, pct=True) * 100
    rps250 = ret250.rank(axis=1, pct=True) * 100
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma20_up = ma20 > ma20.shift(1)
    amt20 = amount.rolling(20).mean()

    tr = pd.read_csv(ROOT / "output/pattern_regime_backtest_20260930/"
                     "trades.csv", encoding="utf-8-sig")
    q = tr[(tr.kind == "quiet") & (tr.timing == "tail")
           & (tr.exit_set == "h20")].copy()
    a = int(dates.searchsorted(pd.Timestamp(T0)))
    b = int(dates.searchsorted(pd.Timestamp(T1)))
    q = q[(q.entry_cal >= a) & (q.entry_cal <= b)]
    print(f"[SIG] quiet-tail entries in window: {len(q)}, "
          f"unique days {q.entry_cal.nunique()}")

    idx = q.entry_cal.to_numpy()
    col = [codes.index(c) for c in q.code]  # noqa: C416 (slow but one-off)
    q["rps120"] = rps120.to_numpy()[idx, col]
    q["rps250"] = rps250.to_numpy()[idx, col]
    q["above50"] = (close > ma50).to_numpy()[idx, col]
    q["ma20up"] = ma20_up.to_numpy()[idx, col]
    q["liq"] = amt20.to_numpy()[idx, col]

    layers = {
        "无筛": np.ones(len(q), bool),
        "RPS120≥70": (q.rps120 >= 70).to_numpy(),
        "RPS120≥80": (q.rps120 >= 80).to_numpy(),
        "RPS120+250≥160": (q.rps120 + q.rps250 >= 160).to_numpy(),
        "收盘>MA50": q.above50.to_numpy(),
        "MA20向上": q.ma20up.to_numpy(),
        "RPS120≥80 且 >MA50": ((q.rps120 >= 80) & q.above50).to_numpy(),
    }
    print("\n=== 准入层 × 卖出规则（窗口内每笔） ===")
    print(f"{'layer':18s} {'n':>5s} {'fwd5':>7s} {'fwd10':>7s} "
          f"{'fwd20':>7s} {'失效退出':>8s} {'胜率5':>6s}")
    for name, m in layers.items():
        g = q[m]
        if len(g) < 30:
            continue
        print(f"{name:18s} {len(g):5d} {g.fwd5.mean()*100:+7.2f}"
              f" {g.fwd10.mean()*100:+7.2f} {g.fwd20.mean()*100:+7.2f}"
              f" {g.ret.mean()*100:+8.2f} {(g.fwd5 > 0).mean()*100:6.1f}")

    # operability: daily signal counts under the leading layer
    lead = q[(q.rps120 >= 80) & q.above50].drop(columns=["sig_cal"])
    per_day = lead.groupby("entry_cal").size()
    print(f"\n[OPER] 准入后 {len(lead)} 个信号 / {per_day.size} 个信号日, "
          f"日均 {per_day.mean():.1f} 只, 最多 {per_day.max()} 只/日")

    # 5-slot liquidity-first portfolio on leading layer, fixed10 exit
    d = lead.rename(columns={"entry_cal": "sig_cal"}).copy()
    d["ret"] = d.fwd10
    d["hold"] = 10
    d["prio"] = d.groupby("sig_cal")["liq"].rank(ascending=False)
    r = slot_compound(d[["sig_cal", "code", "ret", "hold", "prio"]], slots=5)
    eq = pd.Series(r.equity_by_day).sort_index()
    eq.index = dates[eq.index.to_numpy()]
    mo = eq.groupby([eq.index.year, eq.index.month]).last()
    prev = pd.Series([eq.iloc[0], *mo.iloc[:-1]], index=mo.index)
    mret = ((mo / prev - 1) * 100).round(2)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    print(f"\n[PF] 5槽流动性优先 fixed10: 累计 {r.total_ret * 100:+.2f}%  "
          f"年化 {((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100:+.2f}%  "
          f"maxdd {(eq / eq.cummax() - 1).min() * 100:+.2f}%  "
          f"filled {r.filled}/{len(d)}")
    print("月度:", mret.to_dict())


if __name__ == "__main__":
    main()
