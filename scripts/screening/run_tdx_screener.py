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

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market, normalize_tdx_dir
from rps_analyzer.price_adjust import default_gbbq_path, forward_adjust_market, load_gbbq
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_stock_screener import (
    TdxStockScreener, TdxScreenerConfig, print_diagnosis, screen_layer2,
)
from rps_analyzer.huoche_screener import (
    screen_huoche, HuocheConfig, print_huoche_summary,
)
from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig, print_early_launch_summary,
)
from rps_analyzer.super_strong import (
    screen_super_strong, SuperStrongConfig, print_super_strong_summary,
)
from rps_analyzer.signal_tracker import track_signals, print_tracking_report
from rps_analyzer.codes_csv import write_codes_csv

OUTPUT_DIR = Path("output")


def main():
    parser = argparse.ArgumentParser(description="通达信 RPS 选股一键脚本")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 路径，或 sh/sz lday 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD (默认最新)")
    parser.add_argument("--rps-sum", type=float, default=180, help="RPS120+RPS250 阈值 (默认180)")
    parser.add_argument("--near-high", type=float, default=0.80, help="接近新高比例 (默认0.80)")
    parser.add_argument("--diagnose", default=None, help="诊断指定股票代码 (如 600519.SH)")
    parser.add_argument("--save-data", action="store_true", help="保存中间数据到 parquet")
    parser.add_argument("--min-days", type=int, default=250, help="最少交易天数 (默认250)")
    parser.add_argument("--price-basis", choices=("qfq", "raw"), default="qfq",
                        help="qfq: 按目标日等比前复权 (默认, 除权跳空不计入涨幅/均线)；raw: 原始 .day 价格")
    parser.add_argument("--gbbq", default=None,
                        help="除权除息文件，默认 vipdoc 同级 T0002/hq_cache/gbbq")

    # 火车轨阈值（Step 6）
    parser.add_argument("--huoche-rps", type=float, default=90.0,
                        help="周线 RPS_120 阈值 (默认90, 越低越宽松)")
    parser.add_argument("--huoche-near-high", type=float, default=0.80,
                        help="周线接近 N 周新高比例 (默认0.80)")
    parser.add_argument("--huoche-near-high-period", type=int, default=120,
                        help="周线近高回看周数 (默认120)")
    parser.add_argument("--huoche-drawdown", type=float, default=0.25,
                        help="周线最大回撤上限 (默认0.25)")
    parser.add_argument("--huoche-first-weeks", type=int, default=8,
                        help="火车轨首次触发回看周数 (默认8)")
    parser.add_argument("--huoche-ma-trend-weeks", type=int, default=5,
                        help="MA20W 连续上升周数 (默认5)")

    # 早期启涨阈值（Step 7）
    parser.add_argument("--skip-early", action="store_true",
                        help="跳过 Step 7 早期启涨扫描")
    parser.add_argument("--early-rps-min", type=float, default=70.0)
    parser.add_argument("--early-rps-max", type=float, default=100.0)
    parser.add_argument("--early-streak-min", type=int, default=3)
    parser.add_argument("--early-streak-max", type=int, default=12)
    parser.add_argument("--early-seg-ret-min", type=float, default=0.05)
    parser.add_argument("--early-seg-ret-max", type=float, default=0.45)
    parser.add_argument("--early-vs-ma60-max", type=float, default=0.40)
    parser.add_argument("--early-bo-vol-x", type=float, default=1.3)

    # 极强 RPS 榜阈值（Step 8）
    parser.add_argument("--skip-super", action="store_true",
                        help="跳过 Step 8 极强 RPS 榜")
    parser.add_argument("--super-rps120-min", type=float, default=98.0)
    parser.add_argument("--super-rps250-min", type=float, default=98.0)
    parser.add_argument("--super-vs-ma60-max", type=float, default=0.60)
    parser.add_argument("--super-ret20-max", type=float, default=0.60)
    parser.add_argument("--super-rps50-premium", type=float, default=90.0,
                        help="Super premium RPS_50 下限")
    parser.add_argument("--super-vs-ma60-premium", type=float, default=0.25,
                        help="Super premium close/MA60-1 上限")
    parser.add_argument("--super-ret20-premium", type=float, default=0.30,
                        help="Super premium ret20 上限")

    # 信号跟踪（Step 9）
    parser.add_argument("--skip-track", action="store_true",
                        help="跳过 Step 9 信号 NEW/EXIT/HOLD 跟踪")
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
    if args.price_basis == "qfq":
        gbbq = Path(args.gbbq) if args.gbbq else default_gbbq_path(normalize_tdx_dir(args.tdx_dir))
        df, excluded = forward_adjust_market(df, load_gbbq(gbbq), target=args.date)
        if not excluded.empty:
            target_tag = args.date or df["trade_date"].max().strftime("%Y%m%d")
            excluded_path = OUTPUT_DIR / f"tdx_screener_{target_tag}_qfq_excluded.csv"
            excluded.to_csv(excluded_path, index=False, encoding="utf-8-sig")
            print(f"  前复权排除清单: {excluded_path}")
    print(f"  价格口径: {args.price_basis}")
    t_read = time.time() - t0
    print(f"  耗时: {t_read:.1f}s\n")

    # =========================================================================
    # Step 2: 计算 RPS
    # =========================================================================
    print("=" * 70)
    print("Step 2: 计算 RPS_50, RPS_120, RPS_250")
    print("=" * 70)
    t1 = time.time()
    df = calculate_rps(df, periods=[50, 120, 250], verbose=True)
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
        write_codes_csv(codes_path, result["ts_code"])

        print(f"  完整结果: {full_path}")
        print(f"  代码清单: {codes_path}")

    # =========================================================================
    # Step 5: Layer 2 严格筛选
    # =========================================================================
    t3 = 0
    l2_result = pd.DataFrame()
    if not result.empty:
        print("\n" + "=" * 70)
        print("Step 5: Layer 2 严格筛选 (ZHUTU 策略)")
        print("=" * 70)
        t3_start = time.time()
        layer1_codes = result["ts_code"].tolist()
        l2_result = screen_layer2(df, layer1_codes, target_date=args.date, verbose=True)
        t3 = time.time() - t3_start

        if not l2_result.empty:
            l2_display = l2_result[["ts_code", "close", "RPS_120", "RPS_250", "L2_FIRST_SIGNAL"]].copy()
            l2_display = l2_display.sort_values("RPS_120", ascending=False)
            l2_display.columns = ["代码", "收盘价", "RPS120", "RPS250", "首次信号"]
            print(f"\nLayer 2 入选:")
            print(l2_display.to_string(index=False, float_format="%.2f"))
            print(f"\n  共 {len(l2_result)} 只 (首次信号 {l2_result['L2_FIRST_SIGNAL'].sum()} 只)")

            l2_full = OUTPUT_DIR / f"tdx_screener_{target}_L2.csv"
            l2_result.to_csv(l2_full, index=False, encoding="utf-8-sig")

            l2_codes = OUTPUT_DIR / f"tdx_screener_{target}_L2_codes.csv"
            write_codes_csv(l2_codes, l2_result["ts_code"])

            print(f"  完整结果: {l2_full}")
            print(f"  代码清单: {l2_codes}")
        else:
            print("  Layer 2 无股票入选")

    # =========================================================================
    # Step 6: 周线火车轨叠加 (PERIOD=5 概念)
    # =========================================================================
    t4 = 0
    huoche_result = pd.DataFrame()
    if not result.empty:
        print("\n" + "=" * 70)
        print("Step 6: 周线火车轨叠加 (PERIOD=5 概念)")
        print("=" * 70)
        t4_start = time.time()
        l1_codes = result["ts_code"].tolist()
        huoche_cfg = HuocheConfig(
            rps120w_min=args.huoche_rps,
            near_high_ratio=args.huoche_near_high,
            near_high_period_w=args.huoche_near_high_period,
            max_drawdown_pct=args.huoche_drawdown,
            first_signal_weeks=args.huoche_first_weeks,
            ma_trend_strict_weeks=args.huoche_ma_trend_weeks,
        )
        huoche_result = screen_huoche(
            df, l1_codes, target_date=args.date,
            config=huoche_cfg, verbose=True,
        )
        t4 = time.time() - t4_start
        print_huoche_summary(huoche_result)

        if not huoche_result.empty:
            huoche_full = OUTPUT_DIR / f"tdx_screener_{target}_huoche.csv"
            huoche_result.to_csv(huoche_full, index=False, encoding="utf-8-sig")

            huoche_codes = OUTPUT_DIR / f"tdx_screener_{target}_huoche_codes.csv"
            write_codes_csv(huoche_codes, huoche_result["ts_code"])

            print(f"\n  完整结果: {huoche_full}")
            print(f"  代码清单: {huoche_codes}")

            first_df = huoche_result[huoche_result["HUOCHE_FIRST_W"]]
            if not first_df.empty:
                first_path = OUTPUT_DIR / f"tdx_screener_{target}_huoche_first_codes.csv"
                write_codes_csv(first_path, first_df["ts_code"])
                print(f"  首次触发清单: {first_path}")

    # =========================================================================
    # Step 7: 早期启涨扫描 (全市场, Layer 1.5)
    # =========================================================================
    t5 = 0
    early_result = pd.DataFrame()
    if not args.skip_early:
        print("\n" + "=" * 70)
        print("Step 7: 早期启涨扫描 (全市场, Layer 1.5)")
        print("=" * 70)
        t5_start = time.time()
        early_cfg = EarlyLaunchConfig(
            rps_min=args.early_rps_min,
            rps_max=args.early_rps_max,
            above_ma20_min_days=args.early_streak_min,
            above_ma20_max_days=args.early_streak_max,
            seg_ret_min=args.early_seg_ret_min,
            seg_ret_max=args.early_seg_ret_max,
            vs_ma60_max=args.early_vs_ma60_max,
            bo_vol_x=args.early_bo_vol_x,
        )
        early_result = screen_early_launch(
            df, target_date=args.date,
            config=early_cfg, verbose=True,
        )
        t5 = time.time() - t5_start
        print_early_launch_summary(early_result, top_n=30)

        if not early_result.empty:
            target = args.date or df["trade_date"].max().strftime("%Y%m%d")

            # 标注隶属
            l1_set = set(result["ts_code"]) if not result.empty else set()
            huoche_set = set(huoche_result["ts_code"]) if not huoche_result.empty else set()
            early_result["IN_L1"] = early_result["ts_code"].isin(l1_set)
            early_result["IN_HUOCHE"] = early_result["ts_code"].isin(huoche_set)

            n_full = len(early_result)
            n_in_l1 = int(early_result["IN_L1"].sum())
            n_in_hu = int(early_result["IN_HUOCHE"].sum())
            print(f"\n  早期 ∩ L1   : {n_in_l1:3d}  ({n_in_l1/n_full*100:.1f}% of early)  <-- 强势+早期")
            print(f"  早期 ∩ 火车轨: {n_in_hu:3d}  ({n_in_hu/max(1,len(huoche_set))*100:.1f}% of huoche)")
            print(f"  早期-L1     : {n_full - n_in_l1:3d}  (L1未覆盖的新发现)")

            early_full = OUTPUT_DIR / f"tdx_screener_{target}_early.csv"
            early_result.to_csv(early_full, index=False, encoding="utf-8-sig")

            early_codes = OUTPUT_DIR / f"tdx_screener_{target}_early_codes.csv"
            write_codes_csv(early_codes, early_result["ts_code"])

            # 推荐子集：早期 ∩ L1
            early_l1 = early_result[early_result["IN_L1"]]
            if not early_l1.empty:
                el1_path = OUTPUT_DIR / f"tdx_screener_{target}_early_L1.csv"
                early_l1.to_csv(el1_path, index=False, encoding="utf-8-sig")
                el1_codes = OUTPUT_DIR / f"tdx_screener_{target}_early_L1_codes.csv"
                write_codes_csv(el1_codes, early_l1["ts_code"])
                print(f"\n  全集: {early_full}")
                print(f"  推荐: {el1_path}  (早期 ∩ L1, 共 {len(early_l1)} 只)")
            else:
                print(f"\n  完整结果: {early_full}")
                print(f"  代码清单: {early_codes}")

    # =========================================================================
    # Step 8: 极强 RPS 榜 (强者恒强, RPS120/250 双 >=98)
    # =========================================================================
    t6 = 0
    super_result = pd.DataFrame()
    if not args.skip_super and not result.empty:
        print("\n" + "=" * 70)
        print("Step 8: 极强 RPS 榜 (强者恒强)")
        print("=" * 70)
        t6_start = time.time()
        super_cfg = SuperStrongConfig(
            rps120_min=args.super_rps120_min,
            rps250_min=args.super_rps250_min,
            vs_ma60_max=args.super_vs_ma60_max,
            ret20_max=args.super_ret20_max,
            rps50_min_premium=args.super_rps50_premium,
            vs_ma60_premium=args.super_vs_ma60_premium,
            ret20_premium=args.super_ret20_premium,
        )
        super_result = screen_super_strong(
            df, result["ts_code"].tolist(),
            target_date=args.date, config=super_cfg, verbose=True,
        )
        t6 = time.time() - t6_start
        print_super_strong_summary(super_result, top_n=30)

        if not super_result.empty:
            target = args.date or df["trade_date"].max().strftime("%Y%m%d")

            l2_set = set(l2_result["ts_code"]) if not l2_result.empty else set()
            huoche_set = set(huoche_result["ts_code"]) if not huoche_result.empty else set()
            early_set = set(early_result["ts_code"]) if not early_result.empty else set()
            super_result["IN_L2"] = super_result["ts_code"].isin(l2_set)
            super_result["IN_HUOCHE"] = super_result["ts_code"].isin(huoche_set)
            super_result["IN_EARLY"] = super_result["ts_code"].isin(early_set)

            super_full = OUTPUT_DIR / f"tdx_screener_{target}_super.csv"
            super_result.to_csv(super_full, index=False, encoding="utf-8-sig")
            super_codes = OUTPUT_DIR / f"tdx_screener_{target}_super_codes.csv"
            write_codes_csv(super_codes, super_result["ts_code"])

            n = len(super_result)
            n_l2 = int(super_result["IN_L2"].sum())
            n_hu = int(super_result["IN_HUOCHE"].sum())
            n_el = int(super_result["IN_EARLY"].sum())
            n_premium = int((super_result["tier"] == "premium").sum()) if "tier" in super_result.columns else 0
            print(f"\n  覆盖统计: L2 ∩ Super = {n_l2}/{n}  火车轨 ∩ Super = {n_hu}/{n}  Early ∩ Super = {n_el}/{n}")
            print(f"  分档: premium = {n_premium}  extended = {n - n_premium}")
            print(f"  完整结果: {super_full}")
            print(f"  代码清单: {super_codes}")

            if "tier" in super_result.columns:
                premium_df = super_result[super_result["tier"] == "premium"]
                if not premium_df.empty:
                    super_premium_codes = OUTPUT_DIR / f"tdx_screener_{target}_super_premium_codes.csv"
                    write_codes_csv(super_premium_codes, premium_df["ts_code"])
                    print(f"  Premium 清单: {super_premium_codes}")

    # =========================================================================
    # Step 9: 信号跟踪 (NEW / EXIT / HOLD)
    # =========================================================================
    if not args.skip_track:
        target = args.date or df["trade_date"].max().strftime("%Y%m%d")
        layers = [
            ("L1", "_codes.csv"),
            ("L2", "_L2_codes.csv"),
            ("Huoche", "_huoche_codes.csv"),
            ("Early∩L1", "_early_L1_codes.csv"),
            ("Super", "_super_codes.csv"),
        ]
        try:
            tracking = track_signals(OUTPUT_DIR, target, layers=layers)
            print_tracking_report(tracking)
        except Exception as e:
            print(f"\n[信号跟踪] 跳过 (异常: {e})")

    # =========================================================================
    # 汇总
    # =========================================================================
    total = t_read + t_rps + t_screen + t3 + t4 + t5 + t6
    print(f"\n{'='*70}")
    print(
        f"耗时汇总: 读取={t_read:.1f}s  RPS={t_rps:.1f}s  L1={t_screen:.1f}s  "
        f"L2={t3:.1f}s  火车轨={t4:.1f}s  早期={t5:.1f}s  极强榜={t6:.1f}s  总计={total:.1f}s"
    )

    layer_summary = (
        f"分层数: L1={len(result)}  "
        f"火车轨={len(huoche_result)} (首次={int(huoche_result['HUOCHE_FIRST_W'].sum()) if not huoche_result.empty else 0})  "
        f"早期启涨={len(early_result)}  "
        f"极强榜={len(super_result)}"
    )
    print(layer_summary)
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
