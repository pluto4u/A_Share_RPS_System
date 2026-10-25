#!/usr/bin/env python3
"""hithink-finance 有界冒烟：不打印 Key，大结果落盘。"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SH = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output" / "hithink_smoke"
OUTDIR.mkdir(parents=True, exist_ok=True)
FRI = datetime(2026, 8, 21, tzinfo=SH)
FRI_MS = int(FRI.timestamp() * 1000)
START_MS = int(datetime(2026, 7, 22, tzinfo=SH).timestamp() * 1000)
END_MS = int(datetime(2026, 8, 22, tzinfo=SH).timestamp() * 1000)


def load_key() -> None:
    path = Path(os.environ["APPDATA"]) / "hithink-finance" / "credentials.env"
    line = path.read_text(encoding="utf-8").strip()
    os.environ["HITHINK_FINANCE_API_KEY"] = line.split("=", 1)[1]


def run(args: list[str], name: str) -> dict:
    dest = OUTDIR / f"{name}.json"
    exe = Path(os.environ["APPDATA"]) / "npm" / "hithink-finance.cmd"
    cmd = [str(exe), *args, "--format", "json", "--output", str(dest)]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = round(time.time() - t0, 2)
    envelope = {}
    if dest.exists():
        try:
            envelope = json.loads(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            envelope = {"ok": False, "parse_error": dest.read_text(encoding="utf-8")[:240]}
    else:
        try:
            envelope = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            envelope = {"ok": False, "stdout": proc.stdout[:240]}
    data = envelope.get("data") if isinstance(envelope, dict) else None
    n = None
    sample = None
    if isinstance(data, dict):
        items = data.get("item") or data.get("stock_items") or data.get("klines")
        if isinstance(items, list):
            n = len(items)
            if items and isinstance(items[0], dict):
                sample = {k: items[0].get(k) for k in list(items[0])[:6]}
        if n is None and "path" in data:
            inner = dest
            try:
                payload = json.loads(inner.read_text(encoding="utf-8"))
                inner_data = payload.get("data") or payload
                items = inner_data.get("item") if isinstance(inner_data, dict) else None
                if isinstance(items, list):
                    n = len(items)
                    if items and isinstance(items[0], dict):
                        sample = {k: items[0].get(k) for k in list(items[0])[:6]}
            except Exception:
                pass
    return {
        "name": name,
        "ok": bool(envelope.get("ok")) and proc.returncode == 0,
        "exit": proc.returncode,
        "elapsed_s": elapsed,
        "n": n,
        "sample": sample,
        "stderr": (proc.stderr or "")[:200],
        "message": envelope.get("message") or envelope.get("error"),
        "file": str(dest),
    }


def main() -> None:
    load_key()
    jobs = [
        ("symbol_search", ["symbol", "search", "--q", "亨通光电", "--limit", "3"]),
        ("snapshot", ["market", "snapshot", "--thscodes", "600519.SH,600487.SH,300502.SZ"]),
        ("history_qfq", ["market", "history", "--thscode", "600519.SH", "--start-ms", str(START_MS), "--end-ms", str(END_MS), "--adjust", "forward"]),
        ("calendar", ["market", "calendar"]),
        ("auction", ["market", "auction-snapshot", "--thscodes", "600519.SH", "--stage", "final"]),
        ("adj_factors", ["market", "corporate-actions", "--thscode", "600519.SH"]),
        ("income", ["financials", "income", "--thscode", "600519.SH", "--period", "annual", "--limit", "2"]),
        ("valuation", ["valuation", "snapshot", "--thscodes", "600519.SH,300502.SZ"]),
        ("index_industry", ["index", "catalog", "--tag", "industry"]),
        ("index_concept", ["index", "catalog", "--tag", "cn_concept"]),
        ("hs300", ["index", "constituents", "--thscode", "000300.SH"]),
        ("limit_up", ["special", "limit-up-pool", "--date-ms", str(FRI_MS), "--size", "20", "--sort-field", "continue_day_cnt"]),
        ("limit_down", ["special", "limit-down-pool", "--date-ms", str(FRI_MS), "--size", "10"]),
        ("limit_break", ["special", "limit-break-pool", "--date-ms", str(FRI_MS), "--size", "10"]),
        ("ladder", ["special", "limit-up-ladder"]),
        ("hot", ["special", "hot-stock", "--period", "day"]),
        ("dragon", ["special", "dragon-tiger", "--board-type", "all", "--date", "2026-08-21"]),
        ("etf", ["fund", "snapshot", "--thscodes", "510300.SH"]),
    ]
    rows = []
    for name, args in jobs:
        print(f"RUN {name}...", flush=True)
        row = run(args, name)
        rows.append(row)
        print(f"  ok={row['ok']} n={row['n']} {row['elapsed_s']}s {row['message'] or ''}", flush=True)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": "CLI 0.1.5 有界实测；周五对照 2026-08-21；今日周日故当日池为空属预期",
        "source": "hithink-finance remote",
        "passed": sum(1 for r in rows if r["ok"]),
        "failed": sum(1 for r in rows if not r["ok"]),
        "rows": rows,
    }
    path = ROOT / "output" / "hithink_smoke_20260823.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nPASS {report['passed']}/{len(rows)}  {path}")


if __name__ == "__main__":
    main()
