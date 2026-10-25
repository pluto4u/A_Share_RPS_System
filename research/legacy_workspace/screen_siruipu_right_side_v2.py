#!/usr/bin/env python3
"""右侧发现器 V2：YJ 主发现、QD 晋级、QR/QQR 仅监控。

输出文件名自动使用 parquet 的最新交易日，不再写死 20260821。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_siruipu_right_side import bars_since_extreme, ema
from screen_siruipu_right_side import (
    industry_label,
    load_industry_map,
    load_industry_names,
    load_name_master,
)


ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")


def add_indicators(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("trade_date").copy()
    c, h, l, v, amount = g["close"], g["high"], g["low"], g["vol"], g["amount"]
    for n in (5, 10, 20, 60, 120):
        g[f"M{n}"] = c.rolling(n, min_periods=n).mean()
    g["VM20"] = v.rolling(20, min_periods=20).mean()
    g["ADV20"] = amount.rolling(20, min_periods=20).mean()
    g["DIF"] = ema(c, 12) - ema(c, 26)
    g["DEA"] = ema(g["DIF"], 9)
    g["MH"] = 2 * (g["DIF"] - g["DEA"])
    hhv60 = h.rolling(60, min_periods=60).max()
    llv30 = l.rolling(30, min_periods=30).min()
    g["DD"] = (hhv60 - llv30) / hhv60 >= 0.28
    g["LOWAGE"] = bars_since_extreme(l.to_numpy(float), 30, "min")
    g["PEAKAGE"] = bars_since_extreme(h.to_numpy(float), 60, "max")
    g["LOWNEAR"] = g["LOWAGE"] <= 15
    g["ORDER_OK"] = g["PEAKAGE"] > g["LOWAGE"]
    g["HH5_REF"] = h.rolling(5, min_periods=5).max().shift(1)
    g["HH10_REF"] = h.rolling(10, min_periods=10).max().shift(1)
    g["HH20_REF"] = h.rolling(20, min_periods=20).max().shift(1)
    g["M5_REF2"] = g["M5"].shift(2)
    g["MH_REF1"] = g["MH"].shift(1)

    # 用户在通达信 ZZ1234 中使用的原始公式。保留独立字段，避免与 V2
    # 的反追高、流动性和低点/高点顺序约束混为同一个策略。
    g["TDX_YJ"] = (
        g["DD"] & g["LOWNEAR"] & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (g["M5"] > g["M5_REF2"]) & (c > g["HH5_REF"])
        & (g["MH"] > 0) & (g["MH"] > g["MH_REF1"])
        & (c / g["M20"] <= 1.12)
    ).fillna(False)
    g["TDX_QD"] = (
        g["DD"] & g["LOWNEAR"] & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (c > g["M120"]) & (c > g["HH10_REF"])
        & (v >= g["VM20"] * 1.10) & (g["MH"] > 0)
    ).fillna(False)
    g["TDX_QR"] = (
        g["DD"] & (c > g["M60"]) & (g["M10"] > g["M20"])
        & (c > g["HH20_REF"]) & (g["MH"] > 0)
    ).fillna(False)
    g["TDX_QQR"] = (g["TDX_QR"] & (v >= g["VM20"] * 1.80) & (g["DIF"] > 0)).fillna(False)
    g["TDX_XG"] = g["TDX_YJ"] | g["TDX_QD"] | g["TDX_QR"] | g["TDX_QQR"]

    structural = g["DD"] & g["LOWNEAR"] & g["ORDER_OK"]
    g["STRUCT"] = structural
    g["YJ"] = (
        structural & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (g["M5"] > g["M5_REF2"]) & (c > g["HH5_REF"])
        & (g["MH"] > 0) & (g["MH"] > g["MH_REF1"])
        & (c / g["M20"] <= 1.04) & (g["ADV20"] >= 1.0e8)
    ).fillna(False)
    g["QD"] = (
        structural & (c > g["M20"]) & (g["M5"] > g["M10"])
        & (c > g["M120"]) & (c > g["HH10_REF"])
        & (v >= g["VM20"] * 1.10) & (v <= g["VM20"] * 3.0)
        & (g["MH"] > 0) & (c / g["M20"] <= 1.15) & (g["ADV20"] >= 1.0e8)
    ).fillna(False)
    g["QR"] = (
        structural & (c > g["M60"]) & (g["M10"] > g["M20"])
        & (c > g["HH20_REF"]) & (g["MH"] > 0)
        & (c / g["M20"] <= 1.18) & (g["ADV20"] >= 1.0e8)
    ).fillna(False)
    g["QQR"] = (g["QR"] & (v >= g["VM20"] * 1.80) & (g["DIF"] > 0)).fillna(False)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="可选：YYYY-MM-DD；默认使用数据最新交易日")
    args = ap.parse_args()
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
            "RPS_50", "RPS_120", "RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    if args.as_of:
        raw = raw[raw["trade_date"] <= pd.Timestamp(args.as_of)]
        if raw.empty:
            raise SystemExit(f"指定日期之前无数据: {args.as_of}")
    asof = raw["trade_date"].max()
    tag = asof.strftime("%Y%m%d")
    bars_n = raw.groupby("ts_code", sort=False).size()
    work = raw.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False).tail(500)
    parts = [add_indicators(g) for _, g in work.groupby("ts_code", sort=False)]
    df = pd.concat(parts, ignore_index=True)

    names = load_name_master(HQ)
    df["name"] = df["ts_code"].map(names).fillna("")
    compact = df["name"].str.replace(" ", "", regex=False).str.upper()
    is_st = compact.str.startswith(("ST", "*ST")) | compact.str.contains("退", regex=False)
    df["BASE"] = (df["ts_code"].map(bars_n) > 250) & ~is_st
    day = df[df["trade_date"] == asof].copy()
    day["DISCOVERY"] = day["BASE"] & day["YJ"]
    day["PRIORITY"] = day["DISCOVERY"] & (day["RPS_120"] >= 60)
    day["UPGRADE"] = day["BASE"] & day["QD"]
    day["MONITOR"] = day["BASE"] & (day["QR"] | day["QQR"])
    day["TDX_EXACT"] = day["BASE"] & day["TDX_XG"]
    day["TDX_STAGE"] = np.select(
        [day["TDX_QQR"], day["TDX_QR"], day["TDX_QD"], day["TDX_YJ"]],
        ["QQR", "QR", "QD", "YJ"], default="",
    )
    day["vs_ma20_pct"] = (day["close"] / day["M20"] - 1) * 100
    day["vol_ratio"] = day["vol"] / day["VM20"]
    day["adv20_yi"] = day["ADV20"] / 1e8

    exact = day[day["TDX_EXACT"]].copy()
    exact["V2_ANY"] = exact["DISCOVERY"] | exact["UPGRADE"] | exact["MONITOR"]
    exact = exact.sort_values(["TDX_STAGE", "RPS_120", "RPS_250"], ascending=[True, False, False])

    # 正式条件为空时仍保留可审计的“近似候选”，但不改变主公式、也不冒充命中。
    near_conditions = {
        "站上MA20": day["close"] > day["M20"],
        "MA5高于MA10": day["M5"] > day["M10"],
        "MA5两日向上": day["M5"] > day["M5_REF2"],
        "突破前5日高": day["close"] > day["HH5_REF"],
        "MACD柱为正": day["MH"] > 0,
        "MACD柱增强": day["MH"] > day["MH_REF1"],
        "距MA20不超4%": day["close"] / day["M20"] <= 1.04,
    }
    day["near_score"] = sum(s.fillna(False).astype(int) for s in near_conditions.values())
    day["near_missing"] = [
        "、".join(label for label, series in near_conditions.items() if not bool(series.loc[idx]))
        for idx in day.index
    ]
    near = day[
        day["BASE"] & day["STRUCT"] & (day["ADV20"] >= 1.0e8) & ~day["DISCOVERY"]
    ].copy()
    near["vs_ma20_pct"] = (near["close"] / near["M20"] - 1) * 100
    near["adv20_yi"] = near["ADV20"] / 1e8
    near = near.sort_values(["near_score", "RPS_120", "ADV20"], ascending=[False, False, False]).head(20)
    day = day[day["DISCOVERY"] | day["UPGRADE"] | day["MONITOR"]].copy()
    day["role"] = np.select(
        [day["DISCOVERY"], day["UPGRADE"], day["QQR"], day["QR"]],
        ["DISCOVERY", "UPGRADE", "QQR_MONITOR", "QR_MONITOR"],
        default="",
    )
    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    day = day.merge(imap, on="ts_code", how="left")
    day["industry_name"] = [industry_label(str(x), inames) if pd.notna(x) else "" for x in day["industry"]]
    role_order = {"DISCOVERY": 0, "UPGRADE": 1, "QR_MONITOR": 2, "QQR_MONITOR": 3}
    day["_order"] = day["role"].map(role_order)
    day = day.sort_values(["_order", "PRIORITY", "RPS_120"], ascending=[True, False, False])
    out_cols = ["ts_code", "name", "industry_name", "role", "PRIORITY", "close", "RPS_50", "RPS_120",
                "RPS_250", "vs_ma20_pct", "vol_ratio", "adv20_yi", "LOWAGE", "PEAKAGE"]
    out = day[out_cols].copy()
    out_path = ROOT / "output" / f"siruipu_right_side_v2_{tag}.csv"
    priority_path = ROOT / "output" / f"siruipu_right_side_v2_priority_{tag}.csv"
    near_path = ROOT / "output" / f"siruipu_right_side_v2_near_{tag}.csv"
    exact_path = ROOT / "output" / f"siruipu_tdx_exact_{tag}.csv"
    exact_diff_path = ROOT / "output" / f"siruipu_tdx_exact_not_v2_{tag}.csv"
    meta_path = ROOT / "output" / f"siruipu_right_side_v2_{tag}.json"
    out.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    out[out["PRIORITY"]].head(50).to_csv(priority_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    near_cols = ["ts_code", "name", "close", "RPS_50", "RPS_120", "RPS_250",
                 "near_score", "near_missing", "vs_ma20_pct", "adv20_yi", "LOWAGE", "PEAKAGE"]
    near[near_cols].to_csv(near_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    exact_cols = ["ts_code", "name", "TDX_STAGE", "V2_ANY", "close", "RPS_50", "RPS_120",
                  "RPS_250", "vs_ma20_pct", "vol_ratio", "adv20_yi", "LOWAGE", "PEAKAGE"]
    exact[exact_cols].to_csv(exact_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    exact.loc[~exact["V2_ANY"], exact_cols].to_csv(
        exact_diff_path, index=False, encoding="utf-8-sig", float_format="%.4f"
    )
    counts = {k: int(day[k].sum()) for k in ("DISCOVERY", "PRIORITY", "UPGRADE", "MONITOR")}
    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": asof.strftime("%Y-%m-%d"),
        "price_source": "hithink local v_daily_qfq",
        "volume_amount": "raw",
        "main_output": "DISCOVERY=YJ; UPGRADE=QD; QR/QQR are monitoring labels only",
        "rps_usage": "ranking/diagnostic only, not a hard condition",
        "counts": counts,
        "csv": str(out_path.as_posix()),
        "priority_csv": str(priority_path.as_posix()),
        "near_csv": str(near_path.as_posix()),
        "tdx_exact_csv": str(exact_path.as_posix()),
        "tdx_exact_not_v2_csv": str(exact_diff_path.as_posix()),
        "tdx_exact_count": int(len(exact)),
        "tdx_exact_not_v2_count": int((~exact["V2_ANY"]).sum()),
        "tdx_exact_note": "exact transcription of the user-provided ZZ1234 union formula; separate from V2",
        "near_count": int(len(near)),
        "near_definition": "BASE and STRUCT and ADV20>=1e8, but not DISCOVERY; ranked by satisfied YJ subconditions",
        "priority_rule": "DISCOVERY and RPS_120>=60; sorted by RPS_120, top 50",
        "historical_st_note": "current screen uses current TDX names; historical ST status is unavailable",
        "disclaimer": "research screen, not an order list or investment advice",
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
