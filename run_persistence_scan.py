"""
================================================================================
持续性扫描 + 回测 — 把"每日选股"升级为"窗口聚合"
================================================================================
对一段时间窗口内的每个采样日，扫描 L1 / L2 / Super / Huoche / Early∩L1
五个层级，把命中信息汇总成长表，再聚合成"每只票在每层的命中天数 + 持续性
评分"，并可选地回测窗口结束日之后 N 个交易日的 forward return。

用法:
    py -3 run_persistence_scan.py --start 20260301 --end 20260331 --hold 10
    py -3 run_persistence_scan.py --start 20260413 --end 20260417 --hold 0
    py -3 run_persistence_scan.py --start 20260301 --end 20260331 --sample-every 5

输出（output/）:
    persistence_{start}_{end}.csv             每只票在各层的命中天数 + 评分
    persistence_{start}_{end}_codes.csv       top-N 代码清单
    persistence_{start}_{end}_h{N}.csv        若 --hold>0, 含 forward return 的样本
"""

import argparse
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_stock_screener import (
    TdxStockScreener, screen_layer2,
)
from rps_analyzer.huoche_screener import screen_huoche, HuocheConfig
from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig,
)
from rps_analyzer.super_strong import (
    screen_super_strong, SuperStrongConfig,
)
from rps_analyzer.codes_csv import write_codes_csv

OUTPUT_DIR = Path("output")

LAYER_WEIGHTS = {
    "Super_Premium": 6,
    "Super_Extended": 3,
    "Huoche": 4,
    "L2": 3,
    "L1": 2,
    "Early_L1": 2,
}
LAYERS = list(LAYER_WEIGHTS.keys())


def _scan_one_date(
    df: pd.DataFrame,
    date_str: str,
    skip: dict,
) -> List[Tuple[str, str, str]]:
    """对单个采样日扫描所有启用层，返回 (ts_code, date, layer) 三元组列表"""
    rows: List[Tuple[str, str, str]] = []

    l1_screener = TdxStockScreener()
    l1_res = l1_screener.screen(df, target_date=date_str, verbose=False)
    if l1_res.empty:
        return rows

    l1_codes = l1_res["ts_code"].tolist()
    for c in l1_codes:
        rows.append((c, date_str, "L1"))

    if not skip["l2"]:
        l2_res = screen_layer2(df, l1_codes, target_date=date_str, verbose=False)
        for c in l2_res["ts_code"]:
            rows.append((c, date_str, "L2"))

    if not skip["super"]:
        super_res = screen_super_strong(
            df, l1_codes, target_date=date_str,
            config=SuperStrongConfig(), verbose=False,
        )
        if not super_res.empty:
            if "tier" in super_res.columns:
                for _, row in super_res.iterrows():
                    tag = "Super_Premium" if row["tier"] == "premium" else "Super_Extended"
                    rows.append((row["ts_code"], date_str, tag))
            else:
                for c in super_res["ts_code"]:
                    rows.append((c, date_str, "Super_Extended"))

    if not skip["huoche"]:
        h_res = screen_huoche(
            df, l1_codes, target_date=date_str,
            config=HuocheConfig(), verbose=False,
        )
        for c in h_res["ts_code"]:
            rows.append((c, date_str, "Huoche"))

    if not skip["early"]:
        e_res = screen_early_launch(
            df, target_date=date_str,
            config=EarlyLaunchConfig(), verbose=False,
        )
        l1_set = set(l1_codes)
        for c in e_res["ts_code"]:
            if c in l1_set:
                rows.append((c, date_str, "Early_L1"))

    return rows


