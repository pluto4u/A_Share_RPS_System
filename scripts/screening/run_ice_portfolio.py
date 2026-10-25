"""Portfolio-level backtest of the final ice-point combo (research only).

Combo under test (selected on IS, validated OOS in run_ice_optimize):
  entry:  quiet-retest pattern stocks, tail buy on days with advance_share <= 22%
          for >=2 consecutive sessions and weakening path (a0.22_s2_w1_none);
          no new entries on overheated days (advance >= 85%).
  exits:  fixed5 (close at entry+5) and repair_day (close of first market
          repair day) variants, each with limit-down roll and costs.
  sizing: 20 equal-weight slots, full allocation on gated days.

Output: equity curves and statistics only; no buy/sell instructions.
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
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.drop_base_retest import PatternConfig, pattern_entry_events
from trend_start.engine import BUY_COST, curve_stats, sell_cost
from trend_start.ice_opt import IceGateParams, ice_gate_mask, simulate_exit_variants
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel
from trend_start.pattern_backtest import portfolio_from_trades

IS_START, IS_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2021-12-31")
SLOTS = 20
GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
EXIT_SETS = ("fixed5", "repair_day", "fixed3", "fixed1")


def yearly(curve: pd.Series) -> dict[str, float]:
    y = curve.groupby(curve.index.year).last()
    prev = pd.Series([curve.iloc[0], *y.iloc[:-1]], index=y.index)
    return {str(k): float((y[k] / prev[k] - 1) * 100) for k in y.index}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=SLOTS)
    args = ap.parse_args()
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    dates = panel.dates
    as_of = pd.Timestamp(dates[-1])
    args.output.mkdir(parents=True, exist_ok=False)

    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    entry_ok = gate & ~hot
    repair_days = ice0.repair.to_numpy(dtype=bool)
    climax_days = np.zeros(len(dates), dtype=bool)

    trades = pd.read_csv(args.trades)
    quiet = trades.loc[(trades.kind == "quiet") & (trades.timing == "tail")
                       & (trades.exit_set == "h2")]
    sel = quiet.loc[entry_ok[quiet.sig_cal.to_numpy()]].copy()
    print(f"[ENTRY] gated entries: {len(sel)} (gate days {int(gate.sum())})", flush=True)

    pcfg = PatternConfig.model_validate(
        json.loads((ROOT / "config/drop_base_retest.json").read_text(encoding="utf-8")))
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    sc = sell_cost(dates)
    sim_rows: list[dict] = []
    for k, (code, g) in enumerate(sel.groupby("code")):
        if k % 200 == 0:
            print(f"[SIM] {k} stocks", flush=True)
        i = code_row[code]
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float) for f in panel.data})
        try:
            entries = pattern_entry_events(d, pcfg)
        except ValueError:
            continue
        levels = {b: (pv, fl) for b, kd, pv, fl in entries if kd == "quiet"}
        o, h, lo, c = (d.open.to_numpy(), d.high.to_numpy(),
                       d.low.to_numpy(), d.close.to_numpy())
        cal = cols.astype(np.int64)
        dts = dates[cols]
        for r in g.itertuples(index=False):
            if int(r.sig_bar) not in levels:
                continue
            pivot, floor = levels[int(r.sig_bar)]
            out = simulate_exit_variants(code, int(r.entry_bar), pivot, floor,
                                         o, h, lo, c, cal, dts, repair_days, climax_days)
            for v in EXIT_SETS:
                bar, px, reason, at_close = out[v]
                sim_rows.append({
                    "variant": v, "code": code,
                    "sig_cal": int(r.sig_cal), "entry_cal": int(cal[int(r.entry_bar)]),
                    "entry_px": float(c[int(r.entry_bar)]),
                    "exit_cal": int(cal[min(bar, len(cal) - 1)]), "exit_px": float(px),
                    "exit_at_close": bool(at_close), "exit_reason": reason,
                    "ret": float(px * (1 - sc[int(cal[min(bar, len(cal) - 1)])])
                                 / (c[int(r.entry_bar)] * (1 + BUY_COST)) - 1),
                })
    sim = pd.DataFrame(sim_rows)
    sim.to_csv(args.output / "portfolio_trades_all.csv", index=False, encoding="utf-8-sig")

    close_ff = pd.DataFrame(panel.field("close")).T.ffill().T.to_numpy(dtype=np.float32)
    cal_dt = pd.DatetimeIndex(dates)
    oos_start = int(cal_dt.searchsorted(pd.Timestamp("2022-01-01")))
    windows = {
        "IS": (int(cal_dt.searchsorted(IS_START)),
               int(cal_dt.searchsorted(IS_END, side="right") - 1)),
        "OOS": (oos_start, len(dates) - 1),
    }
    windows["FULL"] = (windows["IS"][0], len(dates) - 1)

    pf_rows: list[dict] = []
    curves: dict[str, pd.Series] = {}
    for v in EXIT_SETS:
        t = sim.loc[sim.variant == v]
        for w, (a, b) in windows.items():
            curve, done = portfolio_from_trades(t, close_ff, code_row, dates, a, b, args.slots)
            st = curve_stats(curve)
            st.update({"variant": v, "window": w, "slots": args.slots, "pf_trades": len(done),
                       "pf_win_rate_pct": float((done.pf_ret > 0).mean() * 100)
                       if len(done) else np.nan,
                       "pf_mean_ret_pct": float(done.pf_ret.mean() * 100)
                       if len(done) else np.nan})
            if len(done):
                held_days = (np.minimum(done.pf_exit_cal, b) - done.entry_cal + 1).sum()
                st["slot_usage_pct"] = float(held_days / (args.slots * (b - a + 1)) * 100)
            pf_rows.append(st)
            if w == "FULL":
                curves[v] = curve
        print(f"[PF] {v} done", flush=True)
    hs = pd.Series(panel.index_close).ffill()
    cmat = panel.field("close").astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        rets = cmat[:, 1:] / cmat[:, :-1] - 1
    ew = pd.Series(np.cumprod(1 + np.r_[0.0, np.nanmean(rets, axis=0)]))
    for w, (a, b) in windows.items():
        for bname, series in (("CSI300", hs), ("EqualWeightA", ew)):
            seg = series.iloc[a: b + 1]
            seg.index = cal_dt[a: b + 1]
            pf_rows.append({"variant": bname, "window": w, **curve_stats(seg / seg.iloc[0])})
            if w == "FULL":
                curves[bname] = seg / seg.iloc[0]
    pd.DataFrame(pf_rows).to_csv(args.output / "portfolio_stats.csv",
                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(curves).to_csv(args.output / "equity_curves.csv", encoding="utf-8-sig")
    pd.DataFrame({k: yearly(c) for k, c in curves.items()}).to_csv(
        args.output / "yearly_returns.csv", encoding="utf-8-sig")

    audit = {
        "generated_at": datetime.now().isoformat(),
        "combo": {"gate": vars(GATE), "exits": list(EXIT_SETS), "slots": args.slots,
                  "overheat_block": "advance_share >= 0.85"},
        "gate_days": int(gate.sum()),
        "entries": int(len(sel)),
        "as_of": str(as_of.date()),
        "limits": [
            "尾盘=信号日收盘近似; 极低仓位利用率意味着大部分时间是现金.",
            "正期望口径 CI 跨 0; 组合结果不构成买卖建议.",
        ],
    }
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(pd.DataFrame(pf_rows).to_string(index=False), flush=True)
    print(f"[DONE] {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
