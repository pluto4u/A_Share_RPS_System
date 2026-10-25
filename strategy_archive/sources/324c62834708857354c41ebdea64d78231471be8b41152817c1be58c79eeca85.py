"""Local TDX daily bars -> breadth metrics -> reproducible social cards.

Run: python -m workflows.tdx_breadth --tdx-root F:/TDX
Only reads local source files; never publishes or pushes messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from project_paths import OUTPUT_ROOT

VERSION = "tdx-breadth/v1"
DAY = np.dtype([("date", "<u4"), ("open", "<u4"), ("high", "<u4"),
                ("low", "<u4"), ("close", "<u4"), ("amount", "<f4"),
                ("volume", "<u4"), ("reserved", "<u4")])
PREFIXES = {"sh": ("600", "601", "603", "605", "688", "689"),
            "sz": ("000", "001", "002", "003", "300", "301"),
            "bj": ("43", "83", "87", "88", "92")}


def discover(root: Path) -> list[Path]:
    base = root if root.name.lower() == "vipdoc" else root / "vipdoc"
    return sorted(p for market, prefixes in PREFIXES.items()
                  for p in (base / market / "lday").glob(f"{market}*.day")
                  if len(p.stem) == 8 and p.stem[2:].isdigit()
                  and p.stem[2:].startswith(prefixes))


def read_day(path: Path, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, str]:
    # Hash and parse the same bytes so every report has an exact source fingerprint.
    content = path.read_bytes()
    if not content or len(content) % DAY.itemsize:
        raise ValueError("empty or incomplete 32-byte record")
    raw = np.frombuffer(content, DAY)
    dates = pd.to_datetime(raw["date"].astype(str), format="%Y%m%d", errors="raise")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("duplicate or unsorted dates")
    frame = pd.DataFrame({"date": dates, "close": raw["close"] / 100.,
                          "volume": raw["volume"], "high": raw["high"] / 100.,
                          "low": raw["low"] / 100.})
    if as_of is not None:
        frame = frame[frame.date <= as_of]
    if frame.empty:
        raise ValueError("no records on or before as_of")
    if (frame.close <= 0).any() or (frame.high < frame.low).any():
        raise ValueError("invalid price")
    return frame.tail(400).reset_index(drop=True), hashlib.sha256(content).hexdigest()


def evaluate(frames: dict[str, pd.DataFrame], as_of: pd.Timestamp,
             sessions: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    # All comparisons use the same observed market dates; no stale bars carried forward.
    dates = sorted({d for f in frames.values() for d in f.date if d <= as_of})[-sessions:]
    rows, stocks = [], []
    features = {}
    for symbol, f in frames.items():
        f = f.copy().set_index("date")
        prev = f.close.shift(1)
        f["change"] = f.close / prev - 1
        for n in (5, 20, 60):
            f[f"ma{n}"] = f.close.rolling(n, min_periods=n).mean()
        features[symbol] = f
    for date in dates:
        counts = Counter()
        for symbol, f in features.items():
            if date not in f.index:
                continue
            r = f.loc[date]
            if r.volume <= 0:
                continue
            counts["active"] += 1
            if pd.notna(r.change):
                counts["return_eligible"] += 1
                counts["advancers"] += int(r.change > 0)
                counts["decliners"] += int(r.change < 0)
                counts["unchanged"] += int(r.change == 0)
            for n in (5, 20, 60):
                if pd.notna(r[f"ma{n}"]):
                    counts[f"eligible_ma{n}"] += 1
                    counts[f"above_ma{n}"] += int(r.close > r[f"ma{n}"])
            if date == as_of:
                stocks.append({"symbol": symbol, "date": date.date().isoformat(),
                               "close": r.close, "change_pct": r.change * 100,
                               **{f"ma{n}": r[f"ma{n}"] for n in (5, 20, 60)}})
        row = {"date": date.date().isoformat(), **{k: counts[k] for k in
               ("active", "return_eligible", "advancers", "decliners", "unchanged")}}
        for n in (5, 20, 60):
            eligible = counts[f"eligible_ma{n}"]
            row[f"eligible_ma{n}"] = eligible
            row[f"above_ma{n}"] = counts[f"above_ma{n}"]
            row[f"above_ma{n}_pct"] = counts[f"above_ma{n}"] / eligible * 100 if eligible else None
        row["advance_pct"] = counts["advancers"] / counts["return_eligible"] * 100 if counts["return_eligible"] else None
        rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(stocks)


def font(size: int):
    choices = [os.environ.get("A_SHARE_CJK_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
               "C:/Windows/Fonts/simhei.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    for choice in choices:
        if choice and Path(choice).is_file():
            return ImageFont.truetype(choice, size)
    raise RuntimeError("Chinese font unavailable; set A_SHARE_CJK_FONT")


def render_cards(out: Path, history: pd.DataFrame, summary: dict) -> None:
    bg, ink, muted, accent = "#F4F1E9", "#162A2A", "#61716D", "#167B69"
    def canvas(number, title, subtitle):
        img = Image.new("RGB", (1080, 1440), bg)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((64, 65, 400, 115), 18, fill=accent)
        d.text((85, 73), "A股宽度观察 / LOCAL DATA", font=font(23), fill="white")
        d.text((64, 160), title, font=font(66), fill=ink)
        d.text((67, 260), subtitle, font=font(29), fill=muted)
        d.line((64, 1270, 1016, 1270), fill="#C9D2C9", width=2)
        footer = [f"数据截至 {summary['as_of']} · 通达信本地日线 · 未复权",
                  f"{summary['freshness_note']} · 仅描述市场状态",
                  f"{VERSION}  /  {summary['run_id']}  /  {number:02d}"]
        for i, line in enumerate(footer):
            d.text((66, 1290 + i * 39), line, font=font(23), fill=muted)
        return img, d
    latest = history.iloc[-1]
    img, d = canvas(1, "市场有多宽？", "看上涨家数，也看多少股票站在均线之上")
    pct = latest.above_ma20_pct
    d.text((64, 355), "站上20日均线", font=font(38), fill=ink)
    d.text((64, 410), f"{pct:.1f}%" if pd.notna(pct) else "样本不足", font=font(136), fill=accent)
    for i, (label, value) in enumerate([( "有效交易样本", f"{int(latest.active):,}只"),
                                        ("上涨 / 下跌", f"{int(latest.advancers)} / {int(latest.decliners)}"),
                                        ("数据覆盖率", f"{summary['coverage_pct']:.1f}%")]):
        y = 665 + i * 155
        d.rounded_rectangle((64, y, 1016, y + 125), 22, fill="white")
        d.text((90, y + 38), label, font=font(34), fill=muted)
        d.text((570, y + 32), value, font=font(42), fill=ink)
    d.text((65, 1175), "覆盖率 = 当日有效交易样本 / 解析成功股票", font=font(27), fill=muted)
    img.save(out / "01_cover.png")
    img, d = canvas(2, "短中长，分开看", "均线宽度｜每条均线使用独立的有效样本分母")
    for i, n in enumerate((5, 20, 60)):
        y = 390 + i * 245
        p = latest[f"above_ma{n}_pct"]
        d.text((65, y), f"MA{n} · {n}日均线", font=font(42), fill=ink)
        d.text((760, y), f"{p:.1f}%" if pd.notna(p) else "N/A", font=font(46), fill=accent)
        d.rounded_rectangle((65, y + 82, 1015, y + 120), 15, fill="#D9E1D8")
        if pd.notna(p) and p > 0:
            d.rounded_rectangle((65, y + 82, 65 + 950 * p / 100, y + 120), 15, fill=accent)
        d.text((65, y + 145), f"站上 {int(latest[f'above_ma{n}'])}  /  有效样本 {int(latest[f'eligible_ma{n}'])}", font=font(29), fill=muted)
    d.text((65, 1165), "新股不足周期不计入分母；缺失、停牌不补成今日", font=font(27), fill=muted)
    img.save(out / "02_ma_breadth.png")
    img, d = canvas(3, "宽度正在怎么走？", f"最近 {len(history)} 个本地观测日 · MA20宽度")
    x0, y0, x1, y1 = 130, 420, 990, 1030
    for p in (0, 25, 50, 75, 100):
        y = y1 - (y1 - y0) * p / 100
        d.line((x0, y, x1, y), fill="#D4DDD3", width=2)
        d.text((60, y - 18), str(p), font=font(25), fill=muted)
    points = [(x0 + i * (x1 - x0) / max(len(history) - 1, 1),
               y1 - (y1 - y0) * float(p) / 100)
              if pd.notna(p) else None for i, p in enumerate(history.above_ma20_pct)]
    for a, b in zip(points, points[1:]):
        if a is not None and b is not None:
            d.line((*a, *b), fill=accent, width=7)
    d.text((x0, 1070), str(history.iloc[0].date), font=font(26), fill=muted)
    d.text((780, 1070), str(history.iloc[-1].date), font=font(26), fill=muted)
    d.text((65, 1175), "未复权价格受除权影响；历史样本随上市、停牌变化", font=font(26), fill=muted)
    img.save(out / "03_breadth_trend.png")


def run(root: Path, as_of: str | None = None, output_root: Path | None = None) -> Path:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    cutoff = pd.Timestamp(as_of or now.date().isoformat())
    if cutoff > pd.Timestamp(now.date()):
        raise ValueError("as_of cannot be in the future")
    paths = discover(root)
    if not paths:
        raise ValueError(f"No A-share .day files under {root}")
    frames, provenance, failures = {}, [], []
    for path in paths:
        try:
            frame, digest = read_day(path, cutoff)
            frames[path.stem] = frame
            provenance.append({"symbol": path.stem, "path": str(path.resolve()),
                               "sha256": digest, "latest": frame.date.max().date().isoformat()})
        except (OSError, ValueError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
    if not frames:
        raise ValueError("All local files failed validation")
    # Dominant latest date avoids a single stray/future record setting the report date.
    latest_counts = Counter(f.date.max() for f in frames.values())
    target = cutoff if as_of else max(latest_counts, key=lambda d: (latest_counts[d], d))
    history, stocks = evaluate(frames, target)
    if history.empty or history.iloc[-1].date != target.date().isoformat() or history.iloc[-1].active == 0:
        raise ValueError("No trading samples on requested date; choose an observed trading date")
    if history.iloc[-1].eligible_ma20 == 0:
        raise ValueError("Insufficient MA20 history")
    run_id = now.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    out = (output_root or OUTPUT_ROOT / "workflows" / "tdx_breadth") / target.strftime("%Y-%m-%d") / run_id
    out.mkdir(parents=True, exist_ok=False)
    age = (now.date() - target.date()).days
    summary = {"schema_version": VERSION, "run_id": run_id, "generated_at": now.isoformat(),
               "as_of": target.date().isoformat(), "source_root": str(root.resolve()),
               "discovered_files": len(paths), "parsed_files": len(frames),
               "failed_files": len(failures), "coverage_pct": len(stocks) / len(frames) * 100,
               "freshness_note": f"本地历史快照，距生成日{age}天" if age > 3 else "本地日线快照（非实时）",
               "adjustment": "unadjusted", "latest": history.iloc[-1].to_dict(),
               "methodology": "Active positive-volume A-share files, no ST exclusion; close > full-window SMA, missing/stale bars excluded; no survivorship correction; not an official all-market universe."}
    history.to_csv(out / "breadth_history.csv", index=False, encoding="utf-8-sig")
    stocks.to_csv(out / "constituents.csv", index=False, encoding="utf-8-sig")
    for name, data in (("summary.json", summary), ("source_manifest.json", provenance), ("failures.json", failures)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    render_cards(out, history, summary)
    caption = (f"{summary['as_of']} A股市场宽度观察\n站上20日均线：{history.iloc[-1].above_ma20_pct:.1f}%\n"
               f"上涨{int(history.iloc[-1].advancers)}家，下跌{int(history.iloc[-1].decliners)}家。\n"
               f"数据覆盖率{summary['coverage_pct']:.1f}%；{summary['freshness_note']}。\n"
               "来源：本地通达信未复权日线；含ST，剔除缺失与当日无成交样本。\n"
               "这是市场状态记录，均线宽度不等于收益预测。\n#市场宽度 #A股复盘 #数据可视化\n")
    (out / "xiaohongshu_caption.txt").write_text(caption, encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({"schema_version": VERSION,
        "run_id": run_id, "artifacts": [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                          for p in sorted(out.iterdir()) if p.is_file()]}, indent=2), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-root", type=Path, default=Path(os.environ.get("TDX_ROOT", "F:/TDX")))
    parser.add_argument("--as-of", help="YYYY-MM-DD, default dominant latest local date")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    print(run(args.tdx_root, args.as_of, args.output_root))


if __name__ == "__main__":
    main()
