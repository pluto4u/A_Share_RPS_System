#!/usr/bin/env python3
"""探测本技能声明的每个数据源是否真的可得，输出可得性矩阵。

用途：决定 skill 里哪些功能能保留、哪些必须整块删除。
只做只读探测，不写业务数据。
"""

from __future__ import annotations

import argparse
import json
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT = 8


def http_get(url: str) -> tuple[bool, str, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
        return True, body.decode("utf-8", errors="replace"), len(body)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}", 0
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", 0


def probe_tdx_daily(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(root)}
    if not root.exists():
        return {**out, "available": False, "reason": "目录不存在"}
    counts, latest = {}, None
    for market in ("sh", "sz"):
        lday = root / market / "lday"
        if not lday.exists():
            counts[market] = 0
            continue
        files = list(lday.glob("*.day"))
        counts[market] = len(files)
        for path in files[:400]:
            size = path.stat().st_size
            if size < 32:
                continue
            with path.open("rb") as handle:
                handle.seek(size - 32)
                date = struct.unpack("<i", handle.read(4))[0]
            if latest is None or date > latest:
                latest = date
    total = sum(counts.values())
    return {
        **out,
        "available": total > 0,
        "file_counts": counts,
        "total_files": total,
        "latest_trade_date": latest,
        "reason": "" if total else "未找到 .day 文件",
    }


def probe_tdx_minute(root: Path) -> dict[str, Any]:
    """通达信本地分钟目录（fzline=5分钟, minline=1分钟）。"""
    detail = {}
    for sub in ("fzline", "minline"):
        n = 0
        for market in ("sh", "sz"):
            d = root / market / sub
            if d.exists():
                n += len(list(d.glob("*.lc*")))
        detail[sub] = n
    total = sum(detail.values())
    return {
        "path": str(root),
        "available": total > 0,
        "file_counts": detail,
        "reason": "" if total else "未找到本地分钟文件",
    }


def probe_hq_cache(hq: Path, incon: Path) -> dict[str, Any]:
    wanted = ["tdxhy.cfg", "block_gn.dat", "block_zs.dat", "block_fg.dat"]
    found = {}
    for name in wanted:
        path = hq / name
        if path.exists():
            stat = path.stat()
            found[name] = {
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
    result = {
        "path": str(hq),
        "available": bool(found),
        "files": found,
        "incon_exists": incon.exists(),
    }
    if not found:
        result["reason"] = "未找到行业/概念配置文件"
    return result


def probe_tencent_snapshot() -> dict[str, Any]:
    ok, body, n = http_get("https://qt.gtimg.cn/q=sh000001,sz399001")
    parsed = body.count("~") if ok else 0
    return {
        "endpoint": "https://qt.gtimg.cn/q=",
        "available": ok and parsed > 10,
        "bytes": n,
        "sample": body[:120].strip() if ok else body,
    }


def probe_tencent_minute(symbol: str = "sh600519") -> dict[str, Any]:
    ok, body, n = http_get(
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    )
    if not ok:
        return {"available": False, "reason": body}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return {"available": False, "reason": f"JSON 解析失败: {exc}"}
    node = (payload.get("data") or {}).get(symbol) or {}
    block = (node.get("data") or {})
    rows = block.get("data") or []
    fields = len(rows[0].split()) if rows else 0
    return {
        "endpoint": "web.ifzq.gtimg.cn/appstock/app/minute/query",
        "available": payload.get("code") == 0 and len(rows) > 0,
        "code": payload.get("code"),
        "date": block.get("date"),
        "rows": len(rows),
        "fields_per_row": fields,
        "first_row": rows[0] if rows else None,
        "last_row": rows[-1] if rows else None,
        "bytes": n,
    }


def probe_tencent_qfq(symbol: str = "sh600519") -> dict[str, Any]:
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,320,qfq"
    )
    ok, body, n = http_get(url)
    if not ok:
        return {"available": False, "reason": body}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return {"available": False, "reason": f"JSON 解析失败: {exc}"}
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    key = "qfqday" if node.get("qfqday") else ("day" if node.get("day") else None)
    return {
        "endpoint": "web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        "available": payload.get("code") == 0 and len(rows) > 50,
        "code": payload.get("code"),
        "key_used": key,
        "rows": len(rows),
        "first": rows[0] if rows else None,
        "last": rows[-1] if rows else None,
        "bytes": n,
    }


