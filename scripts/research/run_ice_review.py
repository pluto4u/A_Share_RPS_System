"""Monthly review of the final ice-point strategy over a date range (research only).

For each trading day in the range: gate state (ice streak / overheat / repair /
reversal), then the strategy's entries on gate days with outcomes under the
fixed5 / repair_day exits. Ends at observations; no buy/sell instructions.
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

import pandas as pd
from run_daily_rps import names_from_tnf
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--exit-trades", type=Path,
                    default=ROOT / "output/ice_optimize_robust_20260930/exit_trades.csv")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    dates = panel.dates
    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice = classify_ice(sent, panel.index_close)
    frame = sent.join(ice)
    gate = ice_gate_mask(sent, GATE)
    frame["gate"] = gate
    frame["overheat"] = frame.advance_share >= 0.85
    idx = pd.Series(panel.index_close, index=pd.DatetimeIndex(dates)).ffill()
    frame["csi300"] = idx
    frame["csi300_chg_pct"] = idx.pct_change() * 100
    frame["csi300_fwd5_pct"] = idx.shift(-5) / idx * 100 - 100

    rng = frame.loc[args.start:args.end]
    args.output.mkdir(parents=True, exist_ok=False)
    rng.to_csv(args.output / "daily_states.csv", encoding="utf-8-sig")

    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    trades = pd.read_csv(args.trades)
    trades["d"] = pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()])
    quiet = trades.loc[(trades.kind == "quiet") & (trades.timing == "tail")
                       & (trades.exit_set == "h2")].copy()
    gated = quiet.loc[gate[quiet.sig_cal.to_numpy()]
                      & (quiet.d >= args.start) & (quiet.d <= args.end)].copy()
    gated["name"] = [names.get(str(c), "缺") for c in gated.code]
    ev = pd.read_csv(args.exit_trades)
    ev["d"] = pd.DatetimeIndex(dates[ev.sig_cal.to_numpy()])
    ev = ev.loc[(ev.d >= args.start) & (ev.d <= args.end)]
    wide = ev.pivot_table(index=["code", "sig_cal"], columns="variant",
                          values="ret", aggfunc="first").reset_index()
    hold = ev.pivot_table(index=["code", "sig_cal"], columns="variant",
                          values="hold", aggfunc="first").reset_index()
    wide = wide.rename(columns={v: f"ret_{v}" for v in ("fixed5", "repair_day")})
    hold = hold.rename(columns={v: f"hold_{v}" for v in ("fixed5", "repair_day")})
    gated = gated.merge(wide, on=["code", "sig_cal"], how="left") \
                 .merge(hold, on=["code", "sig_cal"], how="left")
    cols = ["d", "code", "name", "entry_px", "fwd1", "fwd2", "fwd5",
            "ret_fixed5", "hold_fixed5", "ret_repair_day", "hold_repair_day"]
    gated[cols].sort_values("d").to_csv(args.output / "review_trades.csv",
                                        index=False, encoding="utf-8-sig")

    summary = {
        "range": [args.start, args.end],
        "sessions": int(len(rng)),
        "gate_days": [str(d.date()) for d in rng.index[rng.gate]],
        "overheat_days": [str(d.date()) for d in rng.index[rng.overheat]],
        "repair_days": [str(d.date()) for d in rng.index[rng.repair]],
        "reversal_days": [str(d.date()) for d in rng.index[rng.reversal]],
        "min_advance_share": float(rng.advance_share.min()),
        "min_advance_date": str(rng.advance_share.idxmin().date()),
        "entries": int(len(gated)),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if len(gated):
        print(gated[cols].sort_values("d").to_string(index=False), flush=True)
    print(f"[DONE] {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
