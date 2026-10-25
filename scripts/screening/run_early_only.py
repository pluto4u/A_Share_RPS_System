"""
单独跑早期启涨扫描 (使用 parquet 缓存，秒级)
用法: py -3 run_early_only.py --date 20260417
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
from pathlib import Path

from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig, print_early_launch_summary,
)
from rps_analyzer.codes_csv import write_codes_csv

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--rps-min", type=float, default=70.0)
    parser.add_argument("--rps-max", type=float, default=100.0)
    parser.add_argument("--streak-min", type=int, default=3)
    parser.add_argument("--streak-max", type=int, default=12)
    parser.add_argument("--seg-ret-min", type=float, default=0.05)
    parser.add_argument("--seg-ret-max", type=float, default=0.45)
    parser.add_argument("--vs-ma60-max", type=float, default=0.40)
    parser.add_argument("--bo-vol-x", type=float, default=1.3)
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args()

    print(f"[读] {CACHE_PATH}")
    t0 = time.time()
    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    print(f"  耗时 {time.time()-t0:.1f}s, {df['ts_code'].nunique()} 只\n")

    cfg = EarlyLaunchConfig(
        rps_min=args.rps_min, rps_max=args.rps_max,
        above_ma20_min_days=args.streak_min, above_ma20_max_days=args.streak_max,
        seg_ret_min=args.seg_ret_min, seg_ret_max=args.seg_ret_max,
        vs_ma60_max=args.vs_ma60_max, bo_vol_x=args.bo_vol_x,
    )
    t1 = time.time()
    res = screen_early_launch(df, target_date=args.date, config=cfg, verbose=True)
    print(f"\n  扫描耗时: {time.time()-t1:.1f}s")
    print_early_launch_summary(res, top_n=args.top_n)

    if not res.empty:
        target = args.date or df["trade_date"].max().strftime("%Y%m%d")
        path = OUTPUT_DIR / f"tdx_screener_{target}_early.csv"
        res.to_csv(path, index=False, encoding="utf-8-sig")
        codes_path = OUTPUT_DIR / f"tdx_screener_{target}_early_codes.csv"
        write_codes_csv(codes_path, res["ts_code"])
        print(f"\n  完整: {path}\n  代码: {codes_path}")


if __name__ == "__main__":
    main()
