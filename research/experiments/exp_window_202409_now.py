"""Window backtest: champion combo F on 2024-09-01 -> panel end (research only).

Reuses frozen per-trade artifacts (pool_trades.csv / dual_trades.csv); no new
signal generation, no parameter changes — this is a pure re-slicing of the
already-frozen F combo (pool-ice fixed5 > pattern-ice fixed5 > chase fixed3,
shared 20-slot cash pool). Statistics only; no buy/sell instructions.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.slot_portfolio import slot_compound

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-09-01", help="window start date")
    ap.add_argument("--tag", default=None, help="output folder tag")
    args = ap.parse_args()
    WSTART = pd.Timestamp(args.start)
    z = np.load(SNAPSHOT / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    idx_close = z["index_close"].astype(np.float64)
    w0 = int(dates.searchsorted(WSTART))
    w1 = len(dates) - 1
    print(f"窗口: {dates[w0].date()} -> {dates[w1].date()}  ({w1 - w0 + 1} 个交易日)")

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")

    def tag(df: pd.DataFrame, prio: int, name: str) -> pd.DataFrame:
        d = df.copy()
        d["prio"] = prio
        d["engine_name"] = name
        return d

    parts = [
        tag(pool[(pool.channel == "ice") & (pool.variant == "fixed5")], 0, "圈池冰点"),
        tag(dual[(dual.engine == "ice") & (dual.variant == "fixed5")], 1, "形态冰点"),
        tag(dual[(dual.engine == "chase") & (dual.variant == "fixed3")], 2, "放量追高"),
    ]
    df = pd.concat(parts, ignore_index=True)
    win = df[(df.entry_cal >= w0) & (df.entry_cal <= w1)]
    print(f"窗口内候选信号: {len(win)} 笔")

    tr = win.rename(columns={"entry_cal": "sig_cal"})
    r = slot_compound(tr[["sig_cal", "code", "ret", "hold", "prio"]],
                      slots=20, cal_end=w1)
    eq = pd.Series(r.equity_by_day).sort_index()
    eq.index = dates[eq.index.to_numpy()]
    # curve only exists from first entry; anchor at window start = 1.0
    eq = pd.concat([pd.Series([1.0], index=[dates[w0]]), eq])
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    print(f"\n[F 三引擎 @ 20槽] 成交 {r.filled} 笔 / 槽位不足跳过 {r.skipped_locked} 笔")
    print(f"累计 {total * 100:+.1f}%  年化 {cagr * 100:+.1f}%  最大回撤 {mdd * 100:.1f}%")

    # benchmark CSI300 same window
    ib = pd.Series(idx_close[w0:w1 + 1], index=dates[w0:w1 + 1]).ffill()
    itotal = ib.iloc[-1] / ib.iloc[0] - 1
    icagr = (1 + itotal) ** (1 / years) - 1
    imdd = float((ib / ib.cummax() - 1).min())
    print(f"[沪深300 同窗口] 累计 {itotal * 100:+.1f}%  年化 {icagr * 100:+.1f}%  最大回撤 {imdd * 100:.1f}%")

    # yearly breakdown of strategy vs index
    yr = eq.groupby(eq.index.year).last()
    prev = pd.Series([eq.iloc[0], *yr.iloc[:-1]], index=yr.index)
    print("\n逐年（策略, %）:")
    print(((yr / prev - 1) * 100).round(1).to_string())

    # per-engine per-trade stats (all candidate signals, not just filled)
    print("\n窗口内每笔统计（候选信号口径，含未成交）:")
    for name, g in win.groupby("engine_name"):
        print(f"  {name}: n={len(g)}  均值 {g.ret.mean() * 100:+.2f}%  "
              f"胜率 {(g.ret > 0).mean() * 100:.1f}%  中位 {g.ret.median() * 100:+.2f}%")

    out = ROOT / f"output/window_{args.tag or '202409_now'}_20261003"
    out.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out / "equity_window.csv", encoding="utf-8-sig")
    win.to_csv(out / "signals_window.csv", index=False, encoding="utf-8-sig")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
