"""
================================================================================
静默筑底 · 低位再启动 —— 每日扫描入口
================================================================================
流程: 读取通达信本地日线 -> 计算 RPS -> 静默筑底扫描 -> 输出 CSV / 代码清单

用法:
    py -3 run_quiet_base.py
    py -3 run_quiet_base.py --date 20260729
    py -3 run_quiet_base.py --cache output/cache_tdx_with_rps.parquet   # 免读盘, 调参用
    py -3 run_quiet_base.py --exclude-boards 688,300 --exclude-codes output/st_list.csv

输出 (output/):
    quiet_base_{date}.csv / _codes.csv              全部入选
    quiet_base_{date}_BUY.csv / _BUY_codes.csv      可买入档
    quiet_base_{date}_WATCH.csv / _WATCH_codes.csv  观察档
代码清单可直接经 csv_to_tdx_blk.py 导入通达信自定义板块。
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
import time
from pathlib import Path

import pandas as pd

from rps_analyzer.codes_csv import read_codes_csv, write_codes_csv
from rps_analyzer.quiet_base_screener import (
    QuietBaseConfig,
    print_quiet_base_summary,
    screen_quiet_base,
)
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market


OUTPUT_DIR = Path("output")


def _actual_target_date(df: pd.DataFrame, target_date: str | None) -> str:
    dates = df["trade_date"].unique()
    if target_date is None:
        return pd.Timestamp(dates.max()).strftime("%Y%m%d")
    td = pd.Timestamp(target_date)
    valid = dates[dates <= td]
    if len(valid) == 0:
        return target_date
    return pd.Timestamp(valid.max()).strftime("%Y%m%d")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="静默筑底 · 低位再启动选股（每日扫描）")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 或 sh/sz lday 路径")
    parser.add_argument("--cache", default=None,
                        help="改从含 RPS 的 parquet/csv 读取，跳过读盘与 RPS 计算（调参用）")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD，默认最新")
    parser.add_argument("--min-days", type=int, default=350, help="最少日线根数")
    parser.add_argument("--save-cache", default=None, help="把带 RPS 的日线存为 parquet 供下次 --cache")

    parser.add_argument("--exclude-boards", default=None,
                        help="排除板块前缀，逗号分隔，如 688,300")
    parser.add_argument("--exclude-codes", default=None,
                        help="排除清单 CSV（单列代码，如 ST / 退市股）")

    # --- 窗口结构 ---
    parser.add_argument("--base-window", type=int, default=42, help="横盘基底窗口(交易日)")
    parser.add_argument("--recent-gap", type=int, default=20, help="近期段长度(交易日)")
    parser.add_argument("--drop-lookback", type=int, default=250, help="下跌段/前期高点回看")

    # --- A 调整充分 ---
    parser.add_argument("--min-drawdown", type=float, default=0.30, help="前期高点→基底最低 回撤下限")
    parser.add_argument("--min-below-pre-high", type=float, default=0.25, help="今收须低于前期高点的幅度")

    # --- B 横盘充分 / 筹码干净 ---
    parser.add_argument("--box-range-max", type=float, default=0.22,
                        help="箱体振幅上限（箱体=基底期收盘价 q90/q10）")
    parser.add_argument("--base-drift-max", type=float, default=0.10, help="基底期漂移上限")
    parser.add_argument("--vol-dry-max", type=float, default=0.85, help="基底均量/下跌段均量 上限")
    parser.add_argument("--close-cv-max", type=float, default=0.09, help="基底期收盘价 std/mean 上限")

    # --- C 量价关系 / 可买入 ---
    parser.add_argument("--ma20-streak-min", type=int, default=2, help="连续站上 MA20 天数下限")
    parser.add_argument("--ma20-streak-max", type=int, default=25, help="连续站上 MA20 天数上限")
    parser.add_argument("--box-ext-max", type=float, default=0.12, help="距箱体上沿涨幅上限")
    parser.add_argument("--vol-expand-x", type=float, default=1.8, help="近期最大量/基底均量 下限")
    parser.add_argument("--max-ret5", type=float, default=0.15, help="5 日涨幅上限")
    parser.add_argument("--max-ret20", type=float, default=0.35, help="20 日涨幅上限")
    parser.add_argument("--max-vs-ma20", type=float, default=0.10, help="close/MA20-1 上限")

    # --- D RPS 拐头 ---
    parser.add_argument("--rps-slope-lookback", type=int, default=20, help="RPS 抬升比较窗口")
    parser.add_argument("--rps-rise-min", type=float, default=10.0, help="RPS_120 抬升下限")
    parser.add_argument("--rps-floor", type=float, default=40.0, help="RPS_120 地板")
    parser.add_argument("--no-rps-floor", action="store_true", help="关闭 RPS 地板")

    # --- E 风控 ---
    parser.add_argument("--min-amount", type=float, default=8e7, help="近 20 日均额下限")
    parser.add_argument("--min-bars", type=int, default=300, help="K 线数下限(排除次新)")
    return parser


def _config_from_args(args: argparse.Namespace) -> QuietBaseConfig:
    return QuietBaseConfig(
        base_window=args.base_window,
        recent_gap=args.recent_gap,
        drop_lookback=args.drop_lookback,
        min_drawdown=args.min_drawdown,
        min_below_pre_high=args.min_below_pre_high,
        box_range_max=args.box_range_max,
        base_drift_max=args.base_drift_max,
        vol_dry_max=args.vol_dry_max,
        close_cv_max=args.close_cv_max,
        ma20_streak_min=args.ma20_streak_min,
        ma20_streak_max=args.ma20_streak_max,
        box_ext_max=args.box_ext_max,
        vol_expand_x=args.vol_expand_x,
        max_ret5=args.max_ret5,
        max_ret20=args.max_ret20,
        max_vs_ma20=args.max_vs_ma20,
        rps_slope_lookback=args.rps_slope_lookback,
        rps_rise_min=args.rps_rise_min,
        use_rps_floor=not args.no_rps_floor,
        rps_floor=args.rps_floor,
        min_amount=args.min_amount,
        min_bars=args.min_bars,
    )


def _load_daily(args: argparse.Namespace) -> pd.DataFrame:
    if args.cache:
        cache_path = Path(args.cache)
        if not cache_path.exists():
            raise FileNotFoundError(f"缓存不存在: {cache_path}")
        print(f"  从缓存读取: {cache_path}")
        df = (
            pd.read_parquet(cache_path)
            if cache_path.suffix == ".parquet"
            else pd.read_csv(cache_path)
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if "RPS_120" not in df.columns:
            print("  缓存缺少 RPS_120，现场计算 ...")
            df = calculate_rps(df, periods=[50, 120, 250], verbose=True)
        return df

    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    print("\n  计算 RPS ...")
    df = calculate_rps(df, periods=[50, 120, 250], verbose=True)
    if args.save_cache:
        save_path = Path(args.save_cache)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(save_path, index=False)
        print(f"  带 RPS 日线已缓存: {save_path}")
    return df


def _dump(result: pd.DataFrame, actual_date: str) -> None:
    full_path = OUTPUT_DIR / f"quiet_base_{actual_date}.csv"
    codes_path = OUTPUT_DIR / f"quiet_base_{actual_date}_codes.csv"
    result.to_csv(full_path, index=False, encoding="utf-8-sig")
    write_codes_csv(codes_path, result["ts_code"])
    print(f"  全部: {full_path}")
    print(f"  代码: {codes_path}")

    for tier, col in (("BUY", "BUY"), ("WATCH", "WATCH")):
        tier_df = result[result[col]]
        if tier_df.empty:
            continue
        tier_path = OUTPUT_DIR / f"quiet_base_{actual_date}_{tier}.csv"
        tier_codes_path = OUTPUT_DIR / f"quiet_base_{actual_date}_{tier}_codes.csv"
        tier_df.to_csv(tier_path, index=False, encoding="utf-8-sig")
        write_codes_csv(tier_codes_path, tier_df["ts_code"])
        print(f"  {tier}: {tier_path}")
        print(f"  {tier} 代码: {tier_codes_path}")


def main() -> None:
    args = build_parser().parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("Step 1: 读取日线数据 + RPS")
    print("=" * 70)
    t0 = time.time()
    df = _load_daily(args)
    t_read = time.time() - t0
    actual_date = _actual_target_date(df, args.date)
    print(f"  实际目标日: {actual_date}")
    print(f"  耗时: {t_read:.1f}s\n")

    exclude_prefixes = None
    if args.exclude_boards:
        exclude_prefixes = [p.strip() for p in args.exclude_boards.split(",") if p.strip()]
        print(f"  排除板块前缀: {exclude_prefixes}")

    exclude_codes = None
    if args.exclude_codes:
        exclude_codes = read_codes_csv(args.exclude_codes)
        print(f"  排除清单: {len(exclude_codes)} 只 ({args.exclude_codes})")

    print("=" * 70)
    print("Step 2: 静默筑底 · 低位再启动扫描")
    print("=" * 70)
    t1 = time.time()
    result = screen_quiet_base(
        df,
        target_date=actual_date,
        config=_config_from_args(args),
        exclude_codes=exclude_codes,
        exclude_prefixes=exclude_prefixes,
        verbose=True,
    )
    t_scan = time.time() - t1

    print("\n" + "=" * 70)
    print("选股结果")
    print("=" * 70)
    print_quiet_base_summary(result)
    if not result.empty:
        _dump(result, actual_date)

    print(f"\n{'=' * 70}")
    print(f"耗时: 读取={t_read:.1f}s 扫描={t_scan:.1f}s 合计={t_read + t_scan:.1f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