def _aggregate(long_df: pd.DataFrame, n_samples: int) -> pd.DataFrame:
    """聚合长表为每只票一行的持续性 DataFrame"""
    base_cols = ["ts_code", "Super"] + LAYERS + ["layers_covered", "score"]
    if long_df.empty:
        return pd.DataFrame(columns=base_cols)

    pivot = (
        long_df.groupby(["ts_code", "layer"]).size().unstack(fill_value=0)
    )
    for L in LAYERS:
        if L not in pivot.columns:
            pivot[L] = 0
    pivot = pivot[LAYERS]

    # 向后兼容: Super = Super_Premium + Super_Extended
    pivot["Super"] = pivot["Super_Premium"] + pivot["Super_Extended"]
    # layers_covered 用合并后的层数 (Super 算 1 层, 不重复计 Premium/Extended)
    layer_cover_cols = [c for c in ["L1", "L2", "Super", "Huoche", "Early_L1"] if c in pivot.columns]
    pivot["layers_covered"] = (pivot[layer_cover_cols] > 0).sum(axis=1)
    pivot["score"] = sum(pivot[L] * w for L, w in LAYER_WEIGHTS.items())
    pivot["samples"] = n_samples
    pivot["super_rate"] = pivot["Super"] / max(1, n_samples)
    pivot["super_premium_rate"] = pivot["Super_Premium"] / max(1, n_samples)
    pivot["l1_rate"] = pivot["L1"] / max(1, n_samples)

    pivot = pivot.sort_values(
        ["score", "Super_Premium", "Super", "L1"],
        ascending=[False, False, False, False],
    ).reset_index()
    return pivot


def _forward_return(
    df: pd.DataFrame,
    end_dt: pd.Timestamp,
    hold: int,
) -> pd.DataFrame:
    """计算窗口结束日后 hold 个交易日的 forward return"""
    all_dates = sorted(df["trade_date"].unique())
    if end_dt not in all_dates:
        end_dt = pd.Timestamp(max(d for d in all_dates if d <= end_dt))
    idx = all_dates.index(end_dt)
    if idx + hold >= len(all_dates):
        return pd.DataFrame()
    fwd_dt = all_dates[idx + hold]

    cur = df[df["trade_date"] == end_dt][["ts_code", "close"]].rename(columns={"close": "close_end"})
    nxt = df[df["trade_date"] == fwd_dt][["ts_code", "close"]].rename(columns={"close": "close_fwd"})
    fr = cur.merge(nxt, on="ts_code", how="inner")
    fr["forward_return"] = fr["close_fwd"] / fr["close_end"] - 1
    fr["end_date"] = end_dt.strftime("%Y%m%d")
    fr["fwd_date"] = fwd_dt.strftime("%Y%m%d")
    return fr


def _bucket_report(merged: pd.DataFrame, n_samples: int) -> None:
    """按 super_rate 分组，打印 forward return 报告"""
    bins = [
        ("Super≥80%  (核心持续)", merged["super_rate"] >= 0.8),
        ("Super 50-80% (准核心)", (merged["super_rate"] >= 0.5) & (merged["super_rate"] < 0.8)),
        ("Super 20-50% (一般)", (merged["super_rate"] >= 0.2) & (merged["super_rate"] < 0.5)),
        ("Super <20%  (噪音)", merged["super_rate"] < 0.2),
    ]
    print("\n按 Super 持续性分组的 forward return:")
    print(f"{'分组':<25}{'n':>6}{'med%':>8}{'avg%':>8}{'win%':>7}")
    for name, mask in bins:
        sub = merged[mask]
        if sub.empty:
            print(f"{name:<25}{0:>6}{'-':>8}{'-':>8}{'-':>7}")
            continue
        med = sub["forward_return"].median() * 100
        avg = sub["forward_return"].mean() * 100
        win = (sub["forward_return"] > 0).mean() * 100
        print(f"{name:<25}{len(sub):>6}{med:>8.2f}{avg:>8.2f}{win:>6.1f}%")


