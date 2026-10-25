"""
================================================================================
周线火车轨参数扫参 (Step 6 only)
================================================================================
用途:
    - 一次性加载行情 + RPS（带 parquet 缓存），复用已有 L1 候选清单
    - 对多组 HuocheConfig 参数批量调用 screen_huoche
    - 输出对比表（通过数 / 首次触发数 / 与基线交集）

用法:
    py -3 run_huoche_sweep.py --date 20260417
    py -3 run_huoche_sweep.py --date 20260417 --l1-csv output\tdx_screener_20260417_codes.csv
    py -3 run_huoche_sweep.py --date 20260417 --no-cache   # 强制重算 RPS

设计:
    - 参数组合在脚本顶部 PARAM_GRID 中定义，方便迭代
    - 缓存文件: output/cache_tdx_with_rps.parquet
"""

import time
import argparse
import pandas as pd
from pathlib import Path

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.huoche_screener import screen_huoche, HuocheConfig
from rps_analyzer.codes_csv import read_codes_csv

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


# =============================================================================
# 参数组合 — 在这里增删
# =============================================================================
PARAM_GRID = [
    ("baseline",         dict(rps120w_min=90, near_high_ratio=0.80, max_drawdown_pct=0.25, first_signal_weeks=8)),
    ("rps85",            dict(rps120w_min=85, near_high_ratio=0.80, max_drawdown_pct=0.25, first_signal_weeks=8)),
    ("rps80",            dict(rps120w_min=80, near_high_ratio=0.80, max_drawdown_pct=0.25, first_signal_weeks=8)),
    ("nearhigh075",      dict(rps120w_min=90, near_high_ratio=0.75, max_drawdown_pct=0.25, first_signal_weeks=8)),
    ("dd030",            dict(rps120w_min=90, near_high_ratio=0.80, max_drawdown_pct=0.30, first_signal_weeks=8)),
    ("first12w",         dict(rps120w_min=90, near_high_ratio=0.80, max_drawdown_pct=0.25, first_signal_weeks=12)),
    ("loose_combo",      dict(rps120w_min=85, near_high_ratio=0.75, max_drawdown_pct=0.30, first_signal_weeks=12)),
    ("medium_combo",     dict(rps120w_min=87, near_high_ratio=0.78, max_drawdown_pct=0.27, first_signal_weeks=10)),
]


def load_data(args) -> pd.DataFrame:
    """加载行情 + RPS，优先使用 parquet 缓存"""
    if CACHE_PATH.exists() and not args.no_cache:
        print(f"[缓存] 从 {CACHE_PATH} 加载 (使用 --no-cache 可强制重算)")
        t0 = time.time()
        df = pd.read_parquet(CACHE_PATH)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        print(f"  耗时: {time.time() - t0:.1f}s, {len(df):,} 行, {df['ts_code'].nunique()} 只股票\n")
        return df

    print("[读取] 加载通达信本地数据 ...")
    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    print(f"  耗时: {time.time() - t0:.1f}s\n")

    print("[RPS] 计算 RPS_120, RPS_250 ...")
    t0 = time.time()
    df = calculate_rps(df, periods=[120, 250], verbose=True)
    print(f"  耗时: {time.time() - t0:.1f}s\n")

    OUTPUT_DIR.mkdir(exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)
    print(f"[缓存] 已写入 {CACHE_PATH}\n")
    return df


def load_l1_codes(args) -> list:
    if args.l1_csv:
        path = Path(args.l1_csv)
    else:
        target = args.date or "latest"
        path = OUTPUT_DIR / f"tdx_screener_{target}_codes.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"L1 候选清单不存在: {path}\n"
            f"请先跑一次 run_tdx_screener.py --date {args.date} 生成 L1 codes"
        )
    codes = read_codes_csv(path)
    print(f"[L1] 从 {path} 读取 {len(codes)} 只候选\n")
    return codes


