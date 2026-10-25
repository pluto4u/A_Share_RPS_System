"""Circle-pool timing backtest: train-track watchlist + timed entries.

The train-track circle defines a WATCHLIST (quality filter: strong RPS, orderly
consolidation, near highs). Entries are timed, not chased:
  channel A (pullback): in-pool stock whose low touches MA20 and close holds
                        (>= MA20*0.97) -> tail buy; >=5 trading days between
                        entries per stock;
  channel B (ice day):  in-pool stock on a market ice-gate day
                        (a0.22_s2_w1_none, no overheat) -> tail buy.
Pool membership: 60 trading days after the circle day.
Exits: fixed5 / fixed10 / ma20_break(cap 60) / trail_s04_t08, costs + T+1 +
limit-down roll. Protocol: IS inspects, OOS validates. 20-slot cash pool.
Statistics only; no buy/sell instructions.
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
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.engine import BUY_COST, sell_cost
from trend_start.ice_opt import IceGateParams, ice_gate_mask, simulate_trail_exit
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel
from trend_start.pattern_backtest import _sell_close, _sell_next_open, exec_flags
from trend_start.slot_portfolio import slot_compound
from trend_start.train_track import train_track_signals

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
POOL_DAYS = 60
ENTRY_GAP = 5
TOUCH_TOL = 0.97
EXITS = ("fixed5", "fixed10", "ma20_break", "trail_s04_t08")
MA20_CAP = 60


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=20)
    args = ap.parse_args()
    # Post-freeze TDX refresh whitelisted (frozen window verified identical)
    verify_snapshot(args.snapshot, allow_drift=(
        r"vipdoc\sz\lday\sz300909.day", r"T0002\hq_cache\gbbq"))
    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    args.output.mkdir(parents=True, exist_ok=False)

    idx = [str(c) for c in panel.codes]
    c = pd.DataFrame(panel.field("close").astype(float), index=idx)
    o = pd.DataFrame(panel.field("open").astype(float), index=idx)
    h = pd.DataFrame(panel.field("high").astype(float), index=idx)
    lo = pd.DataFrame(panel.field("low").astype(float), index=idx)
    print("[SIG] train-track circles ...", flush=True)
    circles = train_track_signals(o, h, lo, c).to_numpy()      # stocks x days

    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    ice_ok = gate & ~hot
    ice0 = classify_ice(sent, panel.index_close)
    repair_days = ice0.repair.to_numpy(dtype=bool)

    n_stk, n_day = circles.shape
    # pool membership: True for POOL_DAYS days AFTER a circle
    pool = np.zeros_like(circles)
    for s in range(n_stk):
        for b in np.flatnonzero(circles[s]):
            pool[s, b + 1:b + 1 + POOL_DAYS] = True
    print(f"[POOL] circles {int(circles.sum())}, "
          f"avg pool size {pool.sum() / n_day:.0f}", flush=True)

    sc = sell_cost(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))
    close_np = c.to_numpy()
    low_np = lo.to_numpy()
    open_np = o.to_numpy()
    high_np = h.to_numpy()

    rows: list[dict] = []
    for s, code in enumerate(idx):
        if s % 500 == 0:
            print(f"[SIM] {s} stocks", flush=True)
        if not pool[s].any():
            continue
        cols = np.flatnonzero(np.isfinite(close_np[s]))  # segment: listed days
        if len(cols) < 300:
            continue
        c_ = close_np[s][cols]
        o_ = open_np[s][cols]
        h_ = high_np[s][cols]
        lo_ = low_np[s][cols]
        ma = pd.Series(c_).rolling(20).mean().to_numpy()
        dts = panel.dates[cols]
        fl = exec_flags(code, o_, h_, lo_, c_, dts)
        up_locked, down_open, locked = fl["up_locked"], fl["down_open"], fl["locked"]
        n = len(c_)
        last_entry = -999
        for e_cal in np.flatnonzero(pool[s]):
            e = int(np.searchsorted(cols, e_cal))
            if e >= n or cols[e] != e_cal or e < 260 or e >= n - 2:
                continue
            if up_locked[e] or e - last_entry < ENTRY_GAP:
                continue
            touch = low_np[s, e_cal] <= ma[e] and c_[e] >= ma[e] * TOUCH_TOL
            on_ice = ice_ok[e_cal]
            channels = (["pullback"] if touch else []) + (["ice"] if on_ice else [])
            if repair_days[e_cal] and not hot[e_cal]:
                channels.append("repair")
            if not channels:
                continue
            last_entry = e
            entry_px = float(c_[e])

            def rec(ch: str, variant: str, bar: int, px: float, reason: str,
                    ac: bool, _e: int = e, _ec: int = int(e_cal),
                    _px: float = entry_px, _code: str = code,
                    _cols: np.ndarray = cols, _n: int = n) -> None:
                exit_cal = int(_cols[min(bar, _n - 1)])
                rows.append({"channel": ch, "variant": variant, "code": _code,
                             "entry_cal": _ec, "exit_cal": exit_cal,
                             "hold": int(bar - _e), "exit_reason": reason,
                             "ret": float(px * (1 - sc[exit_cal])
                                          / (_px * (1 + BUY_COST)) - 1)})

            for ch in channels:
                for hz, name in ((5, "fixed5"), (10, "fixed10")):
                    bar, px, unf, ac = _sell_close(min(e + hz, n - 1), n, locked,
                                                   down_open, o_, c_)
                    rec(ch, name, bar, px, "unfinished" if unf else "time_stop", ac)
                trig = n
                for j in range(e + 1, min(e + MA20_CAP, n - 1) + 1):
                    if c_[j] < ma[j]:
                        trig = j
                        break
                if trig < n:
                    bar, px, unf, ac = _sell_next_open(trig, n, down_open, locked,
                                                       o_, c_)
                    rec(ch, "ma20_break", bar, px,
                        "unfinished" if unf else "ma20_break", ac)
                else:
                    bar, px, unf, ac = _sell_close(min(e + MA20_CAP, n - 1), n,
                                                   locked, down_open, o_, c_)
                    rec(ch, "ma20_break", bar, px,
                        "unfinished" if unf else "time_stop", ac)
                bar, px, reason, ac = simulate_trail_exit(
                    code, e, o_, h_, lo_, c_, dts,
                    hard_stop=0.04, trail_pct=0.08, max_hold=20)
                rec(ch, "trail_s04_t08", bar, px, reason, ac)

    sim = pd.DataFrame(rows)
    sim.to_csv(args.output / "pool_trades.csv", index=False, encoding="utf-8-sig")
    sim["period"] = np.where(sim.entry_cal >= oos0, "OOS", "IS")
    print(f"[SIM] rows: {len(sim)}", flush=True)

    st_rows = []
    for ch in ("pullback", "ice", "repair"):
        for v in EXITS:
            for period in ("IS", "OOS"):
                g = sim[(sim.channel == ch) & (sim.variant == v)
                        & (sim.period == period)]
                if len(g) == 0:
                    continue
                st_rows.append({"channel": ch, "variant": v, "period": period,
                                "n": len(g), "mean_ret_pct": g.ret.mean() * 100,
                                "win_pct": (g.ret > 0).mean() * 100,
                                "median_hold": g.hold.median()})
    stats = pd.DataFrame(st_rows)
    stats.to_csv(args.output / "pool_stats.csv", index=False, encoding="utf-8-sig")
    print("\n=== per-trade ===")
    print(stats.round(2).to_string(index=False))

    pf_rows = []
    for ch in ("pullback", "ice", "repair", "both"):
        for v in EXITS:
            sub = sim[sim.variant == v] if ch == "both" else \
                sim[(sim.channel == ch) & (sim.variant == v)]
            for period, (a, b) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9),
                                   "ALL": (0, 10**9)}.items():
                s = sub[(sub.entry_cal >= a) & (sub.entry_cal <= b)]
                if s.empty:
                    continue
                tr = s.rename(columns={"entry_cal": "sig_cal"})[
                    ["sig_cal", "code", "ret", "hold"]]
                r = slot_compound(tr, slots=args.slots)
                pf_rows.append({"channel": ch, "variant": v, "period": period,
                                "total_ret_pct": r.total_ret * 100,
                                "filled": r.filled,
                                "skipped_locked": r.skipped_locked})
    pf = pd.DataFrame(pf_rows)
    pf.to_csv(args.output / "pool_portfolio.csv", index=False, encoding="utf-8-sig")
    print("\n=== slot portfolio ===")
    print(pf.round(2).to_string(index=False))

    (args.output / "audit.json").write_text(json.dumps({
        "pool_days": POOL_DAYS, "entry_gap": ENTRY_GAP, "touch_tol": TOUCH_TOL,
        "gate": vars(GATE), "exits": list(EXITS), "slots": args.slots,
        "protocol": "circle defines watchlist; timed entries only; statistics only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
