"""原始价 vs 前复权：同一交易日的 RPS 偏差与 L1 / Super 名单差异。

只读本地通达信文件，不改日常 output/ 下的名单文件，不做收益测试。
另抽样核对 rps_analyzer.price_adjust 与已冻结的 run_local_tdx_compare.adjust 逐条一致。

用法:
    py -3.12 research/price_basis_20261006/compare.py --output output/price_basis_20261006/v1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rps_analyzer.price_adjust import adjust_one, default_gbbq_path, forward_adjust_market, load_gbbq  # noqa: E402
from rps_analyzer.rps_calculator import calculate_rps  # noqa: E402
from rps_analyzer.super_strong import screen_super_strong  # noqa: E402
from rps_analyzer.tdx_reader import load_tdx_market  # noqa: E402
from rps_analyzer.tdx_stock_screener import TdxScreenerConfig, TdxStockScreener  # noqa: E402

SPOT_CHECK = 40


def layers(df: pd.DataFrame, target: str) -> tuple[set[str], set[str], pd.DataFrame]:
    df = calculate_rps(df, periods=[50, 120, 250], verbose=False)
    l1 = TdxStockScreener(TdxScreenerConfig()).screen(df, target_date=target, verbose=False)
    l1_codes = set(l1["ts_code"]) if not l1.empty else set()
    sup = screen_super_strong(df, sorted(l1_codes), target_date=target, verbose=False)
    sup_codes = set(sup["ts_code"]) if not sup.empty else set()
    today = df.loc[df.trade_date == pd.Timestamp(target), ["ts_code", "RPS_50", "RPS_120", "RPS_250"]]
    return l1_codes, sup_codes, today.set_index("ts_code")


def spot_check(raw: pd.DataFrame, events: dict, target: pd.Timestamp) -> dict:
    sys.path.insert(0, str(ROOT / "tmp"))
    from run_local_tdx_compare import adjust as frozen_adjust

    codes = sorted(c for c in raw.ts_code.unique() if c in events)[:: max(1, len(events) // SPOT_CHECK)]
    checked, mismatched, both_rejected = 0, [], 0
    for code in codes[:SPOT_CHECK]:
        g = raw.loc[raw.ts_code == code].drop(columns="ts_code")
        try:
            new, _ = adjust_one(g, events[code], target)
        except ValueError as exc:
            new, new_err = None, str(exc)
        try:
            old, _ = frozen_adjust(g, events[code], target, bars=None)
        except ValueError as exc:
            old, old_err = None, str(exc)
        if new is None or old is None:
            if new is None and old is None and new_err == old_err:
                both_rejected += 1
            elif not (new is None and old is not None and "anchor" in old_err):
                mismatched.append(code)
            continue
        checked += 1
        if not np.allclose(new[["open", "high", "low", "close"]].to_numpy(),
                           old[["open", "high", "low", "close"]].to_numpy(), rtol=0, atol=1e-9):
            mismatched.append(code)
    return {"compared": checked, "both_rejected_same_reason": both_rejected, "mismatched": mismatched}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-dir", default=r"C:\new_tdx\vipdoc")
    ap.add_argument("--date", default=None, help="YYYYMMDD，默认最新交易日")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit(f"拒绝覆盖已有目录: {args.output}")

    raw = load_tdx_market(args.tdx_dir, min_days=250, verbose=False)
    target = args.date or raw.trade_date.max().strftime("%Y%m%d")
    ts = pd.Timestamp(target)
    raw = raw.loc[raw.trade_date <= ts]
    events = load_gbbq(default_gbbq_path(args.tdx_dir))
    qfq, excluded = forward_adjust_market(raw, events, target=ts, verbose=False)

    check = spot_check(raw, events, ts)
    print("spot check:", check, flush=True)

    print("raw layers...", flush=True)
    l1_raw, sup_raw, rps_raw = layers(raw, target)
    print("qfq layers...", flush=True)
    l1_qfq, sup_qfq, rps_qfq = layers(qfq, target)

    both = rps_raw.join(rps_qfq, lsuffix="_raw", rsuffix="_qfq", how="inner")
    for p in (50, 120, 250):
        both[f"d{p}"] = both[f"RPS_{p}_qfq"] - both[f"RPS_{p}_raw"]
    both["L1_raw"] = both.index.isin(l1_raw)
    both["L1_qfq"] = both.index.isin(l1_qfq)
    both["Super_raw"] = both.index.isin(sup_raw)
    both["Super_qfq"] = both.index.isin(sup_qfq)

    args.output.mkdir(parents=True)
    both.sort_values("d250").to_csv(args.output / "rps_by_stock.csv", encoding="utf-8-sig")
    excluded.to_csv(args.output / "qfq_excluded.csv", index=False, encoding="utf-8-sig")

    def dist(col: str) -> dict:
        a = both[col].abs()
        return {"median_abs": round(float(a.median()), 3), "p95_abs": round(float(a.quantile(0.95)), 3),
                "share_gt_5pts": round(float((a > 5).mean()), 4), "share_gt_20pts": round(float((a > 20).mean()), 4)}

    summary = {
        "as_of": target,
        "universe_raw": int(rps_raw["RPS_250"].notna().sum()),
        "universe_qfq": int(rps_qfq["RPS_250"].notna().sum()),
        "qfq_excluded": int(len(excluded)),
        "rps_delta_pts": {f"RPS{p}": dist(f"d{p}") for p in (50, 120, 250)},
        "L1": {"raw": len(l1_raw), "qfq": len(l1_qfq), "both": len(l1_raw & l1_qfq),
               "only_raw": sorted(l1_raw - l1_qfq), "only_qfq": sorted(l1_qfq - l1_raw)},
        "Super": {"raw": len(sup_raw), "qfq": len(sup_qfq), "both": len(sup_raw & sup_qfq),
                  "only_raw": sorted(sup_raw - sup_qfq), "only_qfq": sorted(sup_qfq - sup_raw)},
        "spot_check_vs_frozen_adjust": check,
        "limits": [
            "单日横截面对比，不是收益测试",
            "股票池为本地现存文件，缺历史退市股（见 rps_diag_20261006）",
            "前复权为本地 gbbq 重建，未与独立行情源对账",
        ],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("L1", "Super")}, ensure_ascii=False, indent=2))
    print({k: {kk: vv for kk, vv in summary[k].items() if not kk.startswith("only")} for k in ("L1", "Super")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
