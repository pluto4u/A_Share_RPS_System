"""Daily after-close environment monitor (statistics only; no trade advice).

Sections:
  1. regime metrics + traffic light (index vs MA250, amount vs 60d,
     advance share, dt/zt counts, defensive-crowding risk flag)
  2. sector crowding top/bottom (TDX level-2 industries, amount-share
     percentile vs trailing 250d)
  3. today's signal state: ice-gate / repair / chase flags, circle-pool
     candidates for tomorrow's watch, chase-day flag
  4. trailing-30-trade realized edge (pool-ice fixed5, trailing window sim)

Live mode rebuilds the panel from C:/new_tdx (as_of = latest index session);
--snapshot PATH validates against a frozen cache instead.
"""
from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from research.experiments.exp_sector_crowding import load_industry
from trend_start.engine import BUY_COST, sell_cost
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import build_panel, load_panel
from trend_start.pattern_backtest import exec_flags
from trend_start.train_track import train_track_signals

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
POOL_DAYS = 60
DEFENSIVE = {"银行", "石油", "交通设施", "电力", "保险"}  # 2024-02 signature
TRAIL_DAYS = 420          # window for trailing-edge sim
MON_DIR = PROJECT_ROOT / "output" / "monitor"


def trailing_edge(close: np.ndarray, open_: np.ndarray, high: np.ndarray,
                  low: np.ndarray, circles: np.ndarray, ice_ok: np.ndarray,
                  dates: pd.DatetimeIndex, codes: list[str],
                  sc: np.ndarray) -> tuple[float | None, int]:
    """Simulate pool-ice fixed5 over the trailing window; return (mean, n)."""
    n_stk, n_day = close.shape
    pool = np.zeros_like(circles)
    for s in range(n_stk):
        for b in np.flatnonzero(circles[s]):
            pool[s, b + 1:b + 1 + POOL_DAYS] = True
    rets: list[float] = []
    for s in range(n_stk):
        if not pool[s].any():
            continue
        cols = np.flatnonzero(np.isfinite(close[s]))
        if len(cols) < 280:
            continue
        c_ = close[s][cols]
        o_ = open_[s][cols]
        h_ = high[s][cols]
        lo_ = low[s][cols]
        fl = exec_flags(codes[s], o_, h_, lo_, c_, dates[cols])
        n = len(c_)
        last_entry = -999
        for e_cal in np.flatnonzero(pool[s] & ice_ok):
            e = int(np.searchsorted(cols, e_cal))
            if e >= n - 6 or cols[e] != e_cal or e < 260:
                continue
            if fl["up_locked"][e] or e - last_entry < 5:
                continue
            last_entry = e
            bar = min(e + 5, n - 1)
            while bar > e and fl["locked"][bar]:
                bar -= 1  # roll earlier only if exit day locked (rare)
            exit_cal = int(cols[bar])
            rets.append(float(c_[bar] * (1 - sc[exit_cal])
                              / (c_[e] * (1 + BUY_COST)) - 1))
    if len(rets) < 10:
        return None, len(rets)
    r30 = rets[-30:]
    return float(np.mean(r30)), len(r30)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, default=None,
                    help="frozen cache (validation mode); default = live TDX")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (snapshot mode)")
    args = ap.parse_args()

    if args.snapshot is not None:
        panel = load_panel(args.snapshot)
    else:
        from run_daily_rps import read_day  # noqa: PLC0415
        idx = read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day")
        as_of = pd.DatetimeIndex(idx.trade_date)[-1]
        cache = MON_DIR / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        print(f"[PANEL] rebuild as_of {as_of.date()} ...", flush=True)
        panel, _ = build_panel(args.tdx_root, as_of, cache)

    dates = pd.DatetimeIndex(panel.dates)
    codes = [str(c) for c in panel.codes]
    if args.as_of:
        dates = dates[dates <= pd.Timestamp(args.as_of)]
    d0 = max(0, len(dates) - TRAIL_DAYS - 260)
    dates = dates[d0:]
    close = panel.field("close").astype(float)[:, -len(dates):]
    open_ = panel.field("open").astype(float)[:, -len(dates):]
    high = panel.field("high").astype(float)[:, -len(dates):]
    low = panel.field("low").astype(float)[:, -len(dates):]
    amount = panel.field("amount").astype(float)[:, -len(dates):]
    idx_close = np.asarray(panel.index_close, dtype=float)[-len(dates):]
    today = dates[-1]
    print(f"[MON] {today.date()}, {len(dates)} bars, {len(codes)} codes",
          flush=True)

    sent = daily_sentiment(codes, dates, open_, high, close, amount)
    ice0 = classify_ice(sent, idx_close)
    gate = ice_gate_mask(sent, GATE)
    hot = bool(sent.advance_share.iloc[-1] >= 0.85)
    ice_today = bool(gate[-1]) and not hot
    repair_today = bool(ice0.repair.iloc[-1])
    amt_surge = float((sent.total_amount / sent.prev5_amount).iloc[-1])
    chase_today = bool(ice0.repair.iloc[-1] and amt_surge >= 1.05
                       and sent.zt_count.iloc[-1] >= 20
                       and sent.max_streak.iloc[-1] > 2)

    idx_s = pd.Series(idx_close, index=dates)
    above250 = float(idx_close[-1] > idx_s.rolling(250).mean().iloc[-1])
    amt_sum = np.nansum(amount, axis=0)
    amt_ratio = float(amt_sum[-1]
                      / pd.Series(amt_sum).rolling(60).mean().iloc[-1])

    # sector crowding
    code2hy, names = load_industry()
    hy = pd.Series([code2hy.get(c.split(".")[0]) for c in codes], index=codes)
    hy = hy[hy.notna()]
    amt_df = pd.DataFrame(amount, index=codes).loc[hy.index]
    sec_amt = amt_df.groupby(hy.values).sum()
    share = sec_amt.div(sec_amt.sum(axis=0), axis=1)
    pct = share.T.rolling(250, min_periods=60).apply(
        lambda x: (x.iloc[-1] >= x).mean(), raw=False).T
    crowd = pd.DataFrame({
        "sector": [names.get(s, s) for s in share.index],
        "share_pct250": pct.iloc[:, -1] * 100,
        "amt_share_pct": share.iloc[:, -1] * 100,
    }).dropna().sort_values("share_pct250", ascending=False)
    def_crowd = crowd[crowd.sector.isin(DEFENSIVE)]
    def_risk = bool((def_crowd.share_pct250 >= 95).any())

    # trailing edge
    sc = sell_cost(dates)
    circles = train_track_signals(
        pd.DataFrame(open_, index=codes), pd.DataFrame(high, index=codes),
        pd.DataFrame(low, index=codes), pd.DataFrame(close, index=codes),
    ).to_numpy()
    edge30, n30 = trailing_edge(close, open_, high, low, circles,
                                gate & ~np.asarray(
                                    sent.advance_share.to_numpy() >= 0.85),
                                dates, codes, sc)

    # traffic light
    if def_risk or sent.dt_count.iloc[-1] >= 100:
        light = "RED"
    elif above250 < 0.5 or amt_ratio < 0.9:
        light = "YELLOW"
    else:
        light = "GREEN"

    rep = {
        "date": str(today.date()),
        "light": light,
        "metrics": {
            "index_above_ma250": bool(above250),
            "amount_vs_60d": round(amt_ratio, 3),
            "advance_share": round(float(sent.advance_share.iloc[-1]), 3),
            "zt_count": int(sent.zt_count.iloc[-1]),
            "dt_count": int(sent.dt_count.iloc[-1]),
            "ice_gate_today": ice_today,
            "repair_today": repair_today,
            "chase_today": chase_today,
            "defensive_crowding_risk": def_risk,
            "edge30_mean_pct": (round(edge30 * 100, 2)
                                if edge30 is not None else None),
            "edge30_n": n30,
        },
        "crowded_top5": crowd.head(5).round(1).to_dict("records"),
        "crowded_bottom5": crowd.tail(5).round(1).to_dict("records"),
    }
    def _week_last_session(d: pd.Timestamp) -> bool:
        """Approximate: next weekday falls into a new W-FRI week.

        Covers Fridays and most holiday-shortened weeks; a week whose last
        session is Thu with Fri a holiday is a documented miss (the next
        week's scan still applies the 4-week dedup, so impact is a delayed
        entry at worst).
        """
        nxt = d + pd.Timedelta(days=1)
        while nxt.dayofweek >= 5:
            nxt += pd.Timedelta(days=1)
        return nxt.to_period("W-FRI") != d.to_period("W-FRI")

    if _week_last_session(today):  # weekly pullback forward scan
        try:
            from weekly_pullback_scan import scan as weekly_scan
            cache_dir = (Path(args.snapshot) if args.snapshot
                         else MON_DIR / "cache")
            wdf = weekly_scan(cache=cache_dir)
            rep["weekly_pullback"] = {
                "sig_week": str(wdf.sig_week.iloc[0]) if len(wdf) else None,
                "n": int(len(wdf)),
                "top": wdf.head(10).to_dict("records") if len(wdf) else [],
            }
            print(f"[WEEKLY] 回踩候选 {len(wdf)} 只, 已并入报告", flush=True)
        except Exception as exc:  # noqa: BLE001 - never block daily monitor
            rep["weekly_pullback"] = {"error": str(exc)}
            print(f"[WEEKLY][WARN] 周线扫描失败: {exc}", flush=True)
    MON_DIR.mkdir(parents=True, exist_ok=True)
    out = MON_DIR / f"{today.date()}.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    (MON_DIR / "latest.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
