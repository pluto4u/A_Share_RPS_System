#!/usr/bin/env python3
"""探测本地通达信 5 分钟(fzline)/1 分钟(minline)数据的覆盖率与历史深度。

决定资金效能比(CER)能否回测：
  - 腾讯分钟只有当日 -> 只能实盘用，无法回测
  - 若本地 fzline 有足够深度与覆盖率 -> CER 可回测，功能保留
  - 否则按“数据拿不到就摒弃”删除固定目标 CER，只保留可验证的日线代理
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

import numpy as np

# .lc5/.lc1 记录 32 字节: date(u16) time(u16) open/high/low/close(f32) amount(f32) vol(u32) reserved(u32)
LC_DTYPE = np.dtype([
    ("date", "<u2"), ("time", "<u2"),
    ("open", "<f4"), ("high", "<f4"), ("low", "<f4"), ("close", "<f4"),
    ("amount", "<f4"), ("vol", "<u4"), ("reserved", "<u4"),
])
PREFIX_SH = ("600", "601", "603", "605", "688", "689")
PREFIX_SZ = ("000", "001", "002", "003", "300", "301")


def decode_date(packed: int) -> str:
    """通达信分钟日期编码: year = packed//2048 + 2004, month = (packed%2048)//100, day = packed%100"""
    year = packed // 2048 + 2004
    rest = packed % 2048
    return f"{year:04d}-{rest // 100:02d}-{rest % 100:02d}"


def scan(tdx_root: Path, sub: str, ext: str, sample: int) -> dict:
    files: list[Path] = []
    for market, prefixes in (("sh", PREFIX_SH), ("sz", PREFIX_SZ)):
        d = tdx_root / market / sub
        if d.exists():
            files += [p for p in d.glob(f"*{ext}") if p.name[2:5] in prefixes]

    if not files:
        return {"available": False, "reason": f"未找到 {sub}/*{ext}", "a_share_files": 0}

    picks = random.sample(files, min(sample, len(files)))
    details, depths, bars_per_day = [], [], []
    for path in picks:
        n = path.stat().st_size // LC_DTYPE.itemsize
        if n < 10:
            continue
        with path.open("rb") as fh:
            head = np.frombuffer(fh.read(LC_DTYPE.itemsize * 2), dtype=LC_DTYPE)
            fh.seek(max(0, (n - 600)) * LC_DTYPE.itemsize)
            tail = np.frombuffer(fh.read(LC_DTYPE.itemsize * min(600, n)), dtype=LC_DTYPE)
        first_d, last_d = decode_date(int(head["date"][0])), decode_date(int(tail["date"][-1]))
        uniq_days = len(set(tail["date"].tolist()))
        per_day = len(tail) / uniq_days if uniq_days else 0
        details.append({
            "code": path.name[:8], "bars": int(n),
            "first_date": first_d, "last_date": last_d,
            "bars_per_day_tail": round(per_day, 1),
        })
        depths.append(n)
        bars_per_day.append(per_day)

    if not depths:
        return {"available": False, "reason": "文件过小", "a_share_files": len(files)}

    median_per_day = float(np.median(bars_per_day)) or 1.0
    return {
        "available": True,
        "a_share_files": len(files),
        "sampled": len(details),
        "median_bars": int(np.median(depths)),
        "median_bars_per_day": round(median_per_day, 1),
        "implied_trading_days": int(np.median(depths) / median_per_day),
        "last_dates": sorted({d["last_date"] for d in details})[-4:],
        "first_dates": sorted({d["first_date"] for d in details})[:4],
        "samples": details[:6],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-root", default=r"C:\new_tdx\vipdoc")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--output", default="output/fzline_depth_probe.json")
    args = ap.parse_args()

    random.seed(20260815)
    root = Path(args.tdx_root)
    report = {
        "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fzline_5min": scan(root, "fzline", ".lc5", args.sample),
        "minline_1min": scan(root, "minline", ".lc1", args.sample),
    }

    # A 股总数参照
    total_a = 0
    for market, prefixes in (("sh", PREFIX_SH), ("sz", PREFIX_SZ)):
        d = root / market / "lday"
        if d.exists():
            total_a += len([p for p in d.glob("*.day") if p.name[2:5] in prefixes])
    report["a_share_total_daily_files"] = total_a

    for key in ("fzline_5min", "minline_1min"):
        info = report[key]
        if not info.get("available"):
            print(f"[FAIL] {key}: {info.get('reason')}")
            continue
        cov = info["a_share_files"] / total_a * 100 if total_a else 0
        info["coverage_pct"] = round(cov, 1)
        print(f"[OK  ] {key}: A股文件 {info['a_share_files']}/{total_a} "
              f"({cov:.1f}% 覆盖), 中位 {info['median_bars']} 条 "
              f"≈ {info['implied_trading_days']} 个交易日, "
              f"每日 {info['median_bars_per_day']} 根")
        print(f"        最早 {info['first_dates']}  最新 {info['last_dates']}")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
