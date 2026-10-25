#!/usr/bin/env python3
"""东财回流板块成分股：周五个股主力净流入>0，且本地日线收盘站上 MA5 与 MA20。"""
from __future__ import annotations

import json
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))
from build_limit_ecology import load_name_master  # noqa: E402

TDX = Path(r"C:\new_tdx\vipdoc")
HQ = Path(r"C:\new_tdx\T0002\hq_cache")
TARGET = pd.Timestamp("2026-08-14")
RECORD_SIZE = 32
DTYPE = np.dtype([
    ("date", "<u4"), ("open", "<u4"), ("high", "<u4"),
    ("low", "<u4"), ("close", "<u4"), ("amount", "<f4"),
    ("vol", "<u4"), ("reserved", "<u4"),
])
UT = "b2884a393a59ad64002292a3e90d46a5"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
})

BOARDS = [
    ("industry", "BK1215", "通信"),
    ("industry", "BK0448", "通信设备"),
    ("industry", "BK1592", "通信线缆及配套"),
    ("industry", "BK1591", "通信网络设备及器件"),
    ("industry", "BK0736", "通信服务"),
    ("industry", "BK1589", "通信应用增值服务"),
    ("industry", "BK1339", "被动元件"),
    ("industry", "BK1409", "激光设备"),
    ("concept", "BK1136", "光通信模块"),
    ("concept", "BK1128", "CPO概念"),
]


def to_ts(code: str) -> str | None:
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return f"{code}.SH"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return f"{code}.SZ"
    return None


def members(bk: str) -> list[dict]:
    rows = []
    for pn in range(1, 8):
        r = SESSION.get(
            "https://push2delay.eastmoney.com/api/qt/clist/get",
            params={
                "fid": "f62", "po": 1, "pz": 100, "pn": pn, "np": 1,
                "fltt": 2, "invt": 2, "ut": UT,
                "fs": f"b:{bk}+f:!50",
                "fields": "f12,f14,f2,f3,f62,f66,f72,f184",
                "_": int(time.time() * 1000),
            },
            timeout=15,
        )
        r.raise_for_status()
        diff = (r.json().get("data") or {}).get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        for x in diff:
            code = str(x.get("f12") or "")
            ts = to_ts(code)
            if not ts:
                continue
            main = x.get("f62")
            rows.append({
                "code": code, "ts_code": ts, "name_em": x.get("f14"),
                "pct_em": None if x.get("f3") in (None, "-") else float(x["f3"]),
                "main_yi": None if main in (None, "-") else round(float(main) / 1e8, 2),
                "super_yi": None if x.get("f66") in (None, "-") else round(float(x["f66"]) / 1e8, 2),
                "big_yi": None if x.get("f72") in (None, "-") else round(float(x["f72"]) / 1e8, 2),
            })
        total = (r.json().get("data") or {}).get("total") or 0
        if pn * 100 >= total:
            break
        time.sleep(0.12)
    return rows


def read_tail(ts_code: str, n_bars: int = 40) -> pd.DataFrame | None:
    code, mkt = ts_code.split(".")
    path = TDX / mkt.lower() / "lday" / f"{mkt.lower()}{code}.day"
    if not path.exists():
        return None
    n_avail = path.stat().st_size // RECORD_SIZE
    if n_avail < 25:
        return None
    take = min(n_bars, n_avail)
    with path.open("rb") as handle:
        handle.seek((n_avail - take) * RECORD_SIZE)
        rec = np.frombuffer(handle.read(take * RECORD_SIZE), dtype=DTYPE)
    df = pd.DataFrame({
        "trade_date": pd.to_datetime(rec["date"].astype(str), format="%Y%m%d", errors="coerce"),
        "close": rec["close"] / 100.0,
        "amount": rec["amount"].astype(np.float64),
    }).dropna()
    df = df[df["trade_date"] <= TARGET]
    if df.empty or df["trade_date"].iloc[-1] != TARGET:
        return None
    if len(df) < 20:
        return None
    df["ma5"] = df["close"].rolling(5, min_periods=5).mean()
    df["ma20"] = df["close"].rolling(20, min_periods=20).mean()
    df["adv20"] = df["amount"].shift(1).rolling(20, min_periods=15).median()
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return pd.Series({
        "close": float(last["close"]),
        "prev_close": float(prev["close"]),
        "ma5": float(last["ma5"]) if pd.notna(last["ma5"]) else np.nan,
        "ma20": float(last["ma20"]) if pd.notna(last["ma20"]) else np.nan,
        "amount": float(last["amount"]),
        "adv20": float(last["adv20"]) if pd.notna(last["adv20"]) else np.nan,
    })


def tag(row: dict) -> str:
    if row["ret1_pct"] >= 19.5:
        return "涨停/近涨停"
    if row["vs_ma20_pct"] > 20:
        return "乖离过大"
    if row["ret1_pct"] < 0 and row.get("amount_ratio") and row["amount_ratio"] >= 3:
        return "放量收阴"
    if row["vs_ma20_pct"] <= 12 and row["vs_ma5_pct"] <= 8:
        return "均线附近"
    return "站上均线"


