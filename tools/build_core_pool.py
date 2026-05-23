"""
核心股池综合评分 — 多维度评分输出 Top N 候选

评分维度:
  ① 持续性 (来自 persistence csv): super_rate / l1_rate / layers_covered
  ② 当日信号 (0430 Super / Huoche / Early∩L1 / Huoche FIRST_W)
  ③ 形态 (vs MA60 / ret20 / close vs MA20)
  ④ 趋势节奏 (ret60 健康度)
  ⑤ 资金质量 (成交额)

输出: 最终股池, 默认 Top 15, 各维度明细.

用法:
    py -3 .\\tools\\build_core_pool.py
    py -3 .\\tools\\build_core_pool.py --date 20260430 --top 15 --out output\\core_pool_20260430.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rps_analyzer.codes_csv import read_codes_csv

CACHE = Path("output/cache_tdx_with_rps.parquet")
OUTPUT_DIR = Path("output")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="20260430")
    p.add_argument("--persistence", default="output/persistence_20260401_20260430.csv")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--candidates-from", default="all",
                   choices=["all", "persistence", "super", "intersect"],
                   help="候选池来源: all=持续性+当日Super 联合; persistence=月度榜; super=当日榜; intersect=交集")
    p.add_argument("--out", default=None)
    return p.parse_args()


# 评分函数
def score_persistence(super_rate: float, l1_rate: float, layers: int) -> tuple:
    s_super = super_rate * 100 * 1.5
    s_l1 = l1_rate * 100 * 0.5
    s_layer = layers * 10
    return s_super + s_l1 + s_layer, {"持续Super": round(s_super), "持续L1": round(s_l1), "层覆盖": round(s_layer)}


def score_today(in_super: bool, in_huoche: bool, in_huoche_first: bool,
                in_early_l1: bool) -> tuple:
    score = 0
    detail = {}
    if in_super:
        score += 50
        detail["当日Super"] = 50
    if in_huoche_first:
        score += 50
        detail["Huoche首发"] = 50
    elif in_huoche:
        score += 30
        detail["Huoche持续"] = 30
    if in_early_l1:
        score += 15
        detail["Early∩L1"] = 15
    return score, detail


def score_form(vs_ma60: float, ret20: float, above_ma20: bool) -> tuple:
    score = 0
    detail = {}
    if above_ma20:
        score += 10
        detail["上MA20"] = 10
    else:
        detail["上MA20"] = 0

    if pd.notna(vs_ma60):
        if vs_ma60 < 0.15:
            score += 20
            detail["位置"] = 20
        elif vs_ma60 < 0.30:
            score += 15
            detail["位置"] = 15
        elif vs_ma60 < 0.50:
            score += 5
            detail["位置"] = 5
        elif vs_ma60 >= 0.60:
            score -= 20
            detail["位置"] = -20
        else:
            score -= 5
            detail["位置"] = -5

    if pd.notna(ret20):
        if ret20 < 0.15:
            score += 10
            detail["20日涨"] = 10
        elif ret20 < 0.30:
            score += 5
            detail["20日涨"] = 5
        elif ret20 >= 0.50:
            score -= 10
            detail["20日涨"] = -10
    return score, detail


def score_trend(ret60: float) -> tuple:
    if pd.isna(ret60):
        return 0, {}
    if ret60 < 0:
        return 15, {"60日趋势": 15}
    if ret60 < 0.30:
        return 10, {"60日趋势": 10}
    if ret60 < 0.60:
        return 5, {"60日趋势": 5}
    if ret60 < 1.00:
        return 0, {"60日趋势": 0}
    return -15, {"60日趋势": -15}


def score_liquidity(amount: float) -> tuple:
    if pd.isna(amount):
        return 0, {}
    yi = amount / 1e8
    if yi >= 20:
        return 10, {"成交额": 10}
    if yi >= 5:
        return 5, {"成交额": 5}
    if yi < 1:
        return -10, {"成交额": -10}
    return 0, {"成交额": 0}


def main() -> None:
    args = parse_args()
    target_dt = pd.Timestamp(args.date)

    # 信号集合
    super_codes = set(read_codes_csv(OUTPUT_DIR / f"tdx_screener_{args.date}_super_codes.csv"))
    huoche_codes = set(read_codes_csv(OUTPUT_DIR / f"tdx_screener_{args.date}_huoche_codes.csv"))
    huoche_first_path = OUTPUT_DIR / f"tdx_screener_{args.date}_huoche_first_codes.csv"
    huoche_first_codes = set(read_codes_csv(huoche_first_path)) if huoche_first_path.exists() else set()
    early_l1_path = OUTPUT_DIR / f"tdx_screener_{args.date}_early_L1_codes.csv"
    early_l1_codes = set(read_codes_csv(early_l1_path)) if early_l1_path.exists() else set()

    # 持续性榜
    pers = pd.read_csv(args.persistence)
    pers_codes = set(pers["ts_code"].tolist())

    # 候选池
    if args.candidates_from == "persistence":
        candidates = pers_codes
    elif args.candidates_from == "super":
        candidates = super_codes
    elif args.candidates_from == "intersect":
        candidates = pers_codes & super_codes
    else:
        candidates = pers_codes | super_codes | huoche_codes | huoche_first_codes

    print(f"=" * 90)
    print(f"核心股池构建  目标日 {args.date}")
    print(f"=" * 90)
    print(f"  当日 Super: {len(super_codes)} 只")
    print(f"  当日 Huoche: {len(huoche_codes)} 只 (FIRST_W: {len(huoche_first_codes)})")
    print(f"  当日 Early∩L1: {len(early_l1_codes)} 只")
    print(f"  月度持续性榜: {len(pers_codes)} 只")
    print(f"  候选池 ({args.candidates_from}): {len(candidates)} 只")

    # cache + 形态
    df = pd.read_parquet(CACHE)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    sub = df[df["ts_code"].isin(candidates)].copy()
    sub = sub.sort_values(["ts_code", "trade_date"])
    g = sub.groupby("ts_code", sort=False)
    sub["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    sub["MA60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).mean())
    sub["ret20"] = g["close"].transform(lambda x: x / x.shift(20) - 1)
    sub["ret60"] = g["close"].transform(lambda x: x / x.shift(60) - 1)
    today = sub[sub["trade_date"] == target_dt].copy()
    today["vs_ma60"] = today["close"] / today["MA60"] - 1
    today["above_ma20"] = today["close"] > today["MA20"]

    pers_keep = pers[["ts_code", "Super", "Huoche", "L1", "Early_L1",
                      "layers_covered", "super_rate", "l1_rate", "score"]]
    pers_keep.columns = ["ts_code", "p_Super", "p_Huoche", "p_L1", "p_Early",
                         "p_layers", "p_super_rate", "p_l1_rate", "p_score"]
    merged = today.merge(pers_keep, on="ts_code", how="left")
    merged["p_super_rate"] = merged["p_super_rate"].fillna(0)
    merged["p_l1_rate"] = merged["p_l1_rate"].fillna(0)
    merged["p_layers"] = merged["p_layers"].fillna(0)

    # 评分
    rows = []
    for _, r in merged.iterrows():
        code = r["ts_code"]
        s1, d1 = score_persistence(r["p_super_rate"], r["p_l1_rate"], r["p_layers"])
        s2, d2 = score_today(code in super_codes, code in huoche_codes,
                             code in huoche_first_codes, code in early_l1_codes)
        s3, d3 = score_form(r["vs_ma60"], r["ret20"], bool(r["above_ma20"]))
        s4, d4 = score_trend(r["ret60"])
        s5, d5 = score_liquidity(r.get("amount", 0))
        total = s1 + s2 + s3 + s4 + s5

        # 档位归类
        bucket = "—"
        if r["p_super_rate"] >= 0.45 and r["p_l1_rate"] >= 0.60 and r["p_layers"] >= 3 and r["above_ma20"]:
            bucket = "★核心"
        elif (code in huoche_first_codes) or (code in super_codes and r.get("vs_ma60", 1) <= 0.50):
            bucket = "●机动"
        elif code in early_l1_codes:
            bucket = "○早期"
        elif r["p_l1_rate"] >= 0.30:
            bucket = "△观察"

        rows.append({
            "ts_code": code,
            "bucket": bucket,
            "close": round(r["close"], 2),
            "vs60%": round(r["vs_ma60"] * 100) if pd.notna(r["vs_ma60"]) else None,
            "ret20%": round(r["ret20"] * 100) if pd.notna(r["ret20"]) else None,
            "ret60%": round(r["ret60"] * 100) if pd.notna(r["ret60"]) else None,
            "RPS": f"{int(r['RPS_120'])}/{int(r['RPS_250'])}" if pd.notna(r["RPS_120"]) else "n/a",
            "amt(亿)": round(r["amount"] / 1e8, 1) if pd.notna(r.get("amount", float("nan"))) else None,
            "p_super%": round(r["p_super_rate"] * 100),
            "p_layers": int(r["p_layers"]),
            "今日": "+".join([k for k in ["Super", "Huoche", "Huoche-F", "Early"]
                              if (k == "Super" and code in super_codes)
                              or (k == "Huoche" and code in huoche_codes and code not in huoche_first_codes)
                              or (k == "Huoche-F" and code in huoche_first_codes)
                              or (k == "Early" and code in early_l1_codes)]) or "-",
            "持续分": round(s1),
            "今日分": round(s2),
            "形态分": round(s3),
            "趋势分": round(s4),
            "资金分": round(s5),
            "总分": round(total),
        })

    out = pd.DataFrame(rows).sort_values("总分", ascending=False).reset_index(drop=True)

    print(f"\n{'=' * 90}")
    print(f"评分结果 (Top {args.top}, 共 {len(out)} 只候选)")
    print(f"{'=' * 90}")
    cols_show = ["ts_code", "bucket", "close", "vs60%", "ret20%", "ret60%", "RPS",
                 "amt(亿)", "p_super%", "p_layers", "今日",
                 "持续分", "今日分", "形态分", "趋势分", "资金分", "总分"]
    print(out.head(args.top)[cols_show].to_string(index=False))

    # 按档位汇总
    print(f"\n{'=' * 90}")
    print("Top {} 档位分布:".format(args.top))
    print(f"{'=' * 90}")
    print(out.head(args.top)["bucket"].value_counts().to_string())

    if args.out:
        out.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n[输出] 完整评分写入 {args.out}")


if __name__ == "__main__":
    main()
