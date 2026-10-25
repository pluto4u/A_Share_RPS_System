"""Final combo matrix toward OOS +20% (research only).

Builds on frozen artifacts:
  pool_trades.csv  - circle-pool entries, channel 'ice'/'pullback', exits grid
  dual_trades.csv  - engine A (quiet-pattern ice fixed5) + engine B (chase grid)
All combos share one slot pool (prio: pool-ice > pattern-ice > chase).
IS inspects, OOS validates. Statistics only; no buy/sell instructions.
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
from pathlib import Path

import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))
    args.output.mkdir(parents=True, exist_ok=False)

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(
        ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
        encoding="utf-8-sig")

    def pick_pool(variant: str, prio: int, channel: str = "ice") -> pd.DataFrame:
        d = pool[(pool.channel == channel) & (pool.variant == variant)].copy()
        d["prio"] = prio
        return d[["entry_cal", "code", "ret", "hold", "prio"]]

    def pick_pattern(prio: int) -> pd.DataFrame:
        d = dual[(dual.engine == "ice") & (dual.variant == "fixed5")].copy()
        d["prio"] = prio
        return d[["entry_cal", "code", "ret", "hold", "prio"]]

    def pick_chase(variant: str, prio: int) -> pd.DataFrame:
        d = dual[(dual.engine == "chase") & (dual.variant == variant)].copy()
        d["prio"] = prio
        return d[["entry_cal", "code", "ret", "hold", "prio"]]

    combos = {
        "poolice_f5 (基线)": [pick_pool("fixed5", 0)],
        "dual_old (基线)": [pick_pattern(0), pick_chase("fixed3", 1)],
        "B poolice_f5+chase_f3": [pick_pool("fixed5", 0), pick_chase("fixed3", 1)],
        "C poolice_f5+chase_f5": [pick_pool("fixed5", 0), pick_chase("fixed5", 1)],
        "D poolice_f5+chase_rep": [pick_pool("fixed5", 0), pick_chase("repair_day", 1)],
        "F 三引擎(pool优先)": [pick_pool("fixed5", 0), pick_pattern(1),
                            pick_chase("fixed3", 2)],
        "G 四引擎(全正期望)": [pick_pool("fixed5", 0), pick_pattern(1),
                            pick_pool("fixed5", 2, channel="repair"),
                            pick_chase("fixed5", 3)],
        "G2 四引擎(修复优先)": [pick_pool("fixed5", 0),
                             pick_pool("fixed5", 1, channel="repair"),
                             pick_pattern(2), pick_chase("fixed5", 3)],
        "H poolice_trail+chase_f3": [pick_pool("trail_s04_t08", 0),
                                  pick_chase("fixed3", 1)],
    }

    rows = []
    curves = {}
    for name, parts in combos.items():
        df = pd.concat(parts, ignore_index=True)
        for slots in (20,):
            for period, (a, b) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9),
                                   "ALL": (0, 10**9)}.items():
                s = df[(df.entry_cal >= a) & (df.entry_cal <= b)]
                tr = s.rename(columns={"entry_cal": "sig_cal"})
                r = slot_compound(tr[["sig_cal", "code", "ret", "hold", "prio"]],
                                  slots=slots)
                rows.append({"combo": name, "slots": slots, "period": period,
                             "total_ret_pct": r.total_ret * 100,
                             "filled": r.filled, "skipped": r.skipped_locked})
                if period == "ALL":
                    curves[name] = r.equity_by_day

    # slot sensitivity on combo B and C
    for name in ("B poolice_f5+chase_f3", "C poolice_f5+chase_f5"):
        df = pd.concat(combos[name], ignore_index=True)
        for slots in (10, 30):
            for period, (a, b) in {"OOS": (oos0, 10**9), "ALL": (0, 10**9)}.items():
                s = df[(df.entry_cal >= a) & (df.entry_cal <= b)]
                tr = s.rename(columns={"entry_cal": "sig_cal"})
                r = slot_compound(tr[["sig_cal", "code", "ret", "hold", "prio"]],
                                  slots=slots)
                rows.append({"combo": name, "slots": slots, "period": period,
                             "total_ret_pct": r.total_ret * 100,
                             "filled": r.filled, "skipped": r.skipped_locked})

    out = pd.DataFrame(rows)
    # max drawdown from ALL-period equity curve (cost-basis)
    dd = {k: float((pd.Series(v) / pd.Series(v).cummax() - 1).min() * 100)
          for k, v in curves.items()}
    out["maxdd_pct"] = [dd.get(c) if p == "ALL" else None
                        for c, p in zip(out.combo, out.period, strict=True)]
    out.to_csv(args.output / "combo_matrix.csv", index=False, encoding="utf-8-sig")
    print(out.round(2).to_string(index=False))

    # yearly equity for the leading combo
    lead = "G 四引擎(全正期望)"
    eq = pd.Series(curves[lead]).sort_index()
    eq.index = dates[eq.index.to_numpy()]
    yr = eq.groupby(eq.index.year).last()
    prev = pd.Series([eq.iloc[0], *yr.iloc[:-1]], index=yr.index)
    yearly = ((yr / prev - 1) * 100).round(2)
    print(f"\n[{lead}] yearly % (ALL):")
    print(yearly.to_string())
    yearly.to_csv(args.output / "lead_combo_yearly.csv", encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
