"""
================================================================================
RPS 增强选股 — 一键执行脚本
================================================================================
整合日线 + 周线 + 行业 RPS + 信号跟踪 + 自动 TDX 板块

流程:
  1. 读取通达信数据
  2. 计算 RPS (日线 120/250)
  3. 日线选股 Layer 1 + Layer 2
  4. 周线选股
  5. 日线 x 周线交集 -> 高确定性信号
  6. 行业 RPS 过滤 -> 强行业中的强个股
  7. 信号跟踪 (新进入 / 退出 / 持续)
  8. 自动写入 TDX 板块文件

用法:
    py -3 run_rps_enhanced.py
    py -3 run_rps_enhanced.py --date 20260318
    py -3 run_rps_enhanced.py --no-blk           # 不写 TDX 板块
    py -3 run_rps_enhanced.py --ind-rps 75       # 行业 RPS 阈值
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import time
import argparse
import pandas as pd
from pathlib import Path

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_stock_screener import (
    TdxStockScreener, TdxScreenerConfig, screen_layer2,
)
from rps_analyzer.weekly_rps_screener import WeeklyRpsScreener, WeeklyScreenerConfig
from rps_analyzer.industry_rps import load_industry_map, calculate_industry_rps
from rps_analyzer.signal_tracker import track_signals, print_tracking_report
from rps_analyzer.codes_csv import write_codes_csv
from csv_to_tdx_blk import convert_csv_to_blk

OUTPUT_DIR = Path("output")
DEFAULT_BLK_DIR = Path(r"C:/new_tdx/T0002/blocknew")


def save_result(result: pd.DataFrame, name: str, target: str):
    """保存完整结果 + 代码清单，返回 codes csv 路径"""
    full_path = OUTPUT_DIR / f"{name}_{target}.csv"
    result.to_csv(full_path, index=False, encoding="utf-8-sig")

    codes_path = OUTPUT_DIR / f"{name}_{target}_codes.csv"
    write_codes_csv(codes_path, result["ts_code"])

    return codes_path


def main():
    parser = argparse.ArgumentParser(description="RPS 增强选股 (日线+周线+行业+跟踪+板块)")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 路径，或 sh/sz lday 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD (默认最新)")
    parser.add_argument("--min-days", type=int, default=300, help="最少交易天数 (默认300)")
    parser.add_argument("--rps-sum", type=float, default=180, help="日线 RPS 阈值 (默认180)")
    parser.add_argument("--ind-rps", type=float, default=70.0, help="行业 RPS 阈值 (默认70)")
    parser.add_argument("--no-blk", action="store_true", help="不自动写 TDX 板块")
    parser.add_argument("--blk-dir", default=str(DEFAULT_BLK_DIR), help="TDX 板块输出目录")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    timings = {}

    # =========================================================================
    # Step 1: 读取数据
    # =========================================================================
    print("=" * 70)
    print("Step 1: 读取通达信本地数据")
    print("=" * 70)
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    timings["读取"] = time.time() - t0
    print(f"  耗时: {timings['读取']:.1f}s\n")

    # =========================================================================
    # Step 2: 计算日线 RPS
    # =========================================================================
    print("=" * 70)
    print("Step 2: 计算 RPS_120, RPS_250")
    print("=" * 70)
    t1 = time.time()
    df = calculate_rps(df, periods=[120, 250], verbose=True)
    timings["RPS"] = time.time() - t1
    print(f"  耗时: {timings['RPS']:.1f}s\n")

    target = args.date or df["trade_date"].max().strftime("%Y%m%d")

    # =========================================================================
    # Step 3: 日线选股 Layer 1
    # =========================================================================
    print("=" * 70)
    print("Step 3: 日线选股 Layer 1")
    print("=" * 70)
    t2 = time.time()
    config = TdxScreenerConfig(rps_sum_threshold=args.rps_sum)
    screener = TdxStockScreener(config)
    l1_result = screener.screen(df, target_date=args.date, verbose=True)
    timings["L1选股"] = time.time() - t2

    l1_codes_path = None
    if not l1_result.empty:
        print(f"\n  Layer 1: {len(l1_result)} 只入选")
        l1_codes_path = save_result(l1_result, "tdx_screener", target)

    # =========================================================================
    # Step 4: 日线选股 Layer 2
    # =========================================================================
    l2_result = pd.DataFrame()
    if not l1_result.empty:
        print("\n" + "=" * 70)
        print("Step 4: 日线选股 Layer 2 (ZHUTU 策略)")
        print("=" * 70)
        t3 = time.time()
        layer1_codes = l1_result["ts_code"].tolist()
        l2_result = screen_layer2(df, layer1_codes, target_date=args.date, verbose=True)
        timings["L2选股"] = time.time() - t3

        if not l2_result.empty:
            print(f"\n  Layer 2: {len(l2_result)} 只入选")
            save_result(l2_result, "tdx_screener_L2", target)
    else:
        timings["L2选股"] = 0

    # =========================================================================
    # Step 5: 周线选股
    # =========================================================================
    print("\n" + "=" * 70)
    print("Step 5: 周线选股 (吸筹突破)")
    print("=" * 70)
    t4 = time.time()
    weekly_screener = WeeklyRpsScreener()
    weekly_result = weekly_screener.screen(df, target_date=args.date, verbose=True)
    timings["周线选股"] = time.time() - t4

    if not weekly_result.empty:
        print(f"\n  周线: {len(weekly_result)} 只入选")
        save_result(weekly_result, "weekly_screener", target)

    # =========================================================================
    # Step 6: 日线 x 周线交集
    # =========================================================================
    print("\n" + "=" * 70)
    print("Step 6: 日线 x 周线交集 (高确定性)")
    print("=" * 70)

    intersect_result = pd.DataFrame()
    if not l2_result.empty and not weekly_result.empty:
        l2_codes = set(l2_result["ts_code"])
        weekly_codes = set(weekly_result["ts_code"])
        common = l2_codes & weekly_codes

        if common:
            intersect_result = l2_result[l2_result["ts_code"].isin(common)].copy()
            intersect_result["W_RPS_120"] = intersect_result["ts_code"].map(
                weekly_result.set_index("ts_code")["W_RPS_120"]
            )
            save_result(intersect_result, "intersect_DW", target)
            print(f"  L2({len(l2_codes)}) x 周线({len(weekly_codes)}) => 交集: {len(common)} 只")
            display = intersect_result[["ts_code", "close", "RPS_120", "RPS_250"]].copy()
            display = display.sort_values("RPS_120", ascending=False)
            display.columns = ["代码", "收盘价", "RPS120", "RPS250"]
            print(display.to_string(index=False, float_format="%.2f"))
        else:
            print("  L2 和周线选股无交集")
    elif not l1_result.empty and not weekly_result.empty:
        l1_codes = set(l1_result["ts_code"])
        weekly_codes = set(weekly_result["ts_code"])
        common = l1_codes & weekly_codes

        if common:
            intersect_result = l1_result[l1_result["ts_code"].isin(common)].copy()
            save_result(intersect_result, "intersect_DW", target)
            print(f"  L1({len(l1_codes)}) x 周线({len(weekly_codes)}) => 交集: {len(common)} 只")
        else:
            print("  L1 和周线选股无交集")
    else:
        print("  日线或周线无入选，跳过交集")

    # =========================================================================
    # Step 7: 行业 RPS
    # =========================================================================
    print("\n" + "=" * 70)
    print("Step 7: 行业 RPS 分析")
    print("=" * 70)
    t5 = time.time()

    try:
        ind_map = load_industry_map()
        df_with_ind = calculate_industry_rps(df, ind_map, period=120)
        timings["行业RPS"] = time.time() - t5

        target_dt = pd.Timestamp(target)
        latest_ind = df_with_ind[df_with_ind["trade_date"] == target_dt]

        if not latest_ind.empty:
            ind_summary = (
                latest_ind.groupby("industry")["IND_RPS"]
                .first()
                .sort_values(ascending=False)
            )
            top_n = 10
            print(f"\n  行业 RPS 前 {top_n}:")
            for ind, rps in ind_summary.head(top_n).items():
                cnt = (latest_ind["industry"] == ind).sum()
                print(f"    {ind}: IND_RPS={rps:.1f}  ({cnt} 只)")

            if not l2_result.empty:
                l2_with_ind = l2_result.merge(
                    latest_ind[["ts_code", "industry", "IND_RPS"]].drop_duplicates("ts_code"),
                    on="ts_code",
                    how="left",
                )
                strong_ind = l2_with_ind[l2_with_ind["IND_RPS"] >= args.ind_rps]
                if not strong_ind.empty:
                    save_result(strong_ind, "strong_ind_L2", target)
                    print(f"\n  L2 + 强行业(IND_RPS>={args.ind_rps}): {len(strong_ind)} 只")
                    display = strong_ind[["ts_code", "close", "RPS_120", "industry", "IND_RPS"]].copy()
                    display = display.sort_values("IND_RPS", ascending=False)
                    display.columns = ["代码", "收盘价", "RPS120", "行业", "行业RPS"]
                    print(display.to_string(index=False, float_format="%.1f"))
                else:
                    print(f"\n  L2 中无行业 RPS >= {args.ind_rps} 的票")
    except Exception as e:
        timings["行业RPS"] = time.time() - t5
        print(f"  行业 RPS 计算失败: {e}")

    # =========================================================================
    # Step 8: 信号跟踪
    # =========================================================================
    print("\n" + "=" * 70)
    print("Step 8: 信号跟踪 (新进入 / 退出)")
    print("=" * 70)

    tracking = track_signals(OUTPUT_DIR, target)
    print_tracking_report(tracking)

    # =========================================================================
    # Step 9: 自动写 TDX 板块
    # =========================================================================
    if not args.no_blk:
        print("\n" + "=" * 70)
        print("Step 9: 写入 TDX 板块")
        print("=" * 70)
        blk_dir = Path(args.blk_dir)

        blk_files = [
            (f"tdx_screener_{target}_codes.csv", f"RPS_L1_{target}"),
            (f"tdx_screener_L2_{target}_codes.csv", f"RPS_L2_{target}"),
            (f"intersect_DW_{target}_codes.csv", f"RPS_DW_{target}"),
            (f"strong_ind_L2_{target}_codes.csv", f"RPS_IND_{target}"),
        ]

        for csv_name, blk_name in blk_files:
            csv_path = OUTPUT_DIR / csv_name
            if csv_path.exists():
                try:
                    blk_path, count = convert_csv_to_blk(csv_path, blk_dir, blk_name)
                    print(f"  {blk_name}.blk -> {count} 只  ({blk_path})")
                except Exception as e:
                    print(f"  {blk_name}: 失败 ({e})")

    # =========================================================================
    # 汇总
    # =========================================================================
    total = sum(timings.values())
    print(f"\n{'='*70}")
    timing_str = "  ".join(f"{k}={v:.1f}s" for k, v in timings.items())
    print(f"耗时: {timing_str}  总计={total:.1f}s")

    print(f"\n结果汇总:")
    print(f"  L1 日线:     {len(l1_result)} 只")
    print(f"  L2 日线:     {len(l2_result)} 只")
    print(f"  周线:        {len(weekly_result)} 只")
    print(f"  日周交集:    {len(intersect_result)} 只")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
