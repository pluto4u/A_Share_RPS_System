#!/usr/bin/env python3
"""直接打东财/akshare，取 8月14日（周五）行业与概念资金流向。"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "https://data.eastmoney.com/bkzj/hy.html"
UT = "b2884a393a59ad64002292a3e90d46a5"
FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"


def get(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def clist(host: str, fs: str, pz: int = 200) -> tuple[str, list]:
    q = (
        f"https://{host}/api/qt/clist/get?fid=f62&po=1&pz={pz}&pn=1&np=1"
        f"&fltt=2&invt=2&ut={UT}&fs={urllib.parse.quote(fs)}"
        f"&fields={urllib.parse.quote(FIELDS)}&_={int(time.time()*1000)}"
    )
    code, body = get(q)
    if code != 200:
        return f"HTTP {code} {body[:120]}", []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "JSON decode fail", []
    data = (payload.get("data") or {}).get("diff") or []
    if isinstance(data, dict):
        data = list(data.values())
    return f"OK n={len(data)}", data


def rows_of(data: list) -> list[dict]:
    out = []
    for x in data:
        f62 = x.get("f62")
        if f62 in (None, "-"):
            continue
        out.append({
            "code": x.get("f12"),
            "name": x.get("f14"),
            "pct": x.get("f3"),
            "main_yi": round(float(f62) / 1e8, 2),
            "super_yi": None if x.get("f66") in (None, "-") else round(float(x["f66"]) / 1e8, 2),
            "big_yi": None if x.get("f72") in (None, "-") else round(float(x["f72"]) / 1e8, 2),
            "mid_yi": None if x.get("f78") in (None, "-") else round(float(x["f78"]) / 1e8, 2),
            "small_yi": None if x.get("f84") in (None, "-") else round(float(x["f84"]) / 1e8, 2),
            "leader": x.get("f204") or x.get("f205"),
        })
    out.sort(key=lambda r: r["main_yi"], reverse=True)
    return out


def try_akshare() -> dict:
    report: dict = {}
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        return {"import": str(exc)}
    report["version"] = getattr(ak, "__version__", "?")
    jobs = [
        ("industry_rank", "stock_sector_fund_flow_rank", {"indicator": "今日"}),
        ("concept_rank", "stock_sector_fund_flow_rank", {"indicator": "今日"}),
        ("industry_hist", "stock_sector_fund_flow_hist", {"symbol": "通信设备"}),
        ("board_industry", "stock_board_industry_name_em", {}),
        ("fund_flow_ind", "stock_fund_flow_industry", {}),
        ("fund_flow_concept", "stock_fund_flow_concept", {}),
        ("ths_hy", "stock_fund_flow_industry_ths", {}),
    ]
    # probe available names
    names = [n for n in dir(ak) if "fund" in n.lower() or "bkzj" in n.lower() or "sector" in n.lower()]
    report["ak_flow_attrs"] = names[:80]
    for key, fn, kwargs in jobs:
        if not hasattr(ak, fn):
            report[key] = f"no {fn}"
            continue
        try:
            df = getattr(ak, fn)(**kwargs)
            report[key] = {
                "ok": True, "shape": list(df.shape),
                "cols": list(df.columns.astype(str)),
                "head": df.head(3).astype(str).to_dict(orient="records"),
            }
        except Exception as exc:  # noqa: BLE001
            report[key] = f"ERR {type(exc).__name__}: {exc}"
    return report


def main() -> None:
    hosts = ["push2.eastmoney.com", "push2delay.eastmoney.com", "82.push2.eastmoney.com"]
    boards = {"industry": "m:90+t:2", "concept": "m:90+t:3"}
    result = {"probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "hosts": {}}
    for host in hosts:
        result["hosts"][host] = {}
        for kind, fs in boards.items():
            msg, data = clist(host, fs)
            rows = rows_of(data)
            result["hosts"][host][kind] = {
                "status": msg,
                "n": len(rows),
                "inflow_top8": [r for r in rows if r["main_yi"] > 0][:8],
                "outflow_top5": sorted(rows, key=lambda r: r["main_yi"])[:5],
            }
            print(f"[{host} {kind}] {msg}  inflow>0 {sum(1 for r in rows if r['main_yi']>0)}")
            for r in rows[:5]:
                print(f"    {r['name']:12s} 主力{r['main_yi']:+8.2f}亿 超大{r['super_yi']} 大单{r['big_yi']} 涨跌{r['pct']}")
        time.sleep(0.4)

    print("\n=== akshare ===")
    ak_rep = try_akshare()
    result["akshare"] = ak_rep
    print("attrs", ak_rep.get("ak_flow_attrs"))
    for k, v in ak_rep.items():
        if k == "ak_flow_attrs":
            continue
        print(k, v if isinstance(v, str) else v.get("shape") or v)

    path = Path("output/eastmoney_flow_probe_20260815.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n写出", path)


if __name__ == "__main__":
    main()
