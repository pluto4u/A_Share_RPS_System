"""
对一份 early codes 清单做三重过滤:
  ① 持续性榜 (上月 0401-0430 等窗口) 数据
  ② 当日 4/30 形态约束 (vs MA60, ret20, close vs MA20, 双98)
  ③ 按 SOP §2.1 / §2.2 阈值分档: 核心 / 机动 / Early-only / 排除

用法:
    py -3 .\\tools\\filter_early_picks.py
    py -3 .\\tools\\filter_early_picks.py \\
        --early output\\tdx_screener_20260430_early_codes.csv \\
        --persistence output\\persistence_20260401_20260430.csv \\
        --target-date 20260430
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rps_analyzer.codes_csv import read_codes_csv

CACHE = Path("output/cache_tdx_with_rps.parquet")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--early", default="output/tdx_screener_20260430_early_codes.csv")
    p.add_argument("--persistence", default="output/persistence_20260401_20260430.csv")
    p.add_argument("--target-date", default="20260430")
    p.add_argument("--super-rate", type=float, default=0.45)
    p.add_argument("--l1-rate", type=float, default=0.60)
    p.add_argument("--layers", type=int, default=3)
    p.add_argument("--vs-ma60-max", type=float, default=0.50)
    p.add_argument("--ret20-max", type=float, default=0.50)
    p.add_argument("--out", default=None, help="输出 csv (默认不写文件)")
    return p.parse_args()


def compute_form(df_all: pd.DataFrame, codes: list, target_dt: pd.Timestamp) -> pd.DataFrame:
    sub = df_all[df_all["ts_code"].isin(codes)].copy()
    sub = sub.sort_values(["ts_code", "trade_date"])
    g = sub.groupby("ts_code", sort=False)
    sub["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    sub["MA60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).mean())
    sub["ret20"] = g["close"].transform(lambda x: x / x.shift(20) - 1)

    today = sub[sub["trade_date"] == target_dt].copy()
    today["vs_ma60"] = today["close"] / today["MA60"] - 1
    today["above_ma20"] = today["close"] > today["MA20"]
    today["double_98"] = (today["RPS_120"] >= 98) & (today["RPS_250"] >= 98)
    return today[["ts_code", "close", "MA20", "MA60", "vs_ma60", "ret20",
                  "RPS_120", "RPS_250", "above_ma20", "double_98", "amount"]]


def classify(row: pd.Series, args) -> str:
    """决定档位"""
    if pd.isna(row.get("MA60")):
        return "排除-数据不足"
    if not row["above_ma20"]:
        return "排除-收盘 < MA20"
    if row["vs_ma60"] > args.vs_ma60_max:
        return f"排除-vs MA60 {row['vs_ma60']:.0%} > {args.vs_ma60_max:.0%}"
    if row["ret20"] > args.ret20_max:
        return f"排除-20日涨幅 {row['ret20']:.0%} > {args.ret20_max:.0%}"

    super_rate = row.get("super_rate", 0) or 0
    l1_rate = row.get("l1_rate", 0) or 0
    layers = row.get("layers_covered", 0) or 0
    huoche = row.get("Huoche", 0) or 0
    samples = row.get("samples", 1) or 1

    if super_rate >= args.super_rate and l1_rate >= args.l1_rate and layers >= args.layers:
        return "★ 核心槽"

    if (huoche / samples) >= 0.5 and l1_rate >= args.l1_rate:
        return "● 机动槽-Huoche持续"

    if row["double_98"] and l1_rate >= 0.30:
        return "○ 机动槽-早期+双98"

    if l1_rate >= 0.30 or row["double_98"]:
        return "△ 观察池-Early触发但持续性弱"

    return "排除-持续性极弱"


def main() -> None:
    args = parse_args()

    early_path = Path(args.early)
    pers_path = Path(args.persistence)
    if not early_path.exists():
        sys.exit(f"找不到 early codes: {early_path}")
    if not pers_path.exists():
        sys.exit(f"找不到 persistence csv: {pers_path}")
    if not CACHE.exists():
        sys.exit(f"找不到缓存 {CACHE} (请先跑 diagnose_picks.py --refresh)")

    early_codes = read_codes_csv(early_path)
    pers = pd.read_csv(pers_path)
    target_dt = pd.Timestamp(args.target_date)
    print(f"早期触发清单: {early_path.name}  ({len(early_codes)} 只)")
    print(f"持续性榜: {pers_path.name}  ({len(pers)} 只)")
    print(f"目标日: {args.target_date}\n")

    df = pd.read_parquet(CACHE)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    form = compute_form(df, early_codes, target_dt)
    print(f"形态计算: {len(form)} 只 (有效行情)")

    pers_keep = pers[["ts_code", "Super", "Huoche", "L2", "L1", "Early_L1",
                      "layers_covered", "score", "samples", "super_rate", "l1_rate"]]
    merged = form.merge(pers_keep, on="ts_code", how="left")
    merged["super_rate"] = merged["super_rate"].fillna(0)
    merged["l1_rate"] = merged["l1_rate"].fillna(0)
    merged["layers_covered"] = merged["layers_covered"].fillna(0)
    merged["Huoche"] = merged["Huoche"].fillna(0)
    merged["samples"] = merged["samples"].fillna(21)

    in_pers = merged["score"].notna()
    print(f"  其中 {in_pers.sum()} 只在持续性榜内, {(~in_pers).sum()} 只新进 (Early 当日触发但月内没持续性)")

    merged["bucket"] = merged.apply(lambda r: classify(r, args), axis=1)

    print("\n" + "=" * 90)
    print("按档位分布:")
    print("=" * 90)
    counts = merged["bucket"].value_counts()
    for k, v in counts.items():
        print(f"  {k:30s} {v:3d} 只")

    for label in ["★ 核心槽", "● 机动槽-Huoche持续", "○ 机动槽-早期+双98",
                  "△ 观察池-Early触发但持续性弱"]:
        rows = merged[merged["bucket"] == label]
        if rows.empty:
            continue
        print(f"\n{'=' * 90}\n{label}  ({len(rows)} 只)\n{'=' * 90}")
        show = rows.copy()
        show["close"] = show["close"].round(2)
        show["vs60%"] = (show["vs_ma60"] * 100).round(0).astype(int)
        show["ret20%"] = (show["ret20"] * 100).round(0).astype(int)
        show["RPS"] = show.apply(lambda r: f"{int(r['RPS_120'])}/{int(r['RPS_250'])}", axis=1)
        show["持续性"] = show.apply(
            lambda r: f"S{int(r['Super'])}/H{int(r['Huoche'])}/L{int(r['L1'])}/cov{int(r['layers_covered'])}",
            axis=1)
        show["super%"] = (show["super_rate"] * 100).round(0).astype(int)
        show["l1%"] = (show["l1_rate"] * 100).round(0).astype(int)
        show["score"] = show["score"].fillna(0).astype(int)
        cols = ["ts_code", "close", "vs60%", "ret20%", "RPS", "持续性", "super%", "l1%", "score"]
        show = show[cols].sort_values("score", ascending=False)
        print(show.to_string(index=False))

    if args.out:
        merged.sort_values(["bucket", "score"], ascending=[True, False])\
              .to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n[输出] {args.out}")


if __name__ == "__main__":
    main()