def main() -> None:
    names = load_name_master(HQ)
    board_rows = []
    stock_map: dict[str, dict] = {}
    for kind, bk, bname in BOARDS:
        print(f"成分 {bname} {bk}…", flush=True)
        mem = members(bk)
        print(f"  n={len(mem)}  流入>0 {sum(1 for x in mem if (x['main_yi'] or 0) > 0)}")
        board_rows.append({"kind": kind, "code": bk, "name": bname, "members": len(mem)})
        for x in mem:
            rec = stock_map.setdefault(x["ts_code"], {
                **x, "boards": [], "board_kinds": set(),
            })
            rec["boards"].append(bname)
            rec["board_kinds"].add(kind)
            if x["main_yi"] is not None:
                rec["main_yi"] = x["main_yi"] if rec.get("main_yi") is None else rec["main_yi"]
                rec["super_yi"] = x.get("super_yi") if rec.get("super_yi") is None else rec["super_yi"]
                rec["big_yi"] = x.get("big_yi") if rec.get("big_yi") is None else rec["big_yi"]
        time.sleep(0.15)

    cores = []
    scanned = 0
    for ts, rec in stock_map.items():
        bar = read_tail(ts)
        scanned += 1
        if bar is None:
            rec["local"] = False
            continue
        rec["local"] = True
        rec["close"] = round(float(bar["close"]), 3)
        rec["ret1_pct"] = round((bar["close"] / bar["prev_close"] - 1) * 100, 2)
        rec["ma5"] = None if np.isnan(bar["ma5"]) else round(float(bar["ma5"]), 3)
        rec["ma20"] = None if np.isnan(bar["ma20"]) else round(float(bar["ma20"]), 3)
        rec["vs_ma5_pct"] = None if rec["ma5"] is None else round((rec["close"] / rec["ma5"] - 1) * 100, 2)
        rec["vs_ma20_pct"] = None if rec["ma20"] is None else round((rec["close"] / rec["ma20"] - 1) * 100, 2)
        rec["amount_yi"] = round(float(bar["amount"]) / 1e8, 2)
        rec["adv20_yi"] = None if np.isnan(bar["adv20"]) else round(float(bar["adv20"]) / 1e8, 2)
        rec["amount_ratio"] = None if rec["adv20_yi"] in (None, 0) else round(rec["amount_yi"] / rec["adv20_yi"], 2)
        rec["name"] = names.get(ts) or rec.get("name_em")
        compact = (rec["name"] or "").replace(" ", "").upper()
        rec["is_st"] = compact.startswith(("ST", "*ST")) or "退" in compact
        rec["above_ma"] = (
            rec["ma5"] is not None and rec["ma20"] is not None
            and rec["close"] > rec["ma5"] and rec["close"] > rec["ma20"]
        )
        rec["inflow"] = rec.get("main_yi") is not None and rec["main_yi"] > 0
        rec["both_in"] = (
            rec.get("super_yi") is not None and rec.get("big_yi") is not None
            and rec["super_yi"] > 0 and rec["big_yi"] > 0
        )
        if rec["above_ma"] and rec["inflow"] and not rec["is_st"] and (rec["adv20_yi"] or 0) >= 1.5:
            item = {
                "ts_code": ts, "name": rec["name"],
                "boards": rec["boards"],
                "in_industry": "industry" in rec["board_kinds"],
                "close": rec["close"], "ret1_pct": rec["ret1_pct"],
                "vs_ma5_pct": rec["vs_ma5_pct"], "vs_ma20_pct": rec["vs_ma20_pct"],
                "amount_yi": rec["amount_yi"], "amount_ratio": rec["amount_ratio"],
                "adv20_yi": rec["adv20_yi"], "main_yi": rec["main_yi"],
                "super_yi": rec.get("super_yi"), "big_yi": rec.get("big_yi"),
                "both_in": rec["both_in"], "tag": "",
            }
            item["tag"] = tag(item)
            cores.append(item)

    cores.sort(key=lambda x: (x["in_industry"], x["main_yi"] or 0, x["amount_yi"]), reverse=True)
    industry_cores = [x for x in cores if x["in_industry"]]
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": "2026-08-14",
        "source": {
            "boards": "eastmoney push2delay 今日f62 + 5日f164 已筛回流板块",
            "members": "eastmoney clist fs=b:BKxxxx 成分及个股f62",
            "price": "本地 TDX 未复权日线；G0 verified_qfq 抽样 14/14；最新收盘与前复权对齐，MA 用本地收盘",
        },
        "definition": "核心股 = 回流板块成分 + 东财个股主力净流入>0 + 收盘>MA5且>MA20 + ADV20≥1.5亿 + 非ST",
        "boards": board_rows,
        "universe": len(stock_map),
        "scanned_local": scanned,
        "core_count": len(cores),
        "industry_core_count": len(industry_cores),
        "cores": cores[:40],
        "industry_cores": industry_cores[:25],
    }
    path = ROOT / "output/eastmoney_reflow_ma_core_20260814.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"成分去重 {len(stock_map)}  核心股 {len(cores)}  其中行业 {len(industry_cores)}")
    for x in industry_cores[:15]:
        print(f"  {x['ts_code']} {x['name']:8s} 流入{x['main_yi']:+6.2f} "
              f"MA5{x['vs_ma5_pct']:+5.1f} MA20{x['vs_ma20_pct']:+5.1f}  {x['tag']}  {','.join(x['boards'][:2])}")
    print("写出", path)


if __name__ == "__main__":
    main()
