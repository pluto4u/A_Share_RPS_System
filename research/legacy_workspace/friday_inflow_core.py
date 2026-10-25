#!/usr/bin/env python3
"""周五（2026-08-14）放量行业中，站上 MA5 与 MA20 的核心股。

「资金流入」口径：估算净流入接口已删除。本脚本用两件可回测事实代替：
  1. 行业成交额 / ADV20（相对自身常态是否放量）；
  2. 行业成交额相对上一交易日是否增加。
两者同时偏强，才称为周五放量行业。成交额不是账户资金净流入。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))
sys.path.insert(0, str(ROOT))

from build_limit_ecology import load_name_master, load_tail  # noqa: E402
from run_integrated_decision import (  # noqa: E402
    add_indicators, industry_label, load_industry_map, load_industry_names,
)

TDX = Path(r"C:\new_tdx\vipdoc")
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
TARGET = pd.Timestamp("2026-08-14")
PREV = pd.Timestamp("2026-08-13")
MIN_MEMBERS = 8
MIN_ADV = 1.5e8
TOP_INDUSTRIES = 8
CORE_PER_INDUSTRY = 5


def main() -> int:
    names = load_name_master(HQ)
    daily = load_tail(TDX, n_bars=80, min_bars=40, verbose=True)
    daily = daily[daily["trade_date"] <= TARGET]
    work = add_indicators(daily)
    grp = work.groupby("ts_code", sort=False)["close"]
    work["ma5"] = grp.transform(lambda s: s.rolling(5, min_periods=5).mean())
    work["vs_ma5_pct"] = (work["close"] / work["ma5"] - 1.0) * 100.0

    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    snap = work[work["trade_date"] == TARGET].merge(imap, on="ts_code", how="inner")
    prev = work[work["trade_date"] == PREV][["ts_code", "amount"]].rename(columns={"amount": "prev_amount"})
    snap = snap.merge(prev, on="ts_code", how="left")
    snap["name"] = snap["ts_code"].map(names).fillna("")
    compact = snap["name"].str.replace(" ", "", regex=False).str.upper()
    snap["is_st"] = compact.str.startswith(("ST", "*ST")) | compact.str.contains("退")

    rows = []
    for code, g in snap.groupby("industry"):
        if len(g) < MIN_MEMBERS:
            continue
        amt = g["amount"].sum()
        adv = g["adv20"].sum()
        prev_amt = g["prev_amount"].sum()
        rows.append({
            "industry": code,
            "name": industry_label(code, inames),
            "members": int(len(g)),
            "amount_yi": float(amt / 1e8),
            "amount_vs_adv20": float(amt / adv) if adv else np.nan,
            "amount_vs_prev": float(amt / prev_amt) if prev_amt else np.nan,
        "vs_market": None,
            "up_pct": float((g["ret1"] > 0).mean() * 100),
            "above_ma5_pct": float((g["close"] > g["ma5"]).mean() * 100),
            "above_ma20_pct": float((g["close"] > g["ma20"]).mean() * 100),
            "median_ret1_pct": float(g["ret1"].median() * 100),
            "top1_share_pct": float(g["amount"].max() / amt * 100) if amt else np.nan,
        })
    ind = pd.DataFrame(rows).dropna(subset=["amount_vs_adv20", "amount_vs_prev"])
    market_vs_prev = float(snap["amount"].sum() / snap["prev_amount"].sum())
    ind["vs_market"] = ind["amount_vs_prev"] / market_vs_prev
    ind["single_stock_driven"] = ind["top1_share_pct"] > 35.0
    # 相对流入：比大盘少缩（或真增）且相对自身 ADV20 放量。
    # 周五两市成交额约缩 16%，要求「较昨日不降」会几乎筛空，那不是资金选择，是大盘收缩。
    pool = ind[(ind["amount_vs_adv20"] >= 1.20) & (ind["vs_market"] >= 1.10)
               & ~ind["single_stock_driven"]].copy()
    pool["inflow_score"] = (
        pool["amount_vs_adv20"].rank(pct=True) * 40
        + pool["vs_market"].rank(pct=True) * 40
        + pool["above_ma20_pct"].rank(pct=True) * 20
    )
    pool = pool.sort_values("inflow_score", ascending=False).head(TOP_INDUSTRIES).reset_index(drop=True)

    lead_codes = set(pool["industry"])
    stocks = snap[snap["industry"].isin(lead_codes) & ~snap["is_st"]].copy()
    stocks["industry_name"] = [industry_label(c, inames) for c in stocks["industry"]]
    ma_ok = (
        stocks["ma5"].notna() & stocks["ma20"].notna()
        & (stocks["close"] > stocks["ma5"])
        & (stocks["close"] > stocks["ma20"])
        & stocks["adv20"].notna() & (stocks["adv20"] >= MIN_ADV)
    )
    qualified = stocks[ma_ok].copy()

    cores = []
    for code in pool["industry"]:
        sub = qualified[qualified["industry"] == code].sort_values("amount", ascending=False)
        n_qual = int(len(sub))
        n_all = int((snap["industry"] == code).sum())
        for rank, (_, r) in enumerate(sub.head(CORE_PER_INDUSTRY).iterrows(), 1):
            cores.append({
                "industry": code,
                "industry_name": r["industry_name"],
                "industry_inflow_rank": int(pool.index[pool["industry"] == code][0]) + 1,
                "core_rank_in_industry": rank,
                "qualified_in_industry": n_qual,
                "members": n_all,
                "ts_code": r["ts_code"],
                "name": r["name"],
                "close": round(float(r["close"]), 3),
                "ret1_pct": round(float(r["ret1"] * 100), 2),
                "vs_ma5_pct": round(float(r["vs_ma5_pct"]), 2),
                "vs_ma20_pct": round(float(r["vs_ma20_pct"]), 2),
                "amount_yi": round(float(r["amount"] / 1e8), 2),
                "amount_ratio": None if pd.isna(r["amount_ratio"]) else round(float(r["amount_ratio"]), 2),
                "adv20_yi": round(float(r["adv20"] / 1e8), 2),
                "industry_amount_share_pct": round(float(r["amount"] / stocks.loc[
                    stocks["industry"] == code, "amount"].sum() * 100), 1),
            })

    out = {
        "as_of": "2026-08-14",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inflow_definition": "行业成交额/ADV20≥1.20 且成交额相对大盘超额≥10%（vs_market≥1.10），排除单股成交占比>35%",
        "market_amount_vs_prev": round(market_vs_prev, 3),
        "not_net_inflow": True,
        "industries": json.loads(pool.to_json(orient="records", force_ascii=False)),
        "core_stocks": cores,
        "n_industries_scanned": int(len(ind)),
        "n_inflow_industries": int(len(pool)),
        "n_ma_qualified": int(len(qualified)),
        "n_core": int(len(cores)),
    }
    path = ROOT / "output" / "friday_inflow_core_20260814.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"大盘成交额相对昨日 {market_vs_prev:.3f}")
    print(f"扫描行业 {out['n_industries_scanned']} · 相对放量行业 {out['n_inflow_industries']} · "
          f"MA5+MA20 合格 {out['n_ma_qualified']} · 核心输出 {out['n_core']}")
    print("\n放量行业:")
    for r in out["industries"]:
        print(f"  {r['name']:10s} amt/adv={r['amount_vs_adv20']:.2f}  "
              f"vs昨={r['amount_vs_prev']:.2f} 相对大盘={r.get('vs_market', 0):.2f}  "
              f"额={r['amount_yi']:.0f}亿 涨{r['up_pct']:.0f}% MA20宽{r['above_ma20_pct']:.0f}%")
    print("\n核心股:")
    for s in cores:
        print(f"  [{s['industry_name']}] {s['ts_code']} {s['name']:8s} "
              f"close={s['close']:8.2f} ret={s['ret1_pct']:+6.2f} "
              f"vsMA5={s['vs_ma5_pct']:+5.1f} vsMA20={s['vs_ma20_pct']:+5.1f} "
              f"额={s['amount_yi']:.1f}亿 占行业{s['industry_amount_share_pct']:.0f}%")
    print(f"\n写出 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
