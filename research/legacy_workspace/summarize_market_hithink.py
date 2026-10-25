#!/usr/bin/env python3
"""基于同花顺本地前复权面板生成当日行业横截面摘要。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from screen_siruipu_right_side import industry_label, load_industry_map, load_industry_names


ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")


def main() -> int:
    cols = ["trade_date", "ts_code", "close", "amount", "RPS_120"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    dates = raw["trade_date"].drop_duplicates().sort_values().tail(2).tolist()
    if len(dates) < 2:
        raise SystemExit("至少需要两个交易日")
    prev_date, asof = dates
    two = raw[raw["trade_date"].isin(dates)].copy()
    close = two.pivot(index="ts_code", columns="trade_date", values="close")
    ret = (close[asof] / close[prev_date] - 1).rename("ret")
    prior_amount = (
        two[two["trade_date"] == prev_date]
        .set_index("ts_code")["amount"]
        .rename("previous_amount")
    )
    latest = (
        two[two["trade_date"] == asof]
        .set_index("ts_code")
        .join([ret, prior_amount])
        .reset_index()
    )

    valid_ret = latest["ret"].dropna()
    amount_by_date = two.groupby("trade_date")["amount"].sum()
    current_amount = float(amount_by_date.get(asof, 0.0))
    previous_amount = float(amount_by_date.get(prev_date, 0.0))
    amount_change = current_amount / previous_amount - 1 if previous_amount else None

    def board_name(code: str) -> str:
        raw_code = code.split(".", 1)[0]
        if raw_code.startswith(("300", "301")):
            return "创业板"
        if raw_code.startswith("688"):
            return "科创板"
        return "主板及其他"

    latest["board"] = latest["ts_code"].map(board_name)
    board_stats = []
    for board, frame in latest.dropna(subset=["ret"]).groupby("board"):
        board_stats.append({
            "board": board,
            "n": int(len(frame)),
            "median_ret": round(float(frame["ret"].median()), 6),
            "up_ratio": round(float((frame["ret"] > 0).mean()), 6),
            "amount_yi": round(float(frame["amount"].sum()) / 1e8, 2),
        })

    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    latest = latest.merge(imap, on="ts_code", how="left")
    latest["industry_name"] = [
        industry_label(str(x), inames) if pd.notna(x) else "未映射" for x in latest["industry"]
    ]
    agg = latest.dropna(subset=["ret"]).groupby("industry_name", as_index=False).agg(
        n=("ts_code", "size"),
        mean_ret=("ret", "mean"),
        median_ret=("ret", "median"),
        up_ratio=("ret", lambda s: float((s > 0).mean())),
        amount=("amount", "sum"),
        previous_amount=("previous_amount", "sum"),
        rps120_median=("RPS_120", "median"),
    )
    ranked = agg[agg["n"] >= 8].sort_values(["median_ret", "up_ratio"], ascending=False)
    top = ranked.head(8)
    bottom = ranked.tail(8).sort_values(["median_ret", "up_ratio"])
    focus = ranked[ranked["industry_name"].isin(["医药", "医疗保健", "半导体", "软件服务"])]

    def records(frame: pd.DataFrame) -> list[dict]:
        out = []
        for row in frame.itertuples(index=False):
            out.append({
                "industry": row.industry_name,
                "n": int(row.n),
                "mean_ret": round(float(row.mean_ret), 6),
                "median_ret": round(float(row.median_ret), 6),
                "up_ratio": round(float(row.up_ratio), 6),
                "amount_yi": round(float(row.amount) / 1e8, 2),
                "previous_amount_yi": round(float(row.previous_amount) / 1e8, 2),
                "amount_change": None if not row.previous_amount else round(float(row.amount / row.previous_amount - 1), 6),
                "rps120_median": None if pd.isna(row.rps120_median) else round(float(row.rps120_median), 2),
            })
        return out

    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": asof.strftime("%Y-%m-%d"),
        "previous_date": prev_date.strftime("%Y-%m-%d"),
        "price_source": "hithink local v_daily_qfq",
        "industry_source": "TDX tdxhy.cfg / incon.dat current mapping",
        "coverage": "沪深A股，不含北交所；行业至少8只有效样本",
        "market_breadth": {
            "comparable": int(len(valid_ret)),
            "advance": int((valid_ret > 0).sum()),
            "decline": int((valid_ret < 0).sum()),
            "flat": int((valid_ret == 0).sum()),
            "mean_ret": round(float(valid_ret.mean()), 6),
            "median_ret": round(float(valid_ret.median()), 6),
            "up_5pct": int((valid_ret >= 0.05).sum()),
            "down_5pct": int((valid_ret <= -0.05).sum()),
            "amount_yi": round(current_amount / 1e8, 2),
            "previous_amount_yi": round(previous_amount / 1e8, 2),
            "amount_change": None if amount_change is None else round(float(amount_change), 6),
        },
        "board_stats": sorted(board_stats, key=lambda x: x["board"]),
        "top_industries": records(top),
        "bottom_industries": records(bottom),
        "focus_industries": records(focus),
    }
    out = ROOT / "output" / f"hithink_market_summary_{asof.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
