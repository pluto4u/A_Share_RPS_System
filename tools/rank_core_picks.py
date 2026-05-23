"""
按 SOP §2.1 阈值从 persistence csv 筛核心槽 / 机动槽候选。

用法:
    py -3 .\\tools\\rank_core_picks.py output\\persistence_20260401_20260430.csv
    py -3 .\\tools\\rank_core_picks.py <csv> --super-rate 0.45 --l1-rate 0.60 --layers 3 --top 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--super-rate", type=float, default=0.45, help="Super 持续率阈值")
    p.add_argument("--l1-rate", type=float, default=0.60, help="L1 持续率阈值")
    p.add_argument("--layers", type=int, default=3, help="覆盖层数阈值")
    p.add_argument("--top", type=int, default=10, help="显示前 N 个")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    df = pd.read_csv(args.csv)
    if df.empty:
        sys.exit("空文件")

    print(f"持续性榜: {Path(args.csv).name}  (总入榜 {len(df)} 只)\n")

    cond_super = df["super_rate"] >= args.super_rate
    cond_l1 = df["l1_rate"] >= args.l1_rate
    cond_layers = df["layers_covered"] >= args.layers
    core = df[cond_super & cond_l1 & cond_layers].copy()

    print("=" * 78)
    print(f"核心槽候选 (super>={args.super_rate}, l1>={args.l1_rate}, layers>={args.layers})")
    print("=" * 78)
    if core.empty:
        print("  无候选 -> 阈值太严或当前周期无强势票")
    else:
        show = core.sort_values("super_rate", ascending=False).head(args.top).copy()
        show["super_rate"] = (show["super_rate"] * 100).round(0).astype(int).astype(str) + "%"
        show["l1_rate"] = (show["l1_rate"] * 100).round(0).astype(int).astype(str) + "%"
        cols = ["ts_code", "Super", "Huoche", "L2", "L1", "Early_L1",
                "layers_covered", "super_rate", "l1_rate", "score"]
        print(show[cols].to_string(index=False))
        print(f"\n  共 {len(core)} 只满足核心槽条件")

    # 机动槽: 周线强 (Huoche 高) 但 Super 不够
    mobile = df[
        (df["super_rate"] < args.super_rate)
        & (df["Huoche"] >= df["samples"] * 0.5)
        & (df["l1_rate"] >= args.l1_rate)
        & (~df["ts_code"].isin(core["ts_code"]))
    ].copy()
    print("\n" + "=" * 78)
    print(f"机动槽候选 (Super<{args.super_rate} 但 Huoche>=50%, l1>={args.l1_rate})")
    print("    -> '准 Super' 类型, 周线趋势已成立, RPS_120 一旦升上 98 即可转核心")
    print("=" * 78)
    if mobile.empty:
        print("  无候选")
    else:
        show = mobile.sort_values("score", ascending=False).head(args.top).copy()
        show["super_rate"] = (show["super_rate"] * 100).round(0).astype(int).astype(str) + "%"
        show["l1_rate"] = (show["l1_rate"] * 100).round(0).astype(int).astype(str) + "%"
        cols = ["ts_code", "Super", "Huoche", "L2", "L1", "Early_L1",
                "layers_covered", "super_rate", "l1_rate", "score"]
        print(show[cols].to_string(index=False))
        print(f"\n  共 {len(mobile)} 只满足机动槽条件")

    # 持续 Super 极高但 Huoche 弱 — '隐藏冠军'
    hidden = df[
        (df["super_rate"] >= 0.5)
        & (df["Huoche"] <= 3)
    ].copy()
    print("\n" + "=" * 78)
    print("隐藏冠军 (Super>=50% 但 Huoche<=3) -> 日线极强 + 周线火车轨刚要触发")
    print("=" * 78)
    if hidden.empty:
        print("  无候选")
    else:
        show = hidden.sort_values("super_rate", ascending=False).head(args.top).copy()
        show["super_rate"] = (show["super_rate"] * 100).round(0).astype(int).astype(str) + "%"
        cols = ["ts_code", "Super", "Huoche", "L2", "L1", "Early_L1",
                "layers_covered", "super_rate", "score"]
        print(show[cols].to_string(index=False))
        print(f"\n  共 {len(hidden)} 只")


if __name__ == "__main__":
    main()
