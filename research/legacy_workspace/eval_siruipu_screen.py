#!/usr/bin/env python3
"""评估思瑞浦右侧公式：与资金主导结构、G2 尺子的交叉。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "output" / "siruipu_right_side_20260821.csv"
REG = ROOT / "output" / "siruipu_reg_abnormal_20260821.csv"
RPS = ROOT / "output" / "hithink_rps_latest.csv"
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
OUT = ROOT / "output" / "siruipu_eval_20260821.json"


def main() -> int:
    sc = pd.read_csv(SCREEN)
    rg = pd.read_csv(REG)
    rps = pd.read_csv(RPS)
    px = pd.read_parquet(PARQUET, columns=["trade_date", "ts_code", "amount"])
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    asof = px["trade_date"].max()
    tail = px.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False).tail(20)
    adv = tail.groupby("ts_code")["amount"].mean().rename("adv20")
    day_amt = px[px["trade_date"] == asof][["ts_code", "amount"]].rename(columns={"amount": "amount_d"})

    df = sc.merge(rg[["ts_code", "board", "dev3", "dev10", "dev20", "dev30", "flag"]], on="ts_code", how="left")
    df = df.merge(rps[["ts_code", "amount"]], on="ts_code", how="left")
    df = df.merge(adv, on="ts_code", how="left")
    df = df.merge(day_amt, on="ts_code", how="left")
    df["amt_yi"] = df["amount"] / 1e8
    df["adv20_yi"] = df["adv20"] / 1e8
    df["amt_adv"] = df["amount"] / df["adv20"]

    # G2-like research overlay (not executable; G0 failed)
    df["g2_rps"] = df["RPS_120"] >= 80
    df["g2_ma20"] = df["vs_ma20_pct"] <= 12
    df["g2_liq"] = df["adv20_yi"] >= 1.5
    df["g2_all"] = df["g2_rps"] & df["g2_ma20"] & df["g2_liq"]

    ind = (
        df.groupby("industry_name")
        .agg(
            n=("ts_code", "size"),
            rps120_med=("RPS_120", "median"),
            amt_yi=("amt_yi", "sum"),
            vs_ma20_med=("vs_ma20_pct", "median"),
            qqr=("primary", lambda s: int((s == "QQR").sum())),
        )
        .sort_values(["n", "amt_yi"], ascending=False)
    )
    share = ind["n"] / ind["n"].sum()
    hhi = float((share ** 2).sum())

    def bucket(s, bins, labels):
        return pd.cut(s, bins=bins, labels=labels, right=False).value_counts().sort_index()

    rps_b = bucket(df["RPS_120"], [0, 50, 80, 90, 101], ["<50", "50-80", "80-90", ">=90"])
    ma_b = bucket(df["vs_ma20_pct"], [-10, 8, 12, 20, 40, 200], ["<8", "8-12", "12-20", "20-40", ">=40"])
    liq_b = bucket(df["adv20_yi"], [0, 0.5, 1.5, 3, 8, 1e6], ["<0.5亿", "0.5-1.5亿", "1.5-3亿", "3-8亿", ">=8亿"])
    isolated = int((ind["n"] == 1).sum())
    clustered = ind[ind["n"] >= 3]

    out = {
        "as_of": asof.strftime("%Y-%m-%d"),
        "n": int(len(df)),
        "primary": df["primary"].value_counts().to_dict(),
        "yj": int((df["stages"].str.contains("YJ")).sum()) if "stages" in df else 0,
        "qd": int(df["stages"].str.contains("QD").sum()),
        "rps120_ge80": int(df["g2_rps"].sum()),
        "rps120_lt50": int((df["RPS_120"] < 50).sum()),
        "ma20_le12": int(df["g2_ma20"].sum()),
        "adv20_ge1_5": int(df["g2_liq"].sum()),
        "g2_all": int(df["g2_all"].sum()),
        "g2_names": df.loc[df["g2_all"], ["ts_code", "name", "industry_name", "primary", "RPS_120", "vs_ma20_pct", "adv20_yi"]]
        .round(2)
        .to_dict("records"),
        "rps_buckets": {str(k): int(v) for k, v in rps_b.items()},
        "ma20_buckets": {str(k): int(v) for k, v in ma_b.items()},
        "liq_buckets": {str(k): int(v) for k, v in liq_b.items()},
        "industry_n": int(df["industry_name"].nunique()),
        "industry_hhi": round(hhi, 4),
        "industry_ge3": clustered.reset_index().round(2).to_dict("records"),
        "industry_isolated": isolated,
        "amt_yi_sum": round(float(df["amt_yi"].sum()), 2),
        "amt_yi_median": round(float(df["amt_yi"].median()), 2),
        "adv20_median": round(float(df["adv20_yi"].median()), 2),
        "vs_ma20_median": round(float(df["vs_ma20_pct"].median()), 2),
        "rps120_median": round(float(df["RPS_120"].median()), 1),
        "qqr_rps_lt80": int(((df["primary"] == "QQR") & (df["RPS_120"] < 80)).sum()),
        "qqr_ma20_gt20": int(((df["primary"] == "QQR") & (df["vs_ma20_pct"] > 20)).sum()),
        "reg_any": int((rg.loc[rg["in_xg"], "flag"] != "未接近").sum()) if "in_xg" in rg else None,
        "top_amt": df.sort_values("amt_yi", ascending=False)[
            ["ts_code", "name", "industry_name", "primary", "amt_yi", "adv20_yi", "RPS_120", "vs_ma20_pct"]
        ]
        .head(8)
        .round(2)
        .to_dict("records"),
        "thin": df[df["adv20_yi"] < 0.5][["ts_code", "name", "adv20_yi", "RPS_120"]]
        .sort_values("adv20_yi")
        .head(8)
        .round(3)
        .to_dict("records"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in (
        "n", "primary", "rps120_ge80", "rps120_lt50", "ma20_le12", "adv20_ge1_5",
        "g2_all", "industry_n", "industry_hhi", "industry_isolated", "amt_yi_median",
        "adv20_median", "vs_ma20_median", "rps120_median", "qqr_rps_lt80", "qqr_ma20_gt20",
        "rps_buckets", "ma20_buckets", "liq_buckets",
    )}, ensure_ascii=False, indent=2))
    print("ge3", out["industry_ge3"][:8])
    print("g2", out["g2_names"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
