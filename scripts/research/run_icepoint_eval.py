"""Evaluate KaiPanLa-style ice-point gates on existing pattern trades (no re-simulation).

Joins the frozen-trade ledger from run_pattern_regime_backtest with the
trend_start.icepoint sentiment frame, and reports excess returns with monthly
block-bootstrap CIs per ice level and repair/reversal resonance state.

Research only: statistics, no buy/sell instructions.
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
from run_pattern_regime_backtest import ROOT, trade_row, verify_snapshot
from trend_start.engine import block_bootstrap_mean
from trend_start.icepoint import from_panel
from trend_start.panel import load_panel
from trend_start.pattern_backtest import FWD_HORIZONS, control_fwd_returns

IS_END = pd.Timestamp("2021-12-31")


def excess_stats(g: pd.DataFrame, control: dict[int, np.ndarray],
                 dates: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for hz in FWD_HORIZONS:
        x = g[f"fwd{hz}"].to_numpy() - control[hz][g.sig_cal.to_numpy()]
        ok = np.isfinite(x)
        month = pd.Series(
            pd.DatetimeIndex(dates[g.sig_cal.to_numpy()[ok]]).to_period("M").astype(str)
        )
        lo, hi = block_bootstrap_mean(pd.Series(x[ok]), month)
        out[f"excess_fwd{hz}_pct"] = float(np.nanmean(x) * 100)
        out[f"ci_lo_fwd{hz}"] = lo * 100
        out[f"ci_hi_fwd{hz}"] = hi * 100
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    dates = panel.dates
    print("[ICE] computing sentiment frame", flush=True)
    sent, ice = from_panel(panel)
    args.output.mkdir(parents=True, exist_ok=False)
    sent.join(ice).to_csv(args.output / "icepoint_series.csv", encoding="utf-8-sig")

    trades = pd.read_csv(args.trades)
    trades["d"] = pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()])
    trades["period"] = np.where(trades.d <= IS_END, "IS", "OOS")
    for col in ice.columns:
        trades[col] = ice[col].to_numpy()[trades.sig_cal.to_numpy()]
    control = control_fwd_returns(panel.field("close"), dates)

    rows: list[dict] = []
    quiet_h2 = (trades.kind == "quiet") & (trades.timing == "tail") & (trades.exit_set == "h2")
    chase = trades.kind.isin(["breakout", "confirmed"]) & (trades.timing == "tail")

    specs = {
        # 冰点当日买缩量回踩 (用户规则: 连续冰点后买入, 持有 1-2 天)
        "ice_L1_quiet_h2": quiet_h2 & (trades.ice_level >= 1),
        "ice_L2_quiet_h2": quiet_h2 & (trades.ice_level >= 2),
        "ice_L3_quiet_h2": quiet_h2 & (trades.ice_level >= 3),
        "ice_extreme_quiet_h2": quiet_h2 & trades.ice_extreme.astype(bool),
        # 次日确认后再进: 修复日(反弹) vs 反转日追高
        "repair_quiet_h2": quiet_h2 & trades.repair.astype(bool),
        "rebound_only_quiet_h2": quiet_h2 & trades.rebound_only.astype(bool),
        "reversal_chase_h20": chase & trades.reversal.astype(bool)
                              & (trades.exit_set == "h20"),
        "rebound_chase_h20": chase & trades.rebound_only.astype(bool)
                             & (trades.exit_set == "h20"),
        # 对照
        "all_quiet_h2": quiet_h2,
        "all_chase_h20": chase & (trades.exit_set == "h20"),
    }
    for name, mask in specs.items():
        for period in ("IS", "OOS"):
            g = trades.loc[mask & (trades.period == period)]
            row = {"spec": name, "period": period, **trade_row(g)}
            if len(g) >= 20:
                row.update(excess_stats(g, control, dates))
            rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(args.output / "icepoint_eval.csv", index=False, encoding="utf-8-sig")

    dist = {
        "days": int(len(ice)),
        "ice_level_counts": ice.ice_level.value_counts().sort_index().to_dict(),
        "repair_days": int(ice.repair.sum()),
        "reversal_days": int(ice.reversal.sum()),
        "rebound_only_days": int(ice.rebound_only.sum()),
    }
    audit = {
        "generated_at": datetime.now().isoformat(),
        "snapshot": str(args.snapshot.resolve()),
        "trades_source": str(args.trades.resolve()),
        "distribution": dist,
        "limits": [
            "涨停判定按板块幅度(10/20%), 历史 ST 5% 未区分; 炸板率为日线高低价近似.",
            "融券余额/北向资金本地不可得, 资金面冰点用全市场成交额 5/60 比代替.",
            "冰点/反转阈值为首版设定, 未优化; 结果不构成买卖建议.",
        ],
    }
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    cols = ["spec", "period", "trades", "win_rate_pct", "mean_ret_pct",
            "excess_fwd2_pct", "ci_lo_fwd2", "ci_hi_fwd2",
            "excess_fwd5_pct", "excess_fwd20_pct"]
    print(table[[c for c in cols if c in table.columns]].to_string(index=False), flush=True)
    print(json.dumps(dist, ensure_ascii=False), flush=True)
    print(f"[DONE] {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