def run_one(df, l1_codes, target_date, name, params) -> dict:
    cfg = HuocheConfig(**params)
    t0 = time.time()
    result = screen_huoche(df, l1_codes, target_date=target_date,
                          config=cfg, verbose=False)
    elapsed = time.time() - t0

    pass_codes = set(result["ts_code"]) if not result.empty else set()
    first_codes = set(result.loc[result["HUOCHE_FIRST_W"], "ts_code"]) if not result.empty else set()
    return {
        "name": name,
        "params": params,
        "pass_n": len(pass_codes),
        "first_n": len(first_codes),
        "pass_codes": pass_codes,
        "first_codes": first_codes,
        "elapsed": elapsed,
        "result": result,
    }


def main():
    parser = argparse.ArgumentParser(description="周线火车轨参数扫参")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD")
    parser.add_argument("--min-days", type=int, default=250)
    parser.add_argument("--l1-csv", default=None, help="L1 候选清单 csv (默认从 output/ 读对应日期)")
    parser.add_argument("--no-cache", action="store_true", help="不使用 parquet 缓存")
    parser.add_argument("--save-detail", action="store_true", help="保存每组的明细到 output/sweep_*.csv")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    df = load_data(args)
    l1_codes = load_l1_codes(args)

    print("=" * 78)
    print(f"参数扫参: {len(PARAM_GRID)} 组")
    print("=" * 78)

    runs = []
    for name, params in PARAM_GRID:
        print(f"  [运行] {name:14s}  {params}")
        run = run_one(df, l1_codes, args.date, name, params)
        runs.append(run)
        print(f"           pass={run['pass_n']:3d}  first={run['first_n']:3d}  elapsed={run['elapsed']:.2f}s")

    # ========================================================================
    # 对比表
    # ========================================================================
    baseline = runs[0]
    print("\n" + "=" * 78)
    print("对比表 (基线 = baseline)")
    print("=" * 78)
    rows = []
    for r in runs:
        overlap = len(r["pass_codes"] & baseline["pass_codes"])
        added = len(r["pass_codes"] - baseline["pass_codes"])
        dropped = len(baseline["pass_codes"] - r["pass_codes"])
        rows.append({
            "组合": r["name"],
            "通过": r["pass_n"],
            "首次": r["first_n"],
            "vs基线交集": overlap,
            "vs基线新增": added,
            "vs基线丢失": dropped,
            "耗时s": round(r["elapsed"], 2),
        })
    cmp_df = pd.DataFrame(rows)
    print(cmp_df.to_string(index=False))

    # ========================================================================
    # 参数详情
    # ========================================================================
    print("\n" + "=" * 78)
    print("各组参数详情")
    print("=" * 78)
    for r in runs:
        p = r["params"]
        print(
            f"  {r['name']:14s}  RPS>={p.get('rps120w_min',90):>4.0f}  "
            f"近高>={p.get('near_high_ratio',0.80):.2f}  "
            f"回撤<={p.get('max_drawdown_pct',0.25):.0%}  "
            f"首次回看={p.get('first_signal_weeks',8)}周"
        )

    # ========================================================================
    # 推荐
    # ========================================================================
    print("\n" + "=" * 78)
    print("折中推荐 (按 通过数 ∈ [10,40] 且 首次>=2 排序)")
    print("=" * 78)
    candidates = [
        r for r in runs
        if 10 <= r["pass_n"] <= 40 and r["first_n"] >= 2
    ]
    candidates.sort(key=lambda r: (-r["first_n"], -r["pass_n"]))
    if candidates:
        for r in candidates[:5]:
            print(f"  >> {r['name']:14s}  pass={r['pass_n']}  first={r['first_n']}")
    else:
        print("  无组合同时满足 通过∈[10,40] & 首次>=2，请扩大网格")

    # ========================================================================
    # 保存
    # ========================================================================
    target = args.date or df["trade_date"].max().strftime("%Y%m%d")
    cmp_path = OUTPUT_DIR / f"huoche_sweep_{target}.csv"
    cmp_df.to_csv(cmp_path, index=False, encoding="utf-8-sig")
    print(f"\n  对比表已保存: {cmp_path}")

    if args.save_detail:
        for r in runs:
            if r["result"].empty:
                continue
            p = OUTPUT_DIR / f"huoche_sweep_{target}_{r['name']}.csv"
            r["result"].to_csv(p, index=False, encoding="utf-8-sig")
        print(f"  明细已保存: output/huoche_sweep_{target}_*.csv")


if __name__ == "__main__":
    main()
