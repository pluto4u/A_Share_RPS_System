"""
================================================================================
通达信 RPS 选股 — 一键执行脚本
================================================================================
读取本地通达信数据 -> 计算 RPS -> 运行选股策略 -> 输出结果

用法:
    py -3 run_tdx_screener.py
    py -3 run_tdx_screener.py --date 20260305
    py -3 run_tdx_screener.py --rps-sum 185 --near-high 0.85
    py -3 run_tdx_screener.py --diagnose 600519.SH
"""

import time
import argparse
import pandas as pd
from pathlib import Path

from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_stock_screener import (
    TdxStockScreener, TdxScreenerConfig, print_diagnosis,
)

OUTPUT_DIR = Path("output")


def main():
    parser = argparse.ArgumentParser(description="通达信 RPS 选股一键脚本")
    parser.add_argument("--tdx-dir", default=r"D:\tdx\vipdoc", help="通达信 vipdoc 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD (默认最新)")
    parser.add_argument("--rps-sum", type=float, default=180, help="RPS120+RPS250 阈值 (默认180)")
    parser.add_argument("--near-high", type=float, default=0.80, help="接近新高比例 (默认0.80)")
    parser.add_argument("--diagnose", default=None, help="诊断指定股票代码 (如 600519.SH)")
    parser.add_argument("--save-data", action="store_true", help="保存中间数据到 parquet")
    parser.add_argument("--min-days", type=int, default=250, help="最少交易天数 (默认250)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    # =========================================================================
    # Step 1: 读取通达信数据
    # =========================================================================
    print("=" * 70)
    print("Step 1: 读取通达信本地数据")
    print("=" * 70)
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    t_read = time.time() - t0
    print(f"  耗时: {t_read:.1f}s\n")

    # =========================================================================
    # Step 2: 计算 RPS
    # =========================================================================
    print("=" * 70)
    print("Step 2: 计算 RPS_120, RPS_250")
    print("=" * 70)
    t1 = time.time()
    df = calculate_rps(df, periods=[120, 250], verbose=True)
    t_rps = time.time() - t1
    print(f"  耗时: {t_rps:.1f}s\n")

    if args.save_data:
        data_path = OUTPUT_DIR / "tdx_stock_daily_with_rps.parquet"
        df.to_parquet(data_path, index=False)
        print(f"  数据已保存: {data_path}\n")

    # =========================================================================
    # Step 3: 运行选股
    # =========================================================================
    print("=" * 70)
    print("Step 3: 通达信 RPS 选股")
    print("=" * 70)

    config = TdxScreenerConfig(
        rps_sum_threshold=args.rps_sum,
        near_high_ratio=args.near_high,
    )
    screener = TdxStockScreener(config)

    if args.diagnose:
        print(f"\n诊断股票: {args.diagnose}")
        diag = screener.diagnose(df, args.diagnose, args.date)
        if "error" in diag:
            print(f"  错误: {diag['error']}")
        else:
            print_diagnosis(diag)
        return

    t2 = time.time()
    result = screener.screen(df, target_date=args.date, verbose=True)
    t_screen = time.time() - t2

    # =========================================================================
    # Step 4: 输出结果
    # =========================================================================
    print("\n" + "=" * 70)
    print("选股结果")
    print("=" * 70)

    if result.empty:
        print("  无股票入选")
    else:
        display = result[["ts_code", "close", "RPS_120", "RPS_250"]].copy()
        display = display.sort_values("RPS_120", ascending=False)
        display.columns = ["代码", "收盘价", "RPS120", "RPS250"]
        print(display.to_string(index=False, float_format="%.2f"))
        print(f"\n  共 {len(result)} 只股票入选")

        target = args.date or df["trade_date"].max().strftime("%Y%m%d")

        full_path = OUTPUT_DIR / f"tdx_screener_{target}.csv"
        result.to_csv(full_path, index=False, encoding="utf-8-sig")

        codes_path = OUTPUT_DIR / f"tdx_screener_{target}_codes.csv"
        result[["ts_code"]].drop_duplicates().to_csv(codes_path, index=False, encoding="utf-8-sig")

        print(f"  完整结果: {full_path}")
        print(f"  代码清单: {codes_path}")

    # =========================================================================
    # 汇总
    # =========================================================================
    print(f"\n{'='*70}")
    print(f"耗时汇总: 读取={t_read:.1f}s  RPS计算={t_rps:.1f}s  选股={t_screen:.1f}s  总计={t_read+t_rps+t_screen:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
