"""Dual-engine backtest: ice-point engine + weak-resonance chase engine.

Engine A (ice, validated): quiet-retest tail entries on a0.22_s2_w1_none gate
days, exit fixed5.
Engine B (chase, under test): breakout/confirmed tail entries on weak-resonance
reversal days (market repair & amount>=1.1x prev5 & zt>=20 & max_streak>2),
exit selected from a grid on IS, validated on OOS.

Both engines share one 20-slot pool (trend_start.slot_portfolio cash
compounding). Protocol: IS (2016-09->2021-12) selects, OOS (2022-01->)
validates only. Statistics only; no buy/sell instructions.
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
from trend_start.ice_opt import IceGateParams, ice_gate_mask, simulate_exit_variants
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
CHASE_EXITS = ("fixed1", "fixed2", "fixed3", "fixed5", "fixed10",
               "target5", "target8", "repair_day")
IS_END = pd.Timestamp("2021-12-31")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=20)
    ap.add_argument("--amount-thr", type=float, default=1.1,
                    help="chase-day amount/prev5 threshold (grid: 1.05 valid)")
    args = ap.parse_args()
    # Post-freeze TDX refresh whitelisted (frozen window verified identical)
    verify_snapshot(args.snapshot, allow_drift=(
        r"vipdoc\sz\lday\sz300909.day", r"T0002\hq_cache\gbbq"))
    panel = load_panel(args.snapshot)
    dates = panel.dates
    args.output.mkdir(parents=True, exist_ok=False)

    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    ice_ok = gate & ~hot
    amount_surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    repair = ice0.repair.to_numpy(dtype=bool)
    weak_rev = (repair & (amount_surge >= args.amount_thr)
                & (sent.zt_count.to_numpy() >= 20)
                & (sent.max_streak.to_numpy() > 2))
    print(f"[DAY] ice days {int(ice_ok.sum())}, chase days {int(weak_rev.sum())}, "
          f"overlap {int((ice_ok & weak_rev).sum())}", flush=True)

    trades = pd.read_csv(args.trades)
    ice_sel = trades.loc[(trades.kind == "quiet") & (trades.timing == "tail")
                         & (trades.exit_set == "h2")]
    ice_sel = ice_sel.loc[ice_ok[ice_sel.sig_cal.to_numpy()]]
    chase_sel = trades.loc[(trades.kind.isin(["breakout", "confirmed"]))
                           & (trades.timing == "tail") & (trades.exit_set == "h20")]
    chase_sel = chase_sel.loc[weak_rev[chase_sel.sig_cal.to_numpy()]]
    print(f"[ENTRY] ice {len(ice_sel)}, chase {len(chase_sel)}", flush=True)

    pcfg = PatternConfig.model_validate(
        json.loads((ROOT / "config/drop_base_retest.json").read_text(encoding="utf-8")))
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    sc = sell_cost(dates)
    repair_days = repair
    climax_days = np.zeros(len(dates), dtype=bool)

    def simulate(sel: pd.DataFrame, kinds: set[str], exits: tuple[str, ...],
                 tag: str) -> pd.DataFrame:
        rows: list[dict] = []
        for k, (code, g) in enumerate(sel.groupby("code")):
            if k % 200 == 0:
                print(f"[SIM-{tag}] {k} stocks", flush=True)
            i = code_row[code]
            cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
            d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float)
                              for f in panel.data})
            try:
                entries = pattern_entry_events(d, pcfg)
            except ValueError:
                continue
            levels = {b: (pv, fl) for b, kd, pv, fl in entries if kd in kinds}
            o, h, lo, c = (d.open.to_numpy(), d.high.to_numpy(),
                           d.low.to_numpy(), d.close.to_numpy())
            cal = cols.astype(np.int64)
            dts = dates[cols]
            for r in g.itertuples(index=False):
                if int(r.sig_bar) not in levels:
                    continue
                pivot, floor = levels[int(r.sig_bar)]
                e = int(r.entry_bar)
                out = simulate_exit_variants(code, e, pivot, floor, o, h, lo, c,
                                             cal, dts, repair_days, climax_days)
                for v in exits:
                    bar, px, reason, ac = out[v]
                    exit_cal = int(cal[min(bar, len(cal) - 1)])
                    rows.append({"engine": tag, "variant": v, "code": code,
                                 "entry_cal": int(cal[e]), "exit_cal": exit_cal,
                                 "hold": exit_cal - int(cal[e]),
                                 "exit_reason": reason,
                                 "ret": float(px * (1 - sc[exit_cal])
                                              / (c[e] * (1 + BUY_COST)) - 1)})
        return pd.DataFrame(rows)

    ice_sim = simulate(ice_sel, {"quiet"}, ("fixed5",), "ice")
    chase_sim = simulate(chase_sel, {"breakout", "confirmed"}, CHASE_EXITS, "chase")
    sim = pd.concat([ice_sim, chase_sim], ignore_index=True)
    sim.to_csv(args.output / "dual_trades.csv", index=False, encoding="utf-8-sig")
    print(f"[SIM] rows: {len(sim)}", flush=True)

    cal_dt = pd.DatetimeIndex(dates)
    oos0 = int(cal_dt.searchsorted(pd.Timestamp("2022-01-01")))
    sim["period"] = np.where(sim.entry_cal >= oos0, "OOS", "IS")

    # per-trade stats for chase exits
    st_rows = []
    for v in CHASE_EXITS:
        for period in ("IS", "OOS"):
            g = sim[(sim.engine == "chase") & (sim.variant == v)
                    & (sim.period == period)]
            st_rows.append({"variant": v, "period": period, "n": len(g),
                            "mean_ret_pct": g.ret.mean() * 100,
                            "win_pct": (g.ret > 0).mean() * 100,
                            "median_hold": g.hold.median()})
    stats = pd.DataFrame(st_rows)
    stats.to_csv(args.output / "chase_exit_stats.csv", index=False,
                 encoding="utf-8-sig")
    print("\n=== chase engine exits (per-trade) ===")
    print(stats.round(2).to_string(index=False))

    # portfolio layer: chase-only (each exit), ice-only fixed5, dual (ice fixed5
    # + chase with each exit), sharing one slot pool
    ice5 = sim[(sim.engine == "ice")][["entry_cal", "code", "ret", "hold", "period"]]
    pf_rows = []

    def run_pf(df: pd.DataFrame, label: str) -> None:
        for period, (lo_d, hi_d) in {"IS": (0, oos0 - 1),
                                     "OOS": (oos0, 10**9),
                                     "ALL": (0, 10**9)}.items():
            sub = df[(df.entry_cal >= lo_d) & (df.entry_cal <= hi_d)]
            tr = sub.rename(columns={"entry_cal": "sig_cal"})[
                ["sig_cal", "code", "ret", "hold"]]
            r = slot_compound(tr, slots=args.slots)
            pf_rows.append({"combo": label, "period": period,
                            "total_ret_pct": r.total_ret * 100,
                            "filled": r.filled, "skipped_locked": r.skipped_locked})

    run_pf(ice5, "ice_only_fixed5")
    for v in CHASE_EXITS:
        ch = sim[(sim.engine == "chase") & (sim.variant == v)][
            ["entry_cal", "code", "ret", "hold", "period"]]
        run_pf(ch, f"chase_only_{v}")
        run_pf(pd.concat([ice5, ch], ignore_index=True), f"dual_{v}")
    pf = pd.DataFrame(pf_rows)
    pf.to_csv(args.output / "dual_portfolio.csv", index=False, encoding="utf-8-sig")
    print("\n=== slot portfolio ===")
    print(pf.round(2).to_string(index=False))

    (args.output / "audit.json").write_text(json.dumps({
        "gate": vars(GATE),
        "chase_day_rule": "repair & amount>=thr & zt>=20 & streak>2",
        "amount_thr": args.amount_thr,
        "chase_exits": list(CHASE_EXITS), "slots": args.slots,
        "protocol": "IS selects, OOS validates; statistics only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
