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

from rps_analyzer.codes_csv import write_codes_csv
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.weekly_trend_screener import WeeklyTrendConfig, WeeklyTrendScreener


OUTPUT_DIR = Path("output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly continuous trend pool")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="TDX vipdoc path or sh/sz lday path")
    parser.add_argument("--date", default=None, help="target date YYYYMMDD, default latest")
    parser.add_argument("--min-days", type=int, default=650, help="minimum daily bars")
    parser.add_argument("--rps120w", type=float, default=90.0, help="minimum weekly RPS120")
    parser.add_argument("--ma-rising-weeks", type=int, default=3, help="MA10W/MA20W rising weeks")
    parser.add_argument("--near-high", type=float, default=0.75, help="minimum 52-week close/high ratio")
    parser.add_argument("--max-vs-ma20w", type=float, default=0.35, help="max weekly close/MA20W - 1")
    parser.add_argument("--max-ret4w", type=float, default=0.60, help="max 4-week return")
    parser.add_argument("--min-weekly-amount", type=float, default=2e8, help="minimum avg weekly amount")
    parser.add_argument("--a-vs-ma20d", type=float, default=0.12, help="Tier A max daily close/MA20D - 1")
    parser.add_argument("--a-ret20", type=float, default=0.35, help="Tier A max daily 20-day return")
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
    print(f"  elapsed: {t_read:.1f}s\n")

    config = WeeklyTrendConfig(
        rps120w_min=args.rps120w,
        ma_rising_weeks=args.ma_rising_weeks,
        near_high_ratio_min=args.near_high,
        max_vs_ma20w=args.max_vs_ma20w,
        max_ret4w=args.max_ret4w,
        min_weekly_amount=args.min_weekly_amount,
        a_pullback_vs_ma20d_max=args.a_vs_ma20d,
        a_ret20_max=args.a_ret20,
    )
    screener = WeeklyTrendScreener(config)

    print("=" * 70)
    print("Step 2: Weekly continuous trend pool")
    print("=" * 70)
    t1 = time.time()
    result = screener.screen(df, target_date=args.date, verbose=True)
    t_screen = time.time() - t1

    target = args.date or df["trade_date"].max().strftime("%Y%m%d")

    print("\n" + "=" * 70)
    print("Weekly Trend Result")
    print("=" * 70)
    if result.empty:
        print("  no stocks selected")
    else:
        display_cols = [
            "ts_code",
            "TREND_TIER",
            "close",
            "W_RPS_120",
            "near_high_52w",
            "vs_ma20w",
            "ret4w",
            "vs_ma20d",
            "ret20d",
            "trend_score",
        ]
        print(result[display_cols].head(80).to_string(index=False, float_format="%.3f"))
        print(f"\n  total: {len(result)}")
        print(f"  Tier A: {(result['TREND_TIER'] == 'A').sum()}")
        print(f"  Tier B: {(result['TREND_TIER'] == 'B').sum()}")

        full_path = OUTPUT_DIR / f"weekly_trend_{target}.csv"
        codes_path = OUTPUT_DIR / f"weekly_trend_{target}_codes.csv"
        result.to_csv(full_path, index=False, encoding="utf-8-sig")
        write_codes_csv(codes_path, result["ts_code"])
        print(f"  full: {full_path}")
        print(f"  codes: {codes_path}")

        for tier in ["A", "B"]:
            tier_df = result[result["TREND_TIER"] == tier]
            if tier_df.empty:
                continue
            tier_path = OUTPUT_DIR / f"weekly_trend_{target}_{tier}.csv"
            tier_codes_path = OUTPUT_DIR / f"weekly_trend_{target}_{tier}_codes.csv"
            tier_df.to_csv(tier_path, index=False, encoding="utf-8-sig")
            write_codes_csv(tier_codes_path, tier_df["ts_code"])
            print(f"  tier {tier}: {tier_path}")
            print(f"  tier {tier} codes: {tier_codes_path}")

    print(f"\n{'=' * 70}")
    print(f"elapsed: read={t_read:.1f}s screen={t_screen:.1f}s total={t_read + t_screen:.1f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
