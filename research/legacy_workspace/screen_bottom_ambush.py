#!/usr/bin/env python3
"""底部右侧埋伏筛选器。

三阶段：WATCH（跌势衰竭）→ TURN（首次转强）→ AMBUSH（2-8日后缩量回踩）。
G0 复权闸门未通过时不读取行情、不计算指标、不回测，也没有覆盖开关。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_siruipu_right_side import bars_since_extreme
from screen_siruipu_right_side import (
    industry_label,
    load_industry_map,
    load_industry_names,
    load_name_master,
)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
GATE = ROOT / "output" / "qfq_verification.json"
OUT = ROOT / "output"
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
VERSION = "bottom_ambush_v1"


def now_cn() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def require_qfq() -> dict:
    if not GATE.exists():
        raise RuntimeError(f"缺少G0文件: {GATE}")
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if gate.get("price_adjustment") != "verified_qfq":
        payload = {
            "generated_at": now_cn(),
            "version": VERSION,
            "status": "blocked",
            "action": "零交易",
            "gate": gate.get("price_adjustment", "missing"),
            "blocker": gate.get("blocker", "G0未通过"),
            "gated_metrics": gate.get("gated_metrics", []),
            "note": "没有研究覆盖开关；G0恢复后才能计算WATCH/TURN/AMBUSH或回测。",
        }
        path = OUT / "bottom_ambush_blocked.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    return gate


def turn_age(values: np.ndarray) -> np.ndarray:
    out = np.full(len(values), 9999, dtype=np.int32)
    last = -9999
    for i, flag in enumerate(values.astype(bool)):
        if flag:
            last = i
        out[i] = i - last
    return out


def add_indicators(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("trade_date").copy()
    c, h, l, v, amount = g["close"], g["high"], g["low"], g["vol"], g["amount"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

    g["MA5"] = c.rolling(5, min_periods=5).mean()
    g["MA20"] = c.rolling(20, min_periods=20).mean()
    g["MA60"] = c.rolling(60, min_periods=60).mean()
    g["VM5"] = v.rolling(5, min_periods=5).mean()
    g["VM20"] = v.rolling(20, min_periods=20).mean()
    g["ADV20"] = amount.rolling(20, min_periods=20).mean()
    g["ATR20"] = tr.rolling(20, min_periods=20).mean()
    g["HH250"] = h.rolling(250, min_periods=250).max()
    g["LL10"] = l.rolling(10, min_periods=10).min()
    g["LOWAGE30"] = bars_since_extreme(l.to_numpy(float), 30, "min")
    g["RANGE10"] = h.rolling(10, min_periods=10).max() / g["LL10"] - 1
    up_vol = v.where(c >= pc, 0.0).rolling(10, min_periods=10).sum()
    down_vol = v.where(c < pc, 0.0).rolling(10, min_periods=10).sum()
    ret1 = c / pc - 1
    crash10 = (ret1 <= -0.08).rolling(10, min_periods=10).sum() > 0

    g["DRAWDOWN"] = ((c / g["HH250"] >= 0.40) & (c / g["HH250"] <= 0.75)).fillna(False)
    g["STABLE"] = (
        (g["LOWAGE30"] >= 3)
        & (g["LL10"] >= g["LL10"].shift(10) * 0.98)
        & (g["RANGE10"] <= 0.15)
        & (g["ATR20"] / c <= 0.06)
        & (g["ATR20"] <= g["ATR20"].shift(10) * 1.05)
        & ~crash10
    ).fillna(False)
    g["ABSORB"] = (
        (g["VM5"].shift(1) <= g["VM20"].shift(1) * 0.90)
        & (up_vol >= down_vol * 0.80)
    ).fillna(False)
    cross20 = (c > g["MA20"]) & (pc <= g["MA20"].shift(1))
    break10 = c > h.rolling(10, min_periods=10).max().shift(1)
    g["TURN"] = (
        g["DRAWDOWN"] & g["STABLE"] & g["ABSORB"]
        & (c > g["MA20"]) & (g["MA5"] > g["MA5"].shift(1))
        & (cross20 | break10)
        & (c / g["MA20"] <= 1.06)
        & (v >= g["VM20"] * 1.05) & (v <= g["VM20"] * 2.50)
        & (ret1 >= 0.005) & (ret1 <= 0.08)
    ).fillna(False)
    g["TURNAGE"] = turn_age(g["TURN"].to_numpy())
    rps50_20 = g["RPS_50"].shift(20)
    g["RELATIVE"] = ((g["RPS_50"] >= 40) & (g["RPS_50"] - rps50_20 >= 15)).fillna(False)
    g["AMBUSH"] = (
        g["DRAWDOWN"] & g["STABLE"] & g["RELATIVE"]
        & g["TURNAGE"].between(2, 8)
        & (l <= g["MA20"] * 1.02) & (c >= g["MA20"] * 0.995)
        & (c / g["MA20"] <= 1.04)
        & (v <= g["VM20"] * 0.95) & (ret1 >= -0.02)
    ).fillna(False)
    g["WATCH"] = g["DRAWDOWN"] & g["STABLE"] & g["ABSORB"] & ~g["TURN"] & ~g["AMBUSH"]
    g["ret1"] = ret1
    g["drawdown_pct"] = (c / g["HH250"] - 1) * 100
    g["atr_pct"] = g["ATR20"] / c * 100
    g["vs_ma20_pct"] = (c / g["MA20"] - 1) * 100
    g["vol_ratio"] = v / g["VM20"]
    g["rps50_delta20"] = g["RPS_50"] - rps50_20
    return g


def event_study(df: pd.DataFrame, start: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    for code, g in df.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date").reset_index(drop=True)
        last_signal = -999
        for i in np.flatnonzero(g["AMBUSH"].to_numpy(bool)):
            if g.at[i, "trade_date"] < start or i - last_signal < 20 or i + 20 >= len(g):
                continue
            last_signal = i
            entry = float(g.at[i + 1, "open"])
            if not np.isfinite(entry) or entry <= 0:
                continue
            item = {"signal_date": g.at[i, "trade_date"], "ts_code": code,
                    "entry_date": g.at[i + 1, "trade_date"], "entry_open": entry}
            for horizon in (1, 5, 10, 20):
                item[f"ret_{horizon}d_pct"] = (float(g.at[i + horizon, "close"]) / entry - 1) * 100
            window = g.iloc[i + 1:i + 21]
            item["mfe20_pct"] = (float(window["high"].max()) / entry - 1) * 100
            item["mae20_pct"] = (float(window["low"].min()) / entry - 1) * 100
            rows.append(item)
    events = pd.DataFrame(rows)
    if events.empty:
        return events, {"events": 0, "note": "无事件，不能评价策略"}
    summary = {"events": int(len(events))}
    for horizon in (1, 5, 10, 20):
        s = events[f"ret_{horizon}d_pct"]
        summary[f"ret_{horizon}d_mean_pct"] = round(float(s.mean()), 3)
        summary[f"ret_{horizon}d_median_pct"] = round(float(s.median()), 3)
        summary[f"ret_{horizon}d_win_rate"] = round(float((s > 0).mean()), 4)
    summary["mfe20_median_pct"] = round(float(events["mfe20_pct"].median()), 3)
    summary["mae20_median_pct"] = round(float(events["mae20_pct"].median()), 3)
    summary["note"] = "事件研究使用信号次日开盘进入；未计费用、涨跌停不可成交与组合容量，不等同可交易回测。"
    return events, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="底部右侧埋伏筛选器")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD；默认数据最新日")
    ap.add_argument("--backtest", action="store_true", help="G0通过后运行事件研究")
    ap.add_argument("--backtest-start", default="2021-01-01")
    args = ap.parse_args()

    gate = require_qfq()
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
            "RPS_50", "RPS_120", "RPS_250"]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    if args.as_of:
        raw = raw[raw["trade_date"] <= pd.Timestamp(args.as_of)]
    if raw.empty:
        raise SystemExit("指定窗口无数据")
    asof = raw["trade_date"].max()
    bars_n = raw.groupby("ts_code", sort=False).size()
    work = raw.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False).tail(650)
    calc = pd.concat([add_indicators(g) for _, g in work.groupby("ts_code", sort=False)], ignore_index=True)

    names = load_name_master(HQ)
    calc["name"] = calc["ts_code"].map(names).fillna("")
    compact = calc["name"].str.replace(" ", "", regex=False).str.upper()
    calc["BASE"] = (
        (calc["ts_code"].map(bars_n) > 250)
        & ~compact.str.startswith(("ST", "*ST"))
        & ~compact.str.contains("退", regex=False)
        & (calc["ADV20"] >= 1.5e8)
    ).fillna(False)
    day = calc[calc["trade_date"] == asof].copy()
    day["stage"] = np.select(
        [day["BASE"] & day["AMBUSH"], day["BASE"] & day["TURN"], day["BASE"] & day["WATCH"]],
        ["AMBUSH", "TURN", "WATCH"], default="",
    )
    day = day[day["stage"] != ""].copy()
    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    day = day.merge(imap, on="ts_code", how="left")
    day["industry_name"] = [industry_label(str(x), inames) if pd.notna(x) else "" for x in day["industry"]]
    day["executable"] = False
    day["execute_blocker"] = "尚未接入第一行业共振与账户G5；筛选结果只作观察"
    stage_order = {"AMBUSH": 0, "TURN": 1, "WATCH": 2}
    day["_order"] = day["stage"].map(stage_order)
    day = day.sort_values(["_order", "RPS_50", "ADV20"], ascending=[True, False, False])
    cols_out = ["trade_date", "ts_code", "name", "industry_name", "stage", "executable",
                "close", "drawdown_pct", "atr_pct", "vs_ma20_pct", "vol_ratio",
                "RPS_50", "rps50_delta20", "RPS_120", "ADV20", "execute_blocker"]
    tag = asof.strftime("%Y%m%d")
    csv_path = OUT / f"bottom_ambush_{tag}.csv"
    day[cols_out].to_csv(csv_path, index=False, encoding="utf-8-sig", float_format="%.4f")

    backtest = None
    if args.backtest:
        events, backtest = event_study(calc[calc["BASE"]], pd.Timestamp(args.backtest_start))
        events.to_parquet(OUT / f"bottom_ambush_events_{tag}.parquet", index=False)

    payload = {
        "generated_at": now_cn(), "version": VERSION, "status": "research_only",
        "as_of": asof.strftime("%Y-%m-%d"), "price_gate": gate.get("price_adjustment"),
        "universe": "沪深A股，不含北交所", "counts": day["stage"].value_counts().to_dict(),
        "csv": str(csv_path.as_posix()), "backtest": backtest,
        "execution": "all executable=false until first-industry confirmation and account G5",
        "historical_st_note": "历史ST状态不可得；当前筛选使用当前名称，事件研究需额外处理幸存者偏差。",
    }
    meta_path = OUT / f"bottom_ambush_{tag}.json"
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
