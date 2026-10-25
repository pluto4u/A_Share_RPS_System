#!/usr/bin/env python3
"""决定性探测：涨停池/炸板池能否取历史日期，以及东财资金流能否恢复。

若涨停池只能取当日，则 ST_cycle 无法回测校准，按“数据拿不到就摒弃”原则整块删除。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
UT = "7eea3edcaed734bea9cbfc24409ed989"
TIMEOUT = 12


def get_json(url: str) -> tuple[bool, Any, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"
    try:
        return True, json.loads(raw), ""
    except ValueError:
        return False, None, f"非 JSON: {raw[:160]}"


def pool(kind: str, date: str) -> dict[str, Any]:
    dpt = {"zt": "wz.ztzt", "zb": "wz.zbgc", "dt": "wz.dtgc"}[kind]
    path = {"zt": "getTopicZTPool", "zb": "getTopicZBPool", "dt": "getTopicDTPool"}[kind]
    sort = {"zt": "fbt:asc", "zb": "fbt:asc", "dt": "fund:asc"}[kind]
    url = (
        f"https://push2ex.eastmoney.com/{path}?ut={UT}&dpt={dpt}"
        f"&Pageindex=0&pagesize=500&sort={sort.replace(':', '%3A')}&date={date}"
    )
    ok, payload, err = get_json(url)
    if not ok:
        return {"available": False, "reason": err}
    data = payload.get("data")
    if not data:
        return {"available": False, "reason": f"data 为空 rc={payload.get('rc')}"}
    rows = data.get("pool") or []
    return {"available": bool(rows), "total": data.get("tc"), "rows": len(rows)}


def moneyflow_variants() -> list[dict[str, Any]]:
    variants = {
        "clist_f62_stock": (
            "https://push2.eastmoney.com/api/qt/clist/get?fid=f62&po=1&pz=5&pn=1&np=1"
            "&fltt=2&invt=2&fs=m%3A0%2Bt%3A6%2Cm%3A0%2Bt%3A80%2Cm%3A1%2Bt%3A2%2Cm%3A1%2Bt%3A23"
            "&fields=f12%2Cf14%2Cf62%2Cf184"
        ),
        "clist_f62_industry": (
            "https://push2.eastmoney.com/api/qt/clist/get?fid=f62&po=1&pz=5&pn=1&np=1"
            "&fltt=2&invt=2&fs=m%3A90%2Bt%3A2&fields=f12%2Cf14%2Cf62%2Cf184"
        ),
        "ulist_single": (
            "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
            "&fields=f12%2Cf14%2Cf62&secids=1.600519%2C0.000001"
        ),
    }
    out = []
    for name, url in variants.items():
        ok, payload, err = get_json(url)
        if not ok:
            out.append({"name": name, "available": False, "reason": err})
            continue
        data = payload.get("data") or {}
        items = data.get("diff") or []
        if isinstance(items, dict):
            items = list(items.values())
        out.append({
            "name": name,
            "available": bool(items),
            "rows": len(items),
            "sample": items[0] if items else None,
        })
    return out


def main() -> int:
    # 覆盖 当日 / 近月 / 半年前 / 一年前，判断历史深度
    dates = ["20260814", "20260813", "20260731", "20260630", "20260415", "20260115", "20250814"]
    print("=== 涨停池历史深度 (getTopicZTPool) ===")
    zt: dict[str, Any] = {}
    for d in dates:
        zt[d] = pool("zt", d)
        r = zt[d]
        print(f"  {d}: {'OK' if r['available'] else 'FAIL'} total={r.get('total')} rows={r.get('rows')} {r.get('reason','')}")
        time.sleep(0.4)

    print("\n=== 炸板池历史深度 (getTopicZBPool) ===")
    zb: dict[str, Any] = {}
    for d in dates:
        zb[d] = pool("zb", d)
        r = zb[d]
        print(f"  {d}: {'OK' if r['available'] else 'FAIL'} total={r.get('total')} rows={r.get('rows')} {r.get('reason','')}")
        time.sleep(0.4)

    print("\n=== 跌停池 (getTopicDTPool) ===")
    dt: dict[str, Any] = {}
    for d in dates[:3]:
        dt[d] = pool("dt", d)
        r = dt[d]
        print(f"  {d}: {'OK' if r['available'] else 'FAIL'} total={r.get('total')} rows={r.get('rows')} {r.get('reason','')}")
        time.sleep(0.4)

    print("\n=== 东财资金流变体重试 ===")
    mf = moneyflow_variants()
    for item in mf:
        print(f"  {item['name']}: {'OK' if item['available'] else 'FAIL'} {item.get('reason','')} rows={item.get('rows')}")

    report = {"limit_up_pool": zt, "broken_pool": zb, "limit_down_pool": dt, "moneyflow": mf}
    with open("output/limit_pool_probe.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n写出: output/limit_pool_probe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
