"""
================================================================================
v1 (super_rate) vs v2 (super_premium_rate / entry_grade) 胜率对比
================================================================================
读取 persistence_{start}_{end}_h{N}.csv (含 forward_return)，按下面的分桶打印
胜率、中位收益、平均收益、最大回撤——直观看出 v2 的"金三角/金双角"思路是否
比旧版的纯 super_rate 阈值更能区分赚钱组。

用法:
    py -3 tools/compare_v1_v2_winrate.py output/persistence_20260413_20260417_h5.csv
    py -3 tools/compare_v1_v2_winrate.py output/persistence_*_h5.csv          # 多窗口合并
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


def _bucket(df: pd.DataFrame, label: str, mask: pd.Series) -> dict:
    sub = df[mask]
    if sub.empty:
        return {
            "组": label, "n": 0, "win%": float("nan"),
            "med%": float("nan"), "avg%": float("nan"), "min%": float("nan"),
        }
    fr = sub["forward_return"]
    return {
        "组": label,
        "n": len(sub),
        "win%": round((fr > 0).mean() * 100, 1),
        "med%": round(fr.median() * 100, 2),
        "avg%": round(fr.mean() * 100, 2),
        "min%": round(fr.min() * 100, 2),
    }


def _print_table(rows: list[dict], title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    if not rows:
        print("(无样本)")
        return
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))


def analyze(df: pd.DataFrame) -> None:
    if "forward_return" not in df.columns:
        raise ValueError("缺少 forward_return 列，需要先用 run_persistence_scan.py --hold N 生成 h{N} 文件")

    has_premium = "Super_Premium" in df.columns
    has_premium_rate = "super_premium_rate" in df.columns

    # ---- v1: 旧分桶 (super_rate) ----
    v1 = [
        _bucket(df, "Super≥80% (核心)", df["super_rate"] >= 0.8),
        _bucket(df, "Super 50-80%",    (df["super_rate"] >= 0.5) & (df["super_rate"] < 0.8)),
        _bucket(df, "Super 20-50%",    (df["super_rate"] >= 0.2) & (df["super_rate"] < 0.5)),
        _bucket(df, "Super <20%",      df["super_rate"] < 0.2),
    ]
    _print_table(v1, "v1: 按 super_rate 分桶 (现状)")

    # ---- v2: 新分桶 (Super_Premium / layers_covered) ----
    if has_premium_rate:
        v2 = [
            _bucket(df, "SuperP_rate≥40% (强 S)",  df["super_premium_rate"] >= 0.4),
            _bucket(df, "SuperP_rate>0 (含 P)",    (df["super_premium_rate"] > 0) & (df["super_premium_rate"] < 0.4)),
            _bucket(df, "Super≥60% & 无 P (extended)", (df["super_rate"] >= 0.6) & (df["super_premium_rate"] == 0)),
            _bucket(df, "其余",                     (df["super_rate"] < 0.6) & (df["super_premium_rate"] == 0)),
        ]
        _print_table(v2, "v2: 按 Super_Premium 分桶 (premium 才算真 S)")

    # ---- v2-补充: 多层共振 (layers_covered) ----
    layer_buckets = [
        _bucket(df, "layers=5 (五层共振)", df["layers_covered"] == 5),
        _bucket(df, "layers=4",             df["layers_covered"] == 4),
        _bucket(df, "layers=3",             df["layers_covered"] == 3),
        _bucket(df, "layers≤2",             df["layers_covered"] <= 2),
    ]
    _print_table(layer_buckets, "v2 补充: 按 layers_covered 分桶 (越多层越共振)")

    # ---- v2 复合: SuperP×Huoche 共振 ----
    if has_premium and "Huoche" in df.columns:
        combo = [
            _bucket(df, "SuperP≥1 & Huoche≥1", (df["Super_Premium"] >= 1) & (df["Huoche"] >= 1)),
            _bucket(df, "SuperP≥1 only",       (df["Super_Premium"] >= 1) & (df["Huoche"] == 0)),
            _bucket(df, "Huoche≥1 only",       (df["Super_Premium"] == 0) & (df["Huoche"] >= 1)),
            _bucket(df, "都没有",               (df["Super_Premium"] == 0) & (df["Huoche"] == 0)),
        ]
        _print_table(combo, "v2 复合: Super_Premium × Huoche 共振")


def main() -> None:
    ap = argparse.ArgumentParser(description="v1 (super_rate) vs v2 (super_premium_rate) 胜率对比")
    ap.add_argument("files", nargs="+", help="persistence_*_h*.csv 一个或多个；多个会合并样本")
    args = ap.parse_args()

    paths: list[Path] = []
    for pat in args.files:
        p = Path(pat)
        if p.exists():
            paths.append(p)
        else:
            paths.extend(sorted(Path().glob(pat)))

    if not paths:
        print(f"未找到任何文件: {args.files}")
        sys.exit(1)

    print(f"读取 {len(paths)} 个 h 文件: {[p.name for p in paths]}")
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        df["_src"] = p.name
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True)
    print(f"合并样本: {len(merged)} 行  (含 forward_return: {merged['forward_return'].notna().sum()})")
    merged = merged.dropna(subset=["forward_return"])
    analyze(merged)


if __name__ == "__main__":
    main()
