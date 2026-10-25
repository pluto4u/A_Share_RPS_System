"""
================================================================================
早期启涨 → 前瞻验证 (Forward Recall / Forward Returns)
================================================================================
核心实验:
    在 early_date (例如 20260403) 跑早期启涨扫描 -> set A
    在 confirm_date (例如 20260417) 已有 L1 / 火车轨 csv -> set L1_C / H_C

    回答 4 个问题:
    1. A → L1_C / H_C 命中率 (前瞻召回): 早期信号能预测多大比例的 confirm 日强势股
    2. L1_C / H_C 中有多少出现在 A (precision from labels' POV): 标签集里有多少能被早期信号提前识别
    3. A 中每只票 early_date → confirm_date 的远期收益分布 (按是否 hit 分层)
    4. A 在 early_date 时距 confirm_date 真正启涨日的提前量

用法:
    py -3 verify_early_forward.py --early 20260403 --confirm 20260417
    py -3 verify_early_forward.py --early 20260403 --confirm 20260417 \
         --huoche-csv output/tdx_screener_20260417_huoche_codes.csv \
         --l1-csv output/tdx_screener_20260417_codes.csv
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import time
import pandas as pd
import numpy as np
from pathlib import Path

from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig,
)
from rps_analyzer.codes_csv import read_codes_csv

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def forward_returns(df: pd.DataFrame, codes: list,
                    start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    """计算 codes 中每只票从 start_dt → end_dt 的累计收益"""
    sub = df[df["ts_code"].isin(codes) & df["trade_date"].between(start_dt, end_dt)].copy()
    rows = []
    for code, sdf in sub.groupby("ts_code"):
        sdf = sdf.sort_values("trade_date")
        if len(sdf) < 2:
            continue
        s_close = sdf.iloc[0]["close"]
        e_close = sdf.iloc[-1]["close"]
        rows.append({
            "ts_code": code,
            "start_close": s_close,
            "end_close": e_close,
            "fwd_ret": e_close / s_close - 1,
            "max_high": sdf["high"].max(),
            "max_ret": sdf["high"].max() / s_close - 1,
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--early", required=True, help="早期启涨观察日 YYYYMMDD")
    parser.add_argument("--confirm", required=True, help="确认日 YYYYMMDD")
    parser.add_argument("--huoche-csv", default=None,
                        help="confirm 日火车轨清单, 默认 output/tdx_screener_{confirm}_huoche_codes.csv")
    parser.add_argument("--l1-csv", default=None,
                        help="confirm 日 L1 清单, 默认 output/tdx_screener_{confirm}_codes.csv")
    args = parser.parse_args()

    early_dt = pd.Timestamp(args.early)
    confirm_dt = pd.Timestamp(args.confirm)
    huoche_csv = Path(args.huoche_csv or OUTPUT_DIR / f"tdx_screener_{args.confirm}_huoche_codes.csv")
    l1_csv = Path(args.l1_csv or OUTPUT_DIR / f"tdx_screener_{args.confirm}_codes.csv")
    if not huoche_csv.exists() or not l1_csv.exists():
        raise FileNotFoundError(
            f"缺标签 csv: 火车轨={huoche_csv} L1={l1_csv}\n"
            f"先跑 run_tdx_screener.py --date {args.confirm}"
        )

    huoche_set = set(read_codes_csv(huoche_csv))
    l1_set = set(read_codes_csv(l1_csv))

    print("=" * 78)
    print(f"跨期验证: early={early_dt.date()}  confirm={confirm_dt.date()}  ")
    print(f"标签: L1={len(l1_set)}  火车轨={len(huoche_set)}")
    print("=" * 78)

    df = load_data()

    print("\n[1] 跑 early_date 早期启涨扫描 ...")
    t0 = time.time()
    early_res = screen_early_launch(df, target_date=args.early,
                                    config=EarlyLaunchConfig(), verbose=False)
    print(f"  耗时 {time.time()-t0:.1f}s, 通过 {len(early_res)} 只\n")

    if early_res.empty:
        print("早期启涨为空，结束")
        return
    A = set(early_res["ts_code"])

    # ========================================================================
    # [2] 命中率 / 召回率
    # ========================================================================
    a_in_l1 = A & l1_set
    a_in_h = A & huoche_set
    print("=" * 78)
    print("[2] 前瞻召回 / 精度")
    print("=" * 78)
    print(f"  A (早期启涨 @ {early_dt.date()})   : {len(A):4d} 只")
    print(f"  A ∩ L1_C  (L1 命中)               : {len(a_in_l1):4d} ({len(a_in_l1)/len(A)*100:.1f}% of A, "
          f"{len(a_in_l1)/len(l1_set)*100:.1f}% of L1)")
    print(f"  A ∩ H_C   (火车轨命中)            : {len(a_in_h):4d} ({len(a_in_h)/len(A)*100:.1f}% of A, "
          f"{len(a_in_h)/len(huoche_set)*100:.1f}% of 火车轨)")
    print(f"  A - L1_C  (L1 未覆盖, 噪音/未来)  : {len(A - l1_set):4d}")
    print(f"\n  关键: 火车轨 {len(huoche_set)} 只里 {len(a_in_h)} 只能在 {(confirm_dt-early_dt).days} 天前提前识别")

    if a_in_h:
        print(f"\n  提前识别的火车轨股: {sorted(a_in_h)}")

    # ========================================================================
    # [3] 远期收益 (按分组)
    # ========================================================================
    print("\n" + "=" * 78)
    print(f"[3] 远期收益 ({early_dt.date()} 收盘 → {confirm_dt.date()} 收盘)")
    print("=" * 78)
    fwd = forward_returns(df, list(A), early_dt, confirm_dt)
    if fwd.empty:
        print("  无数据")
        return

    fwd["IN_L1"] = fwd["ts_code"].isin(l1_set)
    fwd["IN_HUOCHE"] = fwd["ts_code"].isin(huoche_set)

    def stats(sub, label):
        if len(sub) == 0:
            print(f"  {label:<28}  n={len(sub):3d}")
            return
        m = sub["fwd_ret"].mean() * 100
        med = sub["fwd_ret"].median() * 100
        win = (sub["fwd_ret"] > 0).mean() * 100
        win10 = (sub["fwd_ret"] > 0.10).mean() * 100
        max_med = sub["max_ret"].median() * 100
        print(f"  {label:<28}  n={len(sub):3d}  mean={m:6.2f}%  med={med:6.2f}%  "
              f"win={win:5.1f}%  >+10%={win10:5.1f}%  max_med={max_med:6.2f}%")

    stats(fwd, "全部 A (早期启涨)")
    stats(fwd[fwd["IN_HUOCHE"]], "  ∩ 火车轨 (最强)")
    stats(fwd[fwd["IN_L1"] & ~fwd["IN_HUOCHE"]], "  ∩ L1 - 火车轨 (准强)")
    stats(fwd[~fwd["IN_L1"]], "  - L1 (噪音/未来)")

    # 基准: 全市场同期收益
    market_codes = df["ts_code"].unique()
    bench = forward_returns(df, list(market_codes), early_dt, confirm_dt)
    stats(bench, "[基准] 全市场全集")

    # ========================================================================
    # [4] 远期收益 Top 20 / Bottom 10
    # ========================================================================
    print("\n" + "=" * 78)
    print(f"[4] A 内远期收益 Top 20")
    print("=" * 78)
    top = fwd.sort_values("fwd_ret", ascending=False).head(20).copy()
    top["fwd_ret"] = (top["fwd_ret"] * 100).round(1)
    top["max_ret"] = (top["max_ret"] * 100).round(1)
    print(top[["ts_code", "fwd_ret", "max_ret", "IN_L1", "IN_HUOCHE"]].to_string(index=False))

    print(f"\n[4] A 内远期收益 Bottom 10")
    bot = fwd.sort_values("fwd_ret").head(10).copy()
    bot["fwd_ret"] = (bot["fwd_ret"] * 100).round(1)
    bot["max_ret"] = (bot["max_ret"] * 100).round(1)
    print(bot[["ts_code", "fwd_ret", "max_ret", "IN_L1", "IN_HUOCHE"]].to_string(index=False))

    # ========================================================================
    # 保存
    # ========================================================================
    out = OUTPUT_DIR / f"early_forward_{args.early}_to_{args.confirm}.csv"
    fwd_save = fwd.merge(early_res[["ts_code", "RPS_120", "streak_above_ma20",
                                    "seg_ret", "vs_ma60"]], on="ts_code", how="left")
    fwd_save.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  明细: {out}")


if __name__ == "__main__":
    main()
