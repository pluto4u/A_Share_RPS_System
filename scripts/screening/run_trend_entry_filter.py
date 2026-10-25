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

from rps_analyzer.codes_csv import write_codes_csv
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.trend_entry_filter import TrendEntryConfig, filter_trend_entries
from rps_analyzer.weekly_trend_screener import WeeklyTrendConfig, WeeklyTrendScreener


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
    parser = argparse.ArgumentParser(description="Filter weekly trend pool into daily entry zones")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="TDX vipdoc path or sh/sz lday path")
    parser.add_argument("--date", default=None, help="target date YYYYMMDD, default latest")
    parser.add_argument("--min-days", type=int, default=650, help="minimum daily bars")

    parser.add_argument("--rps120w", type=float, default=90.0, help="minimum weekly RPS120")
    parser.add_argument("--ma-rising-weeks", type=int, default=3, help="MA10W/MA20W rising weeks")
    parser.add_argument("--near-high", type=float, default=0.75, help="minimum 52-week close/high ratio")
    parser.add_argument("--max-vs-ma20w", type=float, default=0.35, help="max weekly close/MA20W - 1")
    parser.add_argument("--max-ret4w", type=float, default=0.60, help="max 4-week return")
    parser.add_argument("--min-weekly-amount", type=float, default=2e8, help="minimum avg weekly amount")

    parser.add_argument("--ready-vs-ma20d", type=float, default=0.08, help="READY max close/MA20D - 1")
    parser.add_argument("--watch-vs-ma20d", type=float, default=0.12, help="WATCH max close/MA20D - 1")
    parser.add_argument("--ready-ret20", type=float, default=0.30, help="READY max 20-day return")
    parser.add_argument("--watch-ret20", type=float, default=0.40, help="WATCH max 20-day return")
    parser.add_argument("--max-ret5", type=float, default=0.16, help="max 5-day return")
    parser.add_argument("--ready-vol3", type=float, default=1.05, help="READY max vol3/vol20")
    parser.add_argument("--watch-vol3", type=float, default=1.30, help="WATCH max vol3/vol20")
    parser.add_argument("--ready-range5", type=float, default=0.16, help="READY max 5-day range")
    parser.add_argument("--watch-range5", type=float, default=0.22, help="WATCH max 5-day range")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=" * 70)
    print("Step 1: Read TDX local daily data")
    print("=" * 70)
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    t_read = time.time() - t0
    actual_date = _actual_target_date(df, args.date)
    print(f"  actual target date: {actual_date}")
    print(f"  elapsed: {t_read:.1f}s\n")

    weekly_cfg = WeeklyTrendConfig(
        rps120w_min=args.rps120w,
        ma_rising_weeks=args.ma_rising_weeks,
        near_high_ratio_min=args.near_high,
        max_vs_ma20w=args.max_vs_ma20w,
        max_ret4w=args.max_ret4w,
        min_weekly_amount=args.min_weekly_amount,
        a_pullback_vs_ma20d_max=args.watch_vs_ma20d,
        a_ret20_max=args.watch_ret20,
    )
    entry_cfg = TrendEntryConfig(
        max_ready_vs_ma20d=args.ready_vs_ma20d,
        max_watch_vs_ma20d=args.watch_vs_ma20d,
        max_ready_ret20d=args.ready_ret20,
        max_watch_ret20d=args.watch_ret20,
        max_ret5d=args.max_ret5,
        max_vol3_ratio_ready=args.ready_vol3,
        max_vol3_ratio_watch=args.watch_vol3,
        max_range5_ready=args.ready_range5,
        max_range5_watch=args.watch_range5,
    )

    print("=" * 70)
    print("Step 2: Build weekly trend pool")
    print("=" * 70)
    t1 = time.time()
    trend_pool = WeeklyTrendScreener(weekly_cfg).screen(df, target_date=actual_date, verbose=True)
    t_trend = time.time() - t1

    print("\n" + "=" * 70)
    print("Step 3: Filter daily entry zones")
    print("=" * 70)
    t2 = time.time()
    result = filter_trend_entries(
        df,
        trend_pool,
        target_date=actual_date,
        config=entry_cfg,
        verbose=True,
    )
    t_entry = time.time() - t2

    trend_path = OUTPUT_DIR / f"weekly_trend_{actual_date}.csv"
    trend_codes_path = OUTPUT_DIR / f"weekly_trend_{actual_date}_codes.csv"
    if not trend_pool.empty:
        trend_pool.to_csv(trend_path, index=False, encoding="utf-8-sig")
        write_codes_csv(trend_codes_path, trend_pool["ts_code"])

    print("\n" + "=" * 70)
    print("Trend Entry Result")
    print("=" * 70)
    if result.empty:
        print("  no entry candidates")
    else:
        display_cols = [
            "ts_code",
            "ENTRY_TIER",
            "TREND_TIER",
            "close",
            "W_RPS_120",
            "vs_ma20d",
            "ret5d",
            "ret20d",
            "vol3_ratio",
            "range5",
            "pullback_10d",
            "entry_score",
        ]
        print(result[display_cols].head(80).to_string(index=False, float_format="%.3f"))
        print(f"\n  total: {len(result)}")
        print(f"  READY: {(result['ENTRY_TIER'] == 'READY').sum()}")
        print(f"  WATCH: {(result['ENTRY_TIER'] == 'WATCH').sum()}")

        full_path = OUTPUT_DIR / f"trend_entry_{actual_date}.csv"
        codes_path = OUTPUT_DIR / f"trend_entry_{actual_date}_codes.csv"
        result.to_csv(full_path, index=False, encoding="utf-8-sig")
        write_codes_csv(codes_path, result["ts_code"])
        print(f"  full: {full_path}")
        print(f"  codes: {codes_path}")

        for tier in ["READY", "WATCH"]:
            tier_df = result[result["ENTRY_TIER"] == tier]
            if tier_df.empty:
                continue
            tier_path = OUTPUT_DIR / f"trend_entry_{actual_date}_{tier}.csv"
            tier_codes_path = OUTPUT_DIR / f"trend_entry_{actual_date}_{tier}_codes.csv"
            tier_df.to_csv(tier_path, index=False, encoding="utf-8-sig")
            write_codes_csv(tier_codes_path, tier_df["ts_code"])
            print(f"  {tier}: {tier_path}")
            print(f"  {tier} codes: {tier_codes_path}")

    print(f"\n{'=' * 70}")
    print(
        f"elapsed: read={t_read:.1f}s trend={t_trend:.1f}s entry={t_entry:.1f}s "
        f"total={t_read + t_trend + t_entry:.1f}s"
    )
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