def probe_eastmoney_flow() -> dict[str, Any]:
    url = (
        "https://push2.eastmoney.com/api/qt/clist/get?fid=f62&po=1&pz=5&pn=1"
        "&np=1&fltt=2&invt=2&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        "&fields=f12,f14,f62,f184"
    )
    ok, body, n = http_get(url)
    if not ok:
        return {"available": False, "reason": body}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return {"available": False, "reason": f"JSON 解析失败: {exc}"}
    data = payload.get("data") or {}
    items = data.get("diff") or []
    return {
        "endpoint": "push2.eastmoney.com/api/qt/clist/get",
        "available": bool(items),
        "total": data.get("total"),
        "rows": len(items),
        "sample": items[0] if items else None,
        "bytes": n,
    }


def probe_eastmoney_limitpool(date: str) -> dict[str, Any]:
    """涨停池：J1/J2/FR 的唯一现实来源。"""
    url = (
        "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
        f"&dpt=wz.ztzt&Pageindex=0&pagesize=10&sort=fbt%3Aasc&date={date}"
    )
    ok, body, n = http_get(url)
    if not ok:
        return {"available": False, "reason": body}
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return {"available": False, "reason": f"JSON 解析失败: {exc}", "raw": body[:200]}
    data = payload.get("data")
    if not data:
        return {"available": False, "reason": f"data 为空 (rc={payload.get('rc')})", "raw": body[:200]}
    pool = data.get("pool") or []
    return {
        "endpoint": "push2ex.eastmoney.com/getTopicZTPool",
        "available": bool(pool),
        "total": data.get("tc"),
        "rows": len(pool),
        "fields": sorted(pool[0].keys()) if pool else [],
        "sample": pool[0] if pool else None,
        "bytes": n,
    }


def probe_ths_concept() -> dict[str, Any]:
    ok, body, n = http_get("https://q.10jqka.com.cn/gn/detail/code/301558/")
    blocked = (not ok) or ("请开启JavaScript" in body) or ("验证" in body[:2000])
    return {
        "endpoint": "q.10jqka.com.cn/gn/",
        "available": (not blocked) and n > 5000,
        "bytes": n,
        "reason": "" if not blocked else "反爬/需要 JS 或验证",
    }


def probe_akshare() -> dict[str, Any]:
    try:
        import akshare  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"未安装: {type(exc).__name__}"}
    return {"available": True, "version": getattr(akshare, "__version__", "unknown")}


def probe_local_artifacts(root: Path) -> dict[str, Any]:
    checks = {
        "rps_csv": sorted(root.glob("output/rps_*.csv"))[-3:],
        "quiet_base_csv": sorted(root.glob("output/quiet_base_*.csv"))[-3:],
        "emotion_data": sorted(root.glob("emotion_validation/**/*.parquet"))[:3],
        "cache_parquet": sorted(root.glob("data/**/*.parquet"))[:3],
    }
    return {
        key: {"count": len(paths), "latest": paths[-1].name if paths else None}
        for key, paths in checks.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", default=r"C:\new_tdx\vipdoc")
    parser.add_argument("--hq-cache", default=r"C:\new_tdx\T0002\hq_cache")
    parser.add_argument("--incon", default=r"C:\new_tdx\incon.dat")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--output", default="output/data_source_probe.json")
    args = parser.parse_args()

    tdx_root = Path(args.tdx_root)
    report: dict[str, Any] = {
        "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "probe_date_arg": args.date,
        "sources": {},
    }
    sources = report["sources"]

    steps = [
        ("tdx_daily", lambda: probe_tdx_daily(tdx_root)),
        ("tdx_minute", lambda: probe_tdx_minute(tdx_root)),
        ("tdx_hq_cache", lambda: probe_hq_cache(Path(args.hq_cache), Path(args.incon))),
        ("tencent_snapshot", probe_tencent_snapshot),
        ("tencent_minute", probe_tencent_minute),
        ("tencent_qfq_kline", probe_tencent_qfq),
        ("eastmoney_moneyflow", probe_eastmoney_flow),
        ("eastmoney_limit_pool", lambda: probe_eastmoney_limitpool(args.date)),
        ("ths_concept", probe_ths_concept),
        ("akshare", probe_akshare),
    ]
    for name, fn in steps:
        started = time.time()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            result = {"available": False, "reason": f"探测异常 {type(exc).__name__}: {exc}"}
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        sources[name] = result
        flag = "OK  " if result.get("available") else "FAIL"
        print(f"[{flag}] {name:22s} {result.get('reason','') or ''}".rstrip())

    report["local_artifacts"] = probe_local_artifacts(Path.cwd())

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
