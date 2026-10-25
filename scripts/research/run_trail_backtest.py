"""Trailing-stop exit grid backtest for the ice-point combo (research only).

Entry side is identical to run_ice_portfolio (quiet-retest pattern, tail buy,
gate a0.22_s2_w1_none, no entries on overheated days). Exit side replaces
fixed/repair rules with a trailing grid: hard_stop x trail_pct, max_hold 20.
Protocol: IS (2016-09->2021-12) selects, OOS (2022-01->2026-09) validates only.
Portfolio layer: 20-slot cash compounding via trend_start.slot_portfolio.

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
from trend_start.drop_base_retest import PatternConfig, pattern_entry_events
from trend_start.engine import BUY_COST, sell_cost
from trend_start.ice_opt import IceGateParams, ice_gate_mask, simulate_trail_exit
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
STOPS = (0.03, 0.04, 0.05)
TRAILS = (0.06, 0.08, 0.10, 0.12)
MAX_HOLD = 20
IS_END = pd.Timestamp("2021-12-31")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=20)
    args = ap.parse_args()
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    dates = panel.dates
    args.output.mkdir(parents=True, exist_ok=False)

    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    _ = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    entry_ok = gate & ~hot

    trades = pd.read_csv(args.trades)
    quiet = trades.loc[(trades.kind == "quiet") & (trades.timing == "tail")
                       & (trades.exit_set == "h2")]
    sel = quiet.loc[entry_ok[quiet.sig_cal.to_numpy()]].copy()
    print(f"[ENTRY] gated entries: {len(sel)} (gate days {int(gate.sum())})",
          flush=True)

    pcfg = PatternConfig.model_validate(
        json.loads((ROOT / "config/drop_base_retest.json").read_text(encoding="utf-8")))
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    sc = sell_cost(dates)
    variants = [f"trail_s{int(s*100):02d}_t{int(t*100):02d}"
                for s in STOPS for t in TRAILS]

    rows: list[dict] = []
    for k, (code, g) in enumerate(sel.groupby("code")):
        if k % 200 == 0:
            print(f"[SIM] {k} stocks", flush=True)
        i = code_row[code]
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float)
                          for f in panel.data})
        try:
            entries = pattern_entry_events(d, pcfg)
        except ValueError:
            continue
        levels = {b for b, kd, _pv, _fl in entries if kd == "quiet"}
        o, h, lo, c = (d.open.to_numpy(), d.high.to_numpy(),
                       d.low.to_numpy(), d.close.to_numpy())
        cal = cols.astype(np.int64)
        dts = dates[cols]
        for r in g.itertuples(index=False):
            if int(r.sig_bar) not in levels:
                continue
            e = int(r.entry_bar)
            entry_px = float(c[e])
            for s in STOPS:
                for t in TRAILS:
                    bar, px, reason, ac = simulate_trail_exit(
                        code, e, o, h, lo, c, dts,
                        hard_stop=s, trail_pct=t, max_hold=MAX_HOLD)
                    exit_cal = int(cal[min(bar, len(cal) - 1)])
                    rows.append({
                        "variant": f"trail_s{int(s*100):02d}_t{int(t*100):02d}",
                        "code": code, "sig_cal": int(r.sig_cal),
                        "entry_cal": int(cal[e]),
                        "exit_cal": exit_cal,
                        "hold": exit_cal - int(cal[e]),
                        "exit_reason": reason, "exit_at_close": bool(ac),
                        "ret": float(px * (1 - sc[exit_cal])
                                     / (entry_px * (1 + BUY_COST)) - 1),
                    })
    sim = pd.DataFrame(rows)
    sim.to_csv(args.output / "trail_trades.csv", index=False, encoding="utf-8-sig")
    print(f"[SIM] rows: {len(sim)}", flush=True)

    cal_dt = pd.DatetimeIndex(dates)
    oos0 = int(cal_dt.searchsorted(pd.Timestamp("2022-01-01")))
    sim["period"] = np.where(sim.entry_cal >= oos0, "OOS", "IS")

    # per-trade stats
    st_rows = []
    for v in variants:
        for period in ("IS", "OOS"):
            g = sim[(sim.variant == v) & (sim.period == period)]
            fin = g[g.exit_reason != "unfinished"]
            st_rows.append({
                "variant": v, "period": period, "n": len(g),
                "mean_ret_pct": g.ret.mean() * 100,
                "win_pct": (g.ret > 0).mean() * 100,
                "median_hold": g.hold.median(),
                "unfinished_pct": (g.exit_reason == "unfinished").mean() * 100,
                "finished_mean_pct": fin.ret.mean() * 100,
                "finished_win_pct": (fin.ret > 0).mean() * 100,
            })
    stats = pd.DataFrame(st_rows)
    stats.to_csv(args.output / "trail_stats.csv", index=False, encoding="utf-8-sig")

    # portfolio layer: slot-aware compounding per variant x period
    pf_rows = []
    base = pd.read_csv(ROOT / "output/ice_portfolio_20260930/portfolio_trades_all.csv",
                       encoding="utf-8-sig")
    base5 = base[base.variant == "fixed5"].copy()
    base5["hold"] = base5.exit_cal - base5.entry_cal
    for label, df in [("fixed5_baseline", base5)] + \
            [(v, sim[sim.variant == v]) for v in variants]:
        for period in ("IS", "OOS", "ALL"):
            sub = df.copy()
            sub["entry_cal"] = sub.entry_cal.astype(int)
            if period != "ALL":
                lo_d, hi_d = (0, oos0 - 1) if period == "IS" else (oos0, 10**9)
                sub = sub[(sub.entry_cal >= lo_d) & (sub.entry_cal <= hi_d)]
            tr = sub.drop(columns=["sig_cal"], errors="ignore").rename(
                columns={"entry_cal": "sig_cal"})[["sig_cal", "code", "ret", "hold"]]
            r = slot_compound(tr, slots=args.slots)
            pf_rows.append({"variant": label, "period": period,
                            "total_ret_pct": r.total_ret * 100,
                            "filled": r.filled, "skipped_locked": r.skipped_locked})
    pf = pd.DataFrame(pf_rows)
    pf.to_csv(args.output / "trail_portfolio.csv", index=False, encoding="utf-8-sig")

    print("\n=== per-trade stats (finished trades only) ===")
    print(stats.round(2).to_string(index=False))
    print("\n=== slot portfolio (20 slots) ===")
    print(pf.round(2).to_string(index=False))

    (args.output / "audit.json").write_text(json.dumps({
        "gate": vars(GATE), "stops": STOPS, "trails": TRAILS,
        "max_hold": MAX_HOLD, "slots": args.slots,
        "protocol": "IS selects, OOS validates; statistics only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
