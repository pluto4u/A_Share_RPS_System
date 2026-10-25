#!/usr/bin/env python3
"""抽取 2026-08-14 复盘所需的第一/次主线个股、指数与双日对照。不改决策脚本。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))
sys.path.insert(0, str(ROOT))

from build_limit_ecology import RECORD_DTYPE, RECORD_SIZE, load_name_master, load_tail  # noqa: E402
from run_integrated_decision import (  # noqa: E402
    add_indicators, load_industry_map, load_industry_names, industry_label,
)
from rps_analyzer.rps_calculator import calculate_rps  # noqa: E402

TDX = Path(r"C:\new_tdx\vipdoc")
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
INCON = Path(r"C:\new_tdx\incon.dat")
TARGET = pd.Timestamp("2026-08-14")
PREV = pd.Timestamp("2026-08-13")
LEAD = "T070401"
SECOND = "T040502"
INDICES = [
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sz399303", "国证2000"),
]


def read_index(code: str, n: int = 40) -> pd.DataFrame | None:
    market = code[:2]
    path = TDX / market / "lday" / f"{code}.day"
    if not path.exists():
        return None
    n_avail = path.stat().st_size // RECORD_SIZE
    take = min(n, n_avail)
    with path.open("rb") as fh:
        fh.seek((n_avail - take) * RECORD_SIZE)
        rec = np.frombuffer(fh.read(take * RECORD_SIZE), dtype=RECORD_DTYPE)
    return pd.DataFrame({
        "trade_date": pd.to_datetime(rec["date"].astype("int64").astype(str), format="%Y%m%d"),
        "open": rec["open"] / 100.0, "high": rec["high"] / 100.0,
        "low": rec["low"] / 100.0, "close": rec["close"] / 100.0,
        "amount": rec["amount"].astype(np.float64),
    })


def pack_day(row: pd.Series) -> dict:
    close, high, low, open_ = row["close"], row["high"], row["low"], row["open"]
    rng = high - low
    return {
        "ts_code": row["ts_code"], "name": row.get("name", ""),
        "close": round(float(close), 3),
        "open": round(float(open_), 3),
        "high": round(float(high), 3),
        "low": round(float(low), 3),
        "ret1_pct": round(float(row["ret1"] * 100), 2) if pd.notna(row.get("ret1")) else None,
        "open_ret_pct": round(float((open_ / row["prev_close_bar"] - 1) * 100), 2)
            if pd.notna(row.get("prev_close_bar")) and row["prev_close_bar"] else None,
        "close_vs_high_pct": round(float((close / high - 1) * 100), 2) if high else None,
        "close_vs_vwap_proxy": None,  # 无分钟成交额路径，不用假均价
        "upper_shadow_share": round(float((high - close) / rng), 3) if rng else None,
        "amount_yi": round(float(row["amount"] / 1e8), 2),
        "amount_ratio": round(float(row["amount_ratio"]), 2) if pd.notna(row.get("amount_ratio")) else None,
        "vs_ma20_pct": round(float(row["vs_ma20_pct"]), 2) if pd.notna(row.get("vs_ma20_pct")) else None,
        "vs_ma60_pct": round(float((close / row["ma60"] - 1) * 100), 2)
            if pd.notna(row.get("ma60")) and row["ma60"] else None,
        "rps50": None if pd.isna(row.get("RPS_50")) else round(float(row["RPS_50"]), 1),
        "rps120": None if pd.isna(row.get("RPS_120")) else round(float(row["RPS_120"]), 1),
        "rps250": None if pd.isna(row.get("RPS_250")) else round(float(row["RPS_250"]), 1),
        "atr_pct": None if pd.isna(row.get("atr_pct")) else round(float(row["atr_pct"]), 2),
        "dist_high60_pct": None if pd.isna(row.get("dist_high60_pct")) else round(float(row["dist_high60_pct"]), 2),
        "above_ma20": bool(close > row["ma20"]) if pd.notna(row.get("ma20")) else None,
        "above_ma60": bool(close > row["ma60"]) if pd.notna(row.get("ma60")) else None,
        "limit_pct": 0.20 if str(row["ts_code"])[:3] in ("300", "301", "688", "689") else 0.10,
    }


def main() -> int:
    names = load_name_master(HQ)
    daily = load_tail(TDX, n_bars=320, min_bars=260, verbose=True)
    daily = daily[daily["trade_date"] <= TARGET]
    work = add_indicators(daily)
    rps = calculate_rps(daily, verbose=False)
    latest_rps = rps[rps["trade_date"] == TARGET][["ts_code", "RPS_50", "RPS_120", "RPS_250"]]
    snap = work[work["trade_date"] == TARGET].merge(latest_rps, on="ts_code", how="left")
    prev = work[work["trade_date"] == PREV][["ts_code", "close", "amount"]].rename(
        columns={"close": "prev_close_bar", "amount": "prev_amount"})
    snap = snap.merge(prev, on="ts_code", how="left")
    snap["name"] = snap["ts_code"].map(names).fillna("")
    snap["atr_pct"] = snap["atr20"] / snap["close"] * 100
    snap["dist_high60_pct"] = (snap["close"] / snap["high60"] - 1.0) * 100

    imap = load_industry_map(HQ / "tdxhy.cfg")
    inames = load_industry_names(INCON)
    snap = snap.merge(imap, on="ts_code", how="left")
    snap["industry_name"] = [industry_label(c, inames) if isinstance(c, str) else "" for c in snap["industry"]]

    def industry_slice(code: str, n: int = 12) -> list[dict]:
        sub = snap[snap["industry"] == code].copy()
        if sub.empty:
            return []
        sub = sub.sort_values("amount", ascending=False).head(n)
        return [pack_day(r) for _, r in sub.iterrows()]

    # 第一主线里最接近可执行的：站上双均、RPS120 最高、乖离不大
    lead_all = snap[snap["industry"] == LEAD].copy()
    near = lead_all[
        lead_all["ma20"].notna() & (lead_all["close"] > lead_all["ma20"])
        & lead_all["ma60"].notna() & (lead_all["close"] > lead_all["ma60"])
        & lead_all["RPS_120"].notna()
        & lead_all["adv20"].notna() & (lead_all["adv20"] >= 8e7)
    ].copy()
    near["exec_gap"] = 80 - near["RPS_120"]
    near = near.sort_values(["exec_gap", "amount"], ascending=[True, False])

    indices = []
    for code, name in INDICES:
        df = read_index(code)
        if df is None or TARGET not in set(df["trade_date"]):
            indices.append({"code": code, "name": name, "available": False})
            continue
        d = df[df["trade_date"] == TARGET].iloc[0]
        p = df[df["trade_date"] == PREV]
        prev_close = float(p.iloc[0]["close"]) if len(p) else None
        indices.append({
            "code": code, "name": name, "available": True,
            "close": round(float(d["close"]), 2),
            "open": round(float(d["open"]), 2),
            "high": round(float(d["high"]), 2),
            "low": round(float(d["low"]), 2),
            "ret1_pct": None if prev_close is None else round((float(d["close"]) / prev_close - 1) * 100, 2),
            "open_ret_pct": None if prev_close is None else round((float(d["open"]) / prev_close - 1) * 100, 2),
            "close_vs_high_pct": round((float(d["close"]) / float(d["high"]) - 1) * 100, 2),
            "amount_yi": round(float(d["amount"]) / 1e8, 1),
        })

    out = {
        "lead_code": LEAD,
        "lead_name": industry_label(LEAD, inames),
        "second_code": SECOND,
        "second_name": industry_label(SECOND, inames),
        "lead_members": int(len(lead_all)),
        "lead_rps120_ge80": int((lead_all["RPS_120"] >= 80).sum()),
        "lead_above_ma20": int((lead_all["close"] > lead_all["ma20"]).sum()),
        "lead_above_ma60": int((lead_all["close"] > lead_all["ma60"]).sum()),
        "lead_median_rps120": None if lead_all["RPS_120"].isna().all()
            else round(float(lead_all["RPS_120"].median()), 1),
        "lead_median_ret1": round(float(lead_all["ret1"].median() * 100), 2),
        "lead_up_pct": round(float((lead_all["ret1"] > 0).mean() * 100), 1),
        "lead_stocks": industry_slice(LEAD, 12),
        "second_stocks": industry_slice(SECOND, 8),
        "near_executable": [pack_day(r) for _, r in near.head(6).iterrows()],
        "indices": indices,
        "market_amount_yi": round(float(snap["amount"].sum() / 1e8), 1),
        "prev_amount_yi": round(float(work[work["trade_date"] == PREV]["amount"].sum() / 1e8), 1),
    }
    path = ROOT / "output" / "review_0814_slice.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {path}")
    print(f"第一主线 {out['lead_name']} n={out['lead_members']} "
          f"RPS120≥80: {out['lead_rps120_ge80']} 中位RPS120={out['lead_median_rps120']} "
          f"当日中位 {out['lead_median_ret1']}% 上涨{out['lead_up_pct']}%")
    print("接近可执行:")
    for s in out["near_executable"][:6]:
        print(f"  {s['ts_code']} {s['name']:8s} rps120={s['rps120']} "
              f"ret={s['ret1_pct']:+.2f} vsMA20={s['vs_ma20_pct']} "
              f"amt={s['amount_yi']} 距高={s['close_vs_high_pct']}")
    print("指数:")
    for x in indices:
        if x.get("available"):
            print(f"  {x['name']} {x['ret1_pct']:+.2f}% 开{x['open_ret_pct']:+.2f}% "
                  f"距高{x['close_vs_high_pct']}%")
        else:
            print(f"  {x['name']} 未取得")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
