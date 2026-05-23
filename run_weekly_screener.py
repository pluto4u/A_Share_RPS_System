"""
================================================================================
周线 RPS 吸筹突破选股 — 一键执行脚本
================================================================================
读取本地通达信数据 -> 日线转周线 -> 计算周线 RPS -> 6 条件选股 -> 输出结果

用法:
    py -3 run_weekly_screener.py
    py -3 run_weekly_screener.py --date 20260305
    py -3 run_weekly_screener.py --diagnose 601126.SH
    py -3 run_weekly_screener.py --diagnose 688048.SH --diagnose 002491.SZ
"""

import time
import argparse
import pandas as pd
from pathlib import Path

from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.weekly_rps_screener import (
    WeeklyRpsScreener, WeeklyScreenerConfig,
)
from rps_analyzer.codes_csv import write_codes_csv

OUTPUT_DIR = Path("output")


def main():
    parser = argparse.ArgumentParser(description="周线 RPS 吸筹突破选股")
    parser.add_argument("--tdx-dir", default=r"D:\tdx\vipdoc", help="通达信 vipdoc 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD (默认最新)")
    parser.add_argument("--diagnose", action="append", default=None, help="诊断指定股票 (可多次使用)")
    parser.add_argument("--min-days", type=int, default=300, help="最少交易天数 (默认300)")
    parser.add_argument("--rps-threshold", type=float, default=70.0, help="周 RPS_120 翻红阈值 (默认70)")
    parser.add_argument("--accum-range", type=float, default=0.35, help="吸筹区间振幅上限 (默认0.35)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("Step 1: 读取通达信本地数据")
    print("=" * 70)
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    t_read = time.time() - t0
    print(f"  耗时: {t_read:.1f}s\n")

    config = WeeklyScreenerConfig(
        rps_threshold=args.rps_threshold,
        accum_range_max=args.accum_range,
    )
    screener = WeeklyRpsScreener(config)

    if args.diagnose:
        print("=" * 70)
        print("诊断模式")
        print("=" * 70)
        for code in args.diagnose:
            diag = screener.diagnose(df, code, args.date)
            if "error" in diag:
                print(f"\n  {code}: {diag['error']}")
            else:
                print(f"\n{'='*60}")
                print(f"股票: {diag['ts_code']}  日期: {diag['trade_date']}")
                print(f"收盘: {diag['close']:.2f}  W_RPS_120: {diag.get('W_RPS_120', 'N/A'):.1f}  MA_120W: {diag.get('MA_120W', 'N/A'):.2f}")
                print(f"{'-'*60}")
                for name, passed in diag["conditions"].items():
                    status = "PASS" if passed else "FAIL"
                    print(f"  [{status}] {name}")
                print(f"{'-'*60}")
                final = "PASS" if diag["all_pass"] else "FAIL"
                print(f"  Final: {final}")
                print(f"{'='*60}")
        return

    print("=" * 70)
    print("Step 2: 周线选股")
    print("=" * 70)
    t1 = time.time()
    result = screener.screen(df, target_date=args.date, verbose=True)
    t_screen = time.time() - t1

    print("\n" + "=" * 70)
    print("选股结果")
    print("=" * 70)

    if result.empty:
        print("  无股票入选")
    else:
        display = result[["ts_code", "close", "W_RPS_120"]].copy()
        display = display.sort_values("W_RPS_120", ascending=False)
        display.columns = ["代码", "收盘价", "W_RPS120"]
        print(display.to_string(index=False, float_format="%.2f"))
        print(f"\n  共 {len(result)} 只股票入选")

        target = args.date or df["trade_date"].max().strftime("%Y%m%d")

        full_path = OUTPUT_DIR / f"weekly_screener_{target}.csv"
        result.to_csv(full_path, index=False, encoding="utf-8-sig")

        codes_path = OUTPUT_DIR / f"weekly_screener_{target}_codes.csv"
        write_codes_csv(codes_path, result["ts_code"])

        print(f"  完整结果: {full_path}")
        print(f"  代码清单: {codes_path}")

    print(f"\n{'='*70}")
    print(f"耗时汇总: 读取={t_read:.1f}s  选股={t_screen:.1f}s  总计={t_read+t_screen:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