def main():
    ap = argparse.ArgumentParser(description="持续性扫描 + 回测")
    ap.add_argument("--start", required=True, help="窗口起始 YYYYMMDD")
    ap.add_argument("--end", required=True, help="窗口结束 YYYYMMDD")
    ap.add_argument("--sample-every", type=int, default=1,
                    help="每 N 个交易日采样一次 (默认1=每个交易日)")
    ap.add_argument("--hold", type=int, default=0,
                    help="窗口结束后 N 个交易日做 forward return 回测 (默认0=不做)")
    ap.add_argument("--top-n", type=int, default=30, help="榜单显示行数")
    ap.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    ap.add_argument("--min-days", type=int, default=250)

    ap.add_argument("--skip-l2", action="store_true")
    ap.add_argument("--skip-super", action="store_true")
    ap.add_argument("--skip-huoche", action="store_true")
    ap.add_argument("--skip-early", action="store_true")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    skip = dict(
        l2=args.skip_l2, super=args.skip_super,
        huoche=args.skip_huoche, early=args.skip_early,
    )

    print("=" * 70)
    print(f"持续性扫描: {args.start} ~ {args.end}  采样: 每{args.sample_every}日")
    print(f"启用层: " + ", ".join(L for L in LAYERS if not skip.get(L.lower().replace("_l1", "").replace("early", "early"), False)))
    print("=" * 70)

    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    df = calculate_rps(df, periods=[50, 120, 250], verbose=False)
    print(f"  数据加载 + RPS 完成: {time.time()-t0:.1f}s\n")

    all_dates = sorted(df["trade_date"].unique())
    start_dt = pd.Timestamp(args.start)
    end_dt = pd.Timestamp(args.end)
    window_dates = [d for d in all_dates if start_dt <= d <= end_dt]
    if not window_dates:
        print(f"  错误: 窗口 {args.start}-{args.end} 内无数据")
        return
    sampled = window_dates[:: args.sample_every]
    n_samples = len(sampled)
    print(f"  窗口内交易日 {len(window_dates)} 天, 采样 {n_samples} 天")
    if n_samples > 6:
        est = n_samples * 60
        print(f"  预计耗时: ~{est//60} 分钟 (L1 优化版每日 ~1 分钟)")

    long_rows: List[Tuple[str, str, str]] = []
    for i, d in enumerate(sampled, 1):
        date_str = d.strftime("%Y%m%d")
        ts = time.time()
        rows = _scan_one_date(df, date_str, skip)
        long_rows.extend(rows)
        n_l1 = sum(1 for r in rows if r[2] == "L1")
        n_super_p = sum(1 for r in rows if r[2] == "Super_Premium")
        n_super_e = sum(1 for r in rows if r[2] == "Super_Extended")
        n_huoche = sum(1 for r in rows if r[2] == "Huoche")
        print(
            f"  [{i}/{n_samples}] {date_str}  L1={n_l1:3d} "
            f"SuperP={n_super_p:3d} SuperE={n_super_e:3d} Huoche={n_huoche:3d}  "
            f"({time.time()-ts:.1f}s)"
        )

    long_df = pd.DataFrame(long_rows, columns=["ts_code", "date", "layer"])
    agg = _aggregate(long_df, n_samples)
    if agg.empty:
        print("\n[聚合] 无数据")
        return

    suffix = f"{args.start}_{args.end}"
    full_path = OUTPUT_DIR / f"persistence_{suffix}.csv"
    agg.to_csv(full_path, index=False, encoding="utf-8-sig")

    top = agg.head(args.top_n)
    codes_path = OUTPUT_DIR / f"persistence_{suffix}_codes.csv"
    write_codes_csv(codes_path, top["ts_code"])

    print("\n" + "=" * 70)
    print(f"持续性榜 Top {min(args.top_n, len(agg))} (共 {len(agg)} 只入榜)")
    print("=" * 70)
    show_cols = ["ts_code", "L1", "L2", "Super", "Super_Premium", "Huoche", "Early_L1",
                 "layers_covered", "super_rate", "score"]
    show = top[show_cols].copy()
    show["super_rate"] = (show["super_rate"] * 100).round(0)
    show.columns = ["代码", "L1", "L2", "Super", "SuperP", "Huoche", "Early", "层数", "Super%", "评分"]
    print(show.to_string(index=False))

    print(f"\n完整结果: {full_path}")
    print(f"代码清单: {codes_path} (top {len(top)})")

    if args.hold > 0:
        print("\n" + "=" * 70)
        print(f"forward return 回测: 持有 {args.hold} 个交易日")
        print("=" * 70)
        fr = _forward_return(df, end_dt, args.hold)
        if fr.empty:
            print("  数据末尾不足以计算 forward return")
        else:
            merged = agg.merge(fr, on="ts_code", how="inner")
            bt_path = OUTPUT_DIR / f"persistence_{suffix}_h{args.hold}.csv"
            merged.to_csv(bt_path, index=False, encoding="utf-8-sig")
            print(f"  样本: {len(merged)}/{len(agg)} 只能算 forward (其余在 {fr['fwd_date'].iloc[0] if not fr.empty else 'NA'} 无价)")
            _bucket_report(merged, n_samples)
            print(f"\n  详细文件: {bt_path}")

    print(f"\n总耗时: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
