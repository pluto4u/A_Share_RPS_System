#!/usr/bin/env python3
"""对照 2026-08-21：同花顺官方涨停/跌停/炸板池 vs 本地日线重建。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/a-share-rotation-trader/scripts"))
from build_limit_ecology import (  # noqa: E402
    DEFAULT_HQ, load_name_master, load_tail, mark_limits,
)

SH = timezone(timedelta(hours=8))
TARGET = pd.Timestamp("2026-08-21")
DATE_MS = int(datetime(2026, 8, 21, tzinfo=SH).timestamp() * 1000)
EXE = Path(os.environ["APPDATA"]) / "npm" / "hithink-finance.cmd"
OUT = ROOT / "output" / "ths_local_limit_compare_20260821.json"


def load_key() -> None:
    line = (Path(os.environ["APPDATA"]) / "hithink-finance" / "credentials.env").read_text(encoding="utf-8").strip()
    os.environ["HITHINK_FINANCE_API_KEY"] = line.split("=", 1)[1]


def ths_pool(kind: str) -> list[dict]:
    rows = []
    page = 1
    while True:
        dest = ROOT / "output" / "hithink_smoke" / f"{kind}_p{page}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(EXE), "special", kind, "--date-ms", str(DATE_MS),
            "--page", str(page), "--size", "200", "--format", "json", "--output", str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        payload = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else {}
        if not payload.get("ok"):
            raise RuntimeError(f"{kind} page {page} fail {proc.stderr[:200]}")
        data = payload.get("data") or {}
        items = data.get("item") or []
        rows.extend(items)
        pag = data.get("pagination") or {}
        print(f"  {kind} page {page} +{len(items)} total={pag.get('total')}", flush=True)
        if page >= int(pag.get("pages") or 1) or not items:
            break
        page += 1
    return rows


def to_ts(code: str) -> str | None:
    raw = str(code or "")
    if "." in raw:
        ticker, suf = raw.split(".", 1)
        suf = suf.upper()
        if suf == "BJ":
            return None
        if ticker.startswith(("600", "601", "603", "605", "688", "689")):
            return f"{ticker}.SH"
        if ticker.startswith(("000", "001", "002", "003", "300", "301")):
            return f"{ticker}.SZ"
        return None
    return None


def pack(items: list[dict], height_key="continue_day_cnt") -> dict[str, dict]:
    out = {}
    skipped_bj = 0
    for x in items:
        ts = to_ts(x.get("thscode") or x.get("ticker"))
        if ts is None:
            if str(x.get("thscode") or "").endswith(".BJ"):
                skipped_bj += 1
            continue
        out[ts] = {
            "ts_code": ts,
            "name": x.get("name"),
            "is_st": bool(x.get("is_st")),
            "height": x.get(height_key),
            "pct": x.get("price_change_ratio_pct"),
            "open_times": x.get("open_times"),
        }
    return out


def main() -> None:
    load_key()
    print("THS pools…", flush=True)
    ths_up = pack(ths_pool("limit-up-pool"))
    ths_dn = pack(ths_pool("limit-down-pool"))
    ths_br = pack(ths_pool("limit-break-pool"))

    print("Local TDX reconstruct…", flush=True)
    names = load_name_master(DEFAULT_HQ)
    daily = load_tail(Path(r"C:\new_tdx\vipdoc"), n_bars=90, min_bars=25, verbose=True)
    work = mark_limits(daily, names)
    day = work[(work["trade_date"] == TARGET) & ~work["suspect_adjust"]].copy()
    if day.empty:
        raise RuntimeError("本地无 2026-08-21")

    loc_up = day[day["is_limit_up"]]
    loc_dn = day[day["is_limit_dn"]]
    loc_br = day[day["broken"]]

    def local_map(frame: pd.DataFrame) -> dict[str, dict]:
        out = {}
        for r in frame.itertuples():
            out[r.ts_code] = {
                "ts_code": r.ts_code, "name": r.name, "is_st": bool(r.is_risk),
                "height": int(r.board_height), "pct": round(float(r.ret1) * 100, 2),
                "limit_regime": r.limit_regime, "one_word": bool(r.one_word),
            }
        return out

    l_up, l_dn, l_br = local_map(loc_up), local_map(loc_dn), local_map(loc_br)

    def diff(local: dict, remote: dict) -> dict:
        ls, rs = set(local), set(remote)
        only_l = sorted(ls - rs)
        only_r = sorted(rs - ls)
        both = sorted(ls & rs)
        height_mismatch = []
        for code in both:
            lh, rh = local[code].get("height"), remote[code].get("height")
            if lh and rh and int(lh) != int(rh):
                height_mismatch.append({
                    "ts_code": code,
                    "name": local[code].get("name") or remote[code].get("name"),
                    "local_height": lh, "ths_height": rh,
                })
        return {
            "local_n": len(ls), "ths_n": len(rs), "overlap": len(both),
            "only_local": [{"ts_code": c, **local[c]} for c in only_l],
            "only_ths": [{"ts_code": c, **remote[c]} for c in only_r],
            "height_mismatch": height_mismatch,
        }

    up_d, dn_d, br_d = diff(l_up, ths_up), diff(l_dn, ths_dn), diff(l_br, ths_br)

    # 只看沪深非 ST 的涨停对齐（东财口径对照时用过）
    l_up_ns = {k: v for k, v in l_up.items() if not v["is_st"]}
    t_up_ns = {k: v for k, v in ths_up.items() if not v["is_st"]}
    up_ns = diff(l_up_ns, t_up_ns)

    report = {
        "trade_date": "2026-08-21",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "local_source": "TDX vipdoc 未复权日线重建 mark_limits",
        "remote_source": "hithink-finance special limit-*-pool",
        "coverage": "沪深 A 股；同花顺北交所已剔除",
        "qfq_note": "涨跌停用未复权收盘，不走复权闸门",
        "limit_up": up_d,
        "limit_up_ex_st": up_ns,
        "limit_down": dn_d,
        "limit_break": br_d,
        "local_day_stats": {
            "stocks": int(len(day)),
            "limit_up": int(len(l_up)),
            "limit_down": int(len(l_dn)),
            "broken": int(len(l_br)),
            "one_word": int(loc_up["one_word"].sum()),
            "max_board_relay": int(loc_up.loc[~loc_up["is_risk"] & ~loc_up["one_word"], "board_height"].max()) if len(loc_up) else 0,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n涨停 本地", up_d["local_n"], "同花顺", up_d["ths_n"], "交集", up_d["overlap"],
          "仅本地", len(up_d["only_local"]), "仅THS", len(up_d["only_ths"]))
    print("涨停剔ST 本地", up_ns["local_n"], "同花顺", up_ns["ths_n"], "交集", up_ns["overlap"])
    print("跌停 本地", dn_d["local_n"], "同花顺", dn_d["ths_n"], "交集", dn_d["overlap"])
    print("炸板 本地", br_d["local_n"], "同花顺", br_d["ths_n"], "交集", br_d["overlap"])
    print("高度不一致", len(up_d["height_mismatch"]))
    print("写出", OUT)


if __name__ == "__main__":
    main()
