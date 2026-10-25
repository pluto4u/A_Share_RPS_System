"""Breakout momentum sleeve: trend-regime 20-day-high entries (research only).

New research line after the 2026-10-04 falsifications (pullback-gate and
promotion exits both dead). Opposite entry philosophy: in an index trend
regime, circle-pool stocks breaking to a 20-day high are bought at the tail
(close), NOT on pullbacks. Entries only on trend-regime days; exits follow
exit rules regardless (regime-flip cost is measured, not hidden).

Universe: train-track circle pool (60 trading days after a circle).
Regime: CSI300 > MA20, MA20 rising over 5d, close > MA60.
Entry: close > max(high[t-20..t-1]), tail buy at close, skip up-locked,
>=5 trading days between entries per stock.
Exits: fixed5 / fixed10 / ma20_break(cap 60) / trail_s04_t08; costs + T+1 +
limit-down roll via the shared exec helpers. Statistics only.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.engine import BUY_COST, sell_cost
from trend_start.ice_opt import simulate_trail_exit
from trend_start.panel import load_panel
from trend_start.pattern_backtest import _sell_close, _sell_next_open, exec_flags
from trend_start.slot_portfolio import slot_compound
from trend_start.train_track import train_track_signals

POOL_DAYS = 60
ENTRY_GAP = 5
MA20_CAP = 60
BRK_WIN = 20


def metrics(eq: pd.Series) -> tuple[float, float, float]:
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    mdd = float((eq / eq.cummax() - 1).min())
    return total * 100, ((1 + total) ** (1 / years) - 1) * 100, mdd * 100


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--slots", type=int, default=10)
    args = ap.parse_args()

    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    n_days = len(dates)
    w0 = int(dates.searchsorted(pd.Timestamp(args.start)))
    idx_s = pd.Series(panel.index_close, index=dates).ffill()
    ma20i = idx_s.rolling(20).mean()
    ma60i = idx_s.rolling(60).mean()
    trend = ((idx_s > ma20i) & (ma20i >= ma20i.shift(5)) & (idx_s > ma60i)).to_numpy()
    print(f"面板: {n_days} 天; 趋势态天数 {int(trend.sum())} "
          f"({trend.mean() * 100:.0f}%); 窗口 {dates[w0].date()} -> {dates[-1].date()}")

    idx = [str(c) for c in panel.codes]
    c = pd.DataFrame(panel.field("close").astype(float), index=idx)
    o = pd.DataFrame(panel.field("open").astype(float), index=idx)
    h = pd.DataFrame(panel.field("high").astype(float), index=idx)
    lo = pd.DataFrame(panel.field("low").astype(float), index=idx)
    print("[SIG] train-track circles ...", flush=True)
    circles = train_track_signals(o, h, lo, c).to_numpy()
    n_stk = circles.shape[0]
    pool = np.zeros_like(circles)
    for s in range(n_stk):
        for b in np.flatnonzero(circles[s]):
            pool[s, b + 1:b + 1 + POOL_DAYS] = True
    print(f"[POOL] circles {int(circles.sum())}", flush=True)

    sc = sell_cost(panel.dates)
    close_np, open_np = c.to_numpy(), o.to_numpy()
    high_np, low_np = h.to_numpy(), lo.to_numpy()

    rows: list[dict] = []
    for s, code in enumerate(idx):
        if s % 500 == 0:
            print(f"[SIM] {s}/{n_stk}", flush=True)
        if not pool[s].any():
            continue
        cols = np.flatnonzero(np.isfinite(close_np[s]))
        if len(cols) < 300:
            continue
        c_, o_, h_, lo_ = (close_np[s][cols], open_np[s][cols],
                           high_np[s][cols], low_np[s][cols])
        ma = pd.Series(c_).rolling(20).mean().to_numpy()
        dts = panel.dates[cols]
        fl = exec_flags(code, o_, h_, lo_, c_, dts)
        up_locked, down_open, locked = fl["up_locked"], fl["down_open"], fl["locked"]
        n = len(c_)
        last_entry = -999
        for e in range(260, n - 2):
            e_cal = int(cols[e])
            if not (pool[s, e_cal] and trend[e_cal]):
                continue
            if up_locked[e] or e - last_entry < ENTRY_GAP:
                continue
            if not c_[e] > h_[e - BRK_WIN:e].max():  # close breaks prior 20d high
                continue
            last_entry = e
            entry_px = float(c_[e])

            def rec(variant: str, bar: int, px: float, reason: str,
                    _e: int = e, _ec: int = e_cal, _px: float = entry_px) -> None:
                exit_cal = int(cols[min(bar, n - 1)])
                rows.append({"variant": variant, "code": code,
                             "entry_cal": _ec, "exit_cal": exit_cal,
                             "hold": int(bar - _e), "exit_reason": reason,
                             "ret": float(px * (1 - sc[exit_cal])
                                          / (_px * (1 + BUY_COST)) - 1)})

            for hz, name in ((5, "fixed5"), (10, "fixed10")):
                bar, px, unf, _ac = _sell_close(min(e + hz, n - 1), n, locked,
                                                down_open, o_, c_)
                rec(name, bar, px, "unfinished" if unf else "time_stop")
            trig = n
            for j in range(e + 1, min(e + MA20_CAP, n - 1) + 1):
                if c_[j] < ma[j]:
                    trig = j
                    break
            if trig < n:
                bar, px, unf, _ac = _sell_next_open(trig, n, down_open, locked,
                                                    o_, c_)
                rec("ma20_break", bar, px,
                    "unfinished" if unf else "ma20_break")
            else:
                bar, px, unf, _ac = _sell_close(min(e + MA20_CAP, n - 1), n,
                                                locked, down_open, o_, c_)
                rec("ma20_break", bar, px,
                    "unfinished" if unf else "time_stop")
            bar, px, reason, _ac = simulate_trail_exit(
                code, e, o_, h_, lo_, c_, dts,
                hard_stop=0.04, trail_pct=0.08, max_hold=20)
            rec("trail_s04_t08", bar, px, reason)

    sim = pd.DataFrame(rows)
    out = ROOT / "output/breakout_sleeve_20261004"
    out.mkdir(parents=True, exist_ok=True)
    sim.to_csv(out / "breakout_trades.csv", index=False, encoding="utf-8-sig")
    print(f"\n[SIM] 突破信号 {sim.entry_cal.nunique()} 天 / {len(sim)} 行")

    w = sim[sim.entry_cal >= w0]
    print(f"\n=== 每笔口径（窗口 {dates[w0].date()} 起，趋势态突破入场）===")
    for v, g in w.groupby("variant"):
        print(f"  {v:<14} n={len(g):>4}  均值 {g.ret.mean() * 100:+6.2f}%  "
              f"胜率 {(g.ret > 0).mean() * 100:5.1f}%  中位持仓 {g.hold.median():.0f} 天")

    # sleeve combo + blend with F ice sleeve
    pool_tr = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                          encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")

    def prio(df: pd.DataFrame, p: int) -> pd.DataFrame:
        d = df.copy()
        d["prio"] = p
        return d

    f = pd.concat([
        prio(pool_tr[(pool_tr.channel == "ice") & (pool_tr.variant == "fixed5")], 0),
        prio(dual[(dual.engine == "ice") & (dual.variant == "fixed5")], 1),
        prio(dual[(dual.engine == "chase") & (dual.variant == "fixed3")], 2),
    ], ignore_index=True)

    def curve(trades: pd.DataFrame, slots: int) -> pd.Series:
        s = trades[(trades.entry_cal >= w0) & (trades.entry_cal <= n_days - 1)]
        t = s.rename(columns={"entry_cal": "sig_cal"})
        cols = ["sig_cal", "code", "ret", "hold"] + (["prio"] if "prio" in t else [])
        r = slot_compound(t[cols], slots=slots, cal_end=n_days - 1)
        eq = pd.Series(r.equity_by_day).sort_index()
        full = pd.Series(1.0, index=dates[w0:])
        if len(eq):
            eq.index = dates[eq.index.to_numpy()]
            full.update(eq)
        return full.ffill()

    ice_eq = curve(f, 20)
    tf = metrics(ice_eq)
    print(f"\n[F 冰点 20槽] 累计 {tf[0]:+.1f}%  回撤 {tf[2]:.1f}%")

    print("\n=== 突破分账（10槽）+ 与 F 50/50 混合 ===")
    curves = {}
    for v, g in w.groupby("variant"):
        eq = curve(g, args.slots)
        curves[v] = eq
        t, cg, d = metrics(eq)
        blend = metrics(0.5 * ice_eq + 0.5 * eq)
        print(f"{v:<14} 分账 {t:+6.1f}%/{cg:+5.1f}%/dd{d:6.1f}%   "
              f"混合 {blend[0]:+6.1f}%/{blend[1]:+5.1f}%/dd{blend[2]:6.1f}%")
    ib = metrics(idx_s.iloc[w0:] / idx_s.iloc[w0])
    print(f"[沪深300] {ib[0]:+.1f}% / dd{ib[2]:.1f}%")

    top = idx_s.iloc[w0:].idxmax()
    print(f"\n=== 拐点解剖：指数顶 {top.date()} 之后 ===")
    for v, eq in curves.items():
        after = eq[eq.index >= top]
        if len(after) > 1:
            dd = float((after / after.cummax() - 1).min()) * 100
            chg = (after.iloc[-1] / after.iloc[0] - 1) * 100
            print(f"{v:<14} 顶后 {chg:+6.1f}%  顶后回撤 {dd:6.1f}%")

    mo = pd.DataFrame({"ice": ice_eq, **curves})
    mret = mo.groupby(mo.index.to_period("M")).last().pct_change() * 100
    first = mo.groupby(mo.index.to_period("M")).last().iloc[0]
    mret.iloc[0] = (first - 1) * 100
    print("\n=== 月度节奏 ===")
    print(mret.round(1).to_string())

    pd.DataFrame({"ice": ice_eq, **curves}).to_csv(out / "curves.csv",
                                                   encoding="utf-8-sig")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
