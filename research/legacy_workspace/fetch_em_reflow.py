#!/usr/bin/env python3
"""东财板块：周五流入，且相对近4日回流；超大单/大单同向。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
UT = "b2884a393a59ad64002292a3e90d46a5"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/bkzj/hy.html"})


def clist(sector: str, indicator: str) -> list[dict]:
    """indicator: today | 5d"""
    if indicator == "today":
        fid, stat, fields = "f62", "1", "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
    else:
        fid, stat, fields = "f164", "5", "f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124"
    fs = f"m:90 t:{sector}"
    rows = []
    for pn in (1, 2, 3):
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": pn, "pz": 100, "po": 1, "np": 1, "ut": UT,
            "fltt": 2, "invt": 2, "fid": fid, "fid0": fid, "fs": fs,
            "stat": stat, "fields": fields, "_": int(time.time() * 1000),
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        diff = (payload.get("data") or {}).get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        for x in diff:
            code = str(x.get("f12"))
            name = x.get("f14")
            if indicator == "today":
                if x.get("f62") in (None, "-"):
                    continue
                main = float(x["f62"])
                super_ = float(x.get("f66") or 0)
                big = float(x.get("f72") or 0)
                rows.append({
                    "code": code, "name": name,
                    "pct": None if x.get("f3") in (None, "-") else float(x["f3"]),
                    "main_yi": round(main / 1e8, 2),
                    "super_yi": round(super_ / 1e8, 2),
                    "big_yi": round(big / 1e8, 2),
                    "mid_yi": round(float(x.get("f78") or 0) / 1e8, 2),
                    "small_yi": round(float(x.get("f84") or 0) / 1e8, 2),
                    "both_in": super_ > 0 and big > 0,
                    "split": (super_ > 0 > big) or (super_ < 0 < big),
                    "leader": x.get("f204") or x.get("f205"),
                })
            else:
                if x.get("f164") in (None, "-"):
                    continue
                rows.append({
                    "code": code, "name": name,
                    "d5_main_yi": round(float(x["f164"]) / 1e8, 2),
                    "d5_super_yi": round(float(x.get("f166") or 0) / 1e8, 2),
                    "d5_big_yi": round(float(x.get("f168") or 0) / 1e8, 2),
                    "d5_pct": None if x.get("f109") in (None, "-") else float(x["f109"]),
                })
        total = (payload.get("data") or {}).get("total") or 0
        if pn * 100 >= total:
            break
        time.sleep(0.2)
    print(f"  {fs} {indicator} n={len(rows)}")
    return rows


def daykline(bk: str) -> dict[str, dict]:
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": 8, "klt": 101, "secid": f"90.{bk}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": UT, "_": int(time.time() * 1000),
    }
    r = SESSION.get(url, params=params, timeout=12)
    r.raise_for_status()
    klines = ((r.json().get("data") or {}).get("klines")) or []
    out = {}
    for line in klines:
        p = str(line).split(",")
        if len(p) < 6:
            continue
        out[p[0][:10]] = {
            "main_yi": round(float(p[1]) / 1e8, 2),
            "super_yi": round(float(p[5]) / 1e8, 2) if p[5] not in ("", "-") else None,
            "big_yi": round(float(p[4]) / 1e8, 2) if p[4] not in ("", "-") else None,
        }
    return out


def classify(row: dict) -> str | None:
    if row["main_yi"] <= 0:
        return None
    tags = []
    thu = row.get("thu_main")
    prior4 = row.get("prior4_yi")
    if thu is not None and thu < 0:
        tags.append("周四流出→周五流入")
    elif thu is not None and thu > 0 and row["main_yi"] >= thu * 1.5 and row["main_yi"] - thu >= 8:
        tags.append("周五较周四明显放大")
    if prior4 is not None and prior4 < 0:
        tags.append("近4日净流出、周五转流入")
    elif prior4 is not None and row["main_yi"] >= 8 and row["main_yi"] > abs(prior4) and prior4 <= row["main_yi"] * 0.4:
        tags.append("周五占近5日主导")
    if row["both_in"]:
        tags.append("超大单大单同向")
    if not any("周四" in t or "近4日" in t or "近5日" in t or "放大" in t for t in tags):
        return None
    return " · ".join(tags)


def merge(today: list[dict], d5: list[dict]) -> list[dict]:
    d5m = {x["code"]: x for x in d5}
    out = []
    for row in today:
        extra = d5m.get(row["code"], {})
        use = dict(row)
        use["d5_main_yi"] = extra.get("d5_main_yi")
        if use["d5_main_yi"] is not None:
            use["prior4_yi"] = round(use["d5_main_yi"] - use["main_yi"], 2)
        else:
            use["prior4_yi"] = None
        out.append(use)
    out.sort(key=lambda r: r["main_yi"], reverse=True)
    return out


def main() -> None:
    print("今日行业…")
    hy_t = clist("2", "today")
    print("5日行业…")
    hy_5 = clist("2", "5d")
    print("今日概念…")
    gn_t = clist("3", "today")
    print("5日概念…")
    gn_5 = clist("3", "5d")

    hy = merge(hy_t, hy_5)
    gn = merge(gn_t, gn_5)

    print("\n尝试日K核对龙头（push2his，限流则跳过）")
    for row in hy[:8] + gn[:8]:
        try:
            hist = daykline(row["code"])
            thu = hist.get("2026-08-13")
            fri = hist.get("2026-08-14")
            row["thu_main"] = thu["main_yi"] if thu else None
            row["kline_fri"] = fri["main_yi"] if fri else None
            print(f"  {row['name']} 周四={row['thu_main']} 日K周五={row['kline_fri']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {row['name']} 日K失败 {type(exc).__name__}")
        time.sleep(1.2)

    for row in hy + gn:
        row["reflow"] = classify(row)

    hy_in = [x for x in hy if x["main_yi"] > 0]
    gn_in = [x for x in gn if x["main_yi"] > 0]
    hy_re = [x for x in hy if x.get("reflow")]
    gn_re = [x for x in gn if x.get("reflow")]
    hy_re.sort(key=lambda r: r["main_yi"], reverse=True)
    gn_re.sort(key=lambda r: r["main_yi"], reverse=True)

    print("\n=== 行业 流入且回流 ===")
    for x in hy_re[:20]:
        print(f"  {x['name']:14s} 五{x['main_yi']:+7.1f} 近4日{x.get('prior4_yi')}  "
              f"{'同向' if x['both_in'] else '分裂'}  {x['reflow']}")
    print("\n=== 概念 流入且回流 top15 ===")
    for x in gn_re[:15]:
        print(f"  {x['name']:14s} 五{x['main_yi']:+7.1f} 近4日{x.get('prior4_yi')}  "
              f"{'同向' if x['both_in'] else '分裂'}  {x['reflow']}")

    report = {
        "source": "eastmoney push2delay clist 今日f62 + 5日f164；日K push2his（能通则核对周四）",
        "label": "估算主力净流入，成交方向模型，不是交易所账户流水",
        "as_of_snapshot": "周末冻结，对应 2026-08-14 收盘",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "definition": {
            "inflow": "周五主力净流入>0",
            "reflow": "周四流出转流入（日K），或近4日（5日合计-周五）净流出后周五转流入，或周五占近5日主导；标注超大单与大单是否同向",
            "prior4": "东财5日主力净额 − 今日主力净额，近似周四至上周五共4日",
        },
        "industry_inflow_top20": hy_in[:20],
        "concept_inflow_top20": gn_in[:20],
        "industry_reflow": hy_re,
        "concept_reflow": gn_re[:30],
        "counts": {
            "industry_today": len(hy), "concept_today": len(gn),
            "industry_inflow": len(hy_in), "concept_inflow": len(gn_in),
            "industry_reflow": len(hy_re), "concept_reflow": len(gn_re),
        },
    }
    path = Path("output/eastmoney_friday_reflow_20260814.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写出", path, "回流行业", len(hy_re), "回流概念", len(gn_re))


if __name__ == "__main__":
    main()
