"""Run dated confirmation screening from local TDX EXTDATA and daily files."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import hashlib
import html
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pytdx.reader import GbbqReader
from rps_analyzer.right_confirmation import ConfirmationConfig, evaluate
from rps_analyzer.right_confirmation_v2 import LayeredConfig
from rps_analyzer.right_confirmation_v2 import evaluate as evaluate_v2
from rps_analyzer.tdx_reader import read_day_file
from rps_analyzer.watchlist_view import WATCH_STAGES, render_watchlist, watchlist
from run_local_tdx_compare import adjust, load_name_master

ROOT = PROJECT_ROOT


def extdata(path: Path, channel: int, date: int) -> dict[str, float]:
    """Validate observed 29-byte index and 12-byte dated value records.

    Exact date only: never backfill a missing RPS. Sizes, dates, duplicate codes,
    date ordering, and RPS range are checked before results can be used.
    """
    idx = (path / f"extdata_{channel}.idx").read_bytes()
    data = path / f"extdata_{channel}.dat"
    if len(idx) % 29 or data.stat().st_size % 12:
        raise ValueError("unsupported EXTDATA record size")
    result: dict[str, float] = {}
    seen: set[str] = set()
    total = 0
    with data.open("rb") as stream:
        for pos in range(0, len(idx), 29):
            market = struct.unpack_from("<H", idx, pos)[0]
            code = idx[pos + 2 : pos + 9].split(b"\0")[0].decode("ascii")
            count = struct.unpack_from("<I", idx, pos + 25)[0]
            block = stream.read(count * 12)
            if len(block) != count * 12:
                raise ValueError("truncated EXTDATA")
            total += count * 12
            if market not in (0, 1):
                continue
            ts_code = code + (".SZ" if market == 0 else ".SH")
            if ts_code in seen:
                raise ValueError("duplicate EXTDATA security")
            seen.add(ts_code)
            values = np.frombuffer(
                block, dtype=[("date", "<u4"), ("reserved", "<u4"), ("value", "<f4")]
            )
            if (np.diff(values["date"].astype(np.int64)) <= 0).any():
                raise ValueError("unordered or duplicate EXTDATA dates")
            matched = values[values["date"] == date]
            if len(matched):
                rps = float(matched["value"][0]) / 10
                if not 0 <= rps <= 100:
                    raise ValueError("invalid EXTDATA RPS range")
                result[ts_code] = rps
    if total != data.stat().st_size:
        raise ValueError("EXTDATA index/data length mismatch")
    return result


def local_float(path: Path, date: int) -> dict[str, dict[str, Any]]:
    """Read DBF LTAG (10,000 shares); retain its recorded update date.

    This is a dated current cache, not a point-in-time archived share master.
    """
    b = path.read_bytes()
    header, record = struct.unpack_from("<HH", b, 8)
    n = struct.unpack_from("<I", b, 4)[0]
    fields, offset = [], 1
    for pos in range(32, header - 1, 32):
        name = b[pos : pos + 11].split(b"\0")[0].decode("ascii")
        width = b[pos + 16]
        fields.append((name, offset, width))
        offset += width
    if offset != record or header + n * record > len(b):
        raise ValueError("invalid DBF layout")
    out = {}
    for i in range(n):
        row = b[header + i * record : header + (i + 1) * record]
        if row[:1] == b"*":
            continue
        f = {
            key: row[start : start + width].decode("gbk").strip()
            for key, start, width in fields
            if key in ("SC", "GPDM", "GXRQ", "LTAG")
        }
        if f.get("SC") not in ("0", "1") or not f.get("LTAG") or not f.get("GXRQ"):
            continue
        updated = int(f["GXRQ"])
        shares = float(f["LTAG"]) * 10000
        if updated <= date and updated > 19000101 and shares > 0:
            code = f["GPDM"] + (".SZ" if f["SC"] == "0" else ".SH")
            out[code] = {"shares": shares, "record_date": updated}
    return out


def chart(d: Any, name: str) -> str:
    d = d.tail(80).reset_index(drop=True)
    low, high = float(d.low.min()), float(d.high.max())
    step = 760 / len(d)

    def y(value: float) -> float:
        return 210 - (value - low) / max(high - low, 0.01) * 190

    bits = [f'<h3>{html.escape(name)} · 最近80根前复权日线</h3><svg viewBox="0 0 820 300">']
    for i, r in d.iterrows():
        x = 35 + i * step
        color = "#d94a52" if r.close >= r.open else "#169d94"
        bits.append(f'<path d="M{x:.1f},{y(r.high):.1f}V{y(r.low):.1f}" stroke="{color}"/>')
        bits.append(
            f'<rect x="{x - step * 0.3:.1f}" y="{min(y(r.open), y(r.close)):.1f}" '
            f'width="{step * 0.6:.1f}" height="{max(abs(y(r.open) - y(r.close)), 1):.1f}" '
            f'fill="{color}"/>'
        )
        vh = r.vol / max(d.vol.max(), 1) * 45
        bits.append(
            f'<rect x="{x - step * 0.3:.1f}" y="{275 - vh:.1f}" width="{step * 0.6:.1f}" '
            f'height="{vh:.1f}" fill="{color}"/>'
        )
    for n, color in ((10, "#d7a221"), (20, "#9758d3"), (60, "#3b7dda")):
        # Chart receives full-length averages computed before slicing.
        points = " ".join(
            f"{35 + i * step:.1f},{y(value):.1f}"
            for i, value in enumerate(d[f"ma{n}"])
            if pd.notna(value)
        )
        bits.append(f'<polyline points="{points}" fill="none" stroke="{color}"/>')
    bits.append(
        f'<text x="35" y="298">{d.trade_date.iloc[0]:%Y-%m-%d} → '
        f"{d.trade_date.iloc[-1]:%Y-%m-%d} · MA10金 / MA20紫 / MA60蓝；下方成交量</text></svg>"
    )
    return "".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--date", default="2026-09-24")
    ap.add_argument("--version", choices=("v1", "v2"), default="v1")
    args = ap.parse_args()
    target = pd.Timestamp(args.date)
    date = int(target.strftime("%Y%m%d"))
    cfg = ConfirmationConfig()
    cfg2 = LayeredConfig()
    out = ROOT / "output" / f"right_confirmation_{'v2_' if args.version == 'v2' else ''}{date}"
    out.mkdir(parents=True, exist_ok=True)
    ext = args.tdx_root / "T0002/extdata"
    r120, r250 = extdata(ext, 6, date), extdata(ext, 7, date)
    codes = sorted(c for c in r120.keys() & r250.keys() if r120[c] + r250[c] > cfg.rps_sum)
    # Include the supplied reference stock even when it fails the strength filter.
    codes = sorted(set(codes) | {"688536.SH"})
    print(f"Exact-date RPS: {len(r120)}/{len(r250)}; strength pool: {len(codes)}", flush=True)
    hq = args.tdx_root / "T0002/hq_cache"
    names = load_name_master(hq)
    floats = local_float(hq / "base.dbf", date)
    events = GbbqReader().get_df(str(hq / "gbbq"))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]
    rows, excluded = [], []
    charts: dict[str, str] = {}
    for i, code in enumerate(codes):
        if i % 50 == 0:
            print(f"Evaluating {i}/{len(codes)}", flush=True)
        name = names.get(code, "")
        try:
            if not name or "ST" in name.upper().replace(" ", "") or "退" in name:
                raise ValueError("missing name or current risk-warning name")
            if code not in r120 or code not in r250:
                raise ValueError("missing exact-date external RPS")
            symbol, market = code.split(".")
            path = (
                args.tdx_root
                / "vipdoc"
                / market.lower()
                / "lday"
                / (market.lower() + symbol + ".day")
            )
            raw = read_day_file(str(path))
            if raw is None or not (raw.trade_date == target).any():
                raise ValueError("missing target-date K line")
            ev = grouped.get(code, empty)
            # Full history is necessary to reproduce literal COUNT(X,0).
            d, _ = adjust(raw, ev, target, bars=None)
            floating = floats.get(code)
            turnover = None
            if floating:
                later_changes = ev[
                    (ev.datetime > floating["record_date"])
                    & (ev.date <= target)
                    & ev.category.between(2, 10)
                ]
                if later_changes.empty:
                    turnover = float(d.vol.iloc[-1]) / floating["shares"] * 100
            result = (
                evaluate_v2(d, r120[code], r250[code], turnover, cfg2)
                if args.version == "v2"
                else evaluate(d, r120[code], r250[code], turnover, cfg)
            )
            rows.append(
                {
                    "ts_code": code,
                    "name": name,
                    "as_of": args.date,
                    **result,
                    "day_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "float_record_date": floating["record_date"] if floating else None,
                    "float_source": "base.dbf LTAG; current cache, not historical archive",
                }
            )
            if result["stage"] in WATCH_STAGES:
                for n in (10, 20, 60):
                    d[f"ma{n}"] = d.close.rolling(n).mean()
                charts[code] = chart(d, name)
        except (ValueError, FileNotFoundError) as exc:
            excluded.append({"ts_code": code, "reason": str(exc)})
    if not rows:
        raise RuntimeError("no evaluable securities")
    table = pd.DataFrame(rows).sort_values(["confirmed", "rps120", "rps250"], ascending=False)
    table.to_csv(out / "all_evaluated.csv", index=False, encoding="utf-8-sig")
    table[table.confirmed].to_csv(out / "confirmed.csv", index=False, encoding="utf-8-sig")
    table[table.baseline.eq(True)].to_csv(out / "baseline.csv", index=False, encoding="utf-8-sig")
    watch_columns = [
        "ts_code",
        "name",
        "as_of",
        "stage",
        "close",
        "ma20_reference",
        "prior_high20",
        "prior_low10",
        "rps120",
        "rps250",
        "volume_ratio",
        "bias20_pct",
    ]
    watchlist(table)[watch_columns].to_csv(out / "watchlist.csv", index=False, encoding="utf-8-sig")
    audit = {
        "as_of": args.date,
        "version": cfg2.version if args.version == "v2" else cfg.version,
        "parameters": cfg2.model_dump() if args.version == "v2" else cfg.model_dump(),
        "external_rps_coverage": {"channel6": len(r120), "channel7": len(r250)},
        "evaluated": len(table),
        "baseline": int(table.baseline.eq(True).sum()),
        "corrected_base": int(table.corrected_base.eq(True).sum()),
        "confirmed": int(table.confirmed.sum()),
        "watchlist": len(watchlist(table)),
        "stages": table.stage.value_counts().to_dict(),
        "excluded": excluded,
        "independent_adjusted_price_verified": False,
        "historical_share_master_verified": False,
        "forward_performance_validated": False,
        "gbbq_sha256": hashlib.sha256((hq / "gbbq").read_bytes()).hexdigest(),
        "base_dbf_sha256": hashlib.sha256((hq / "base.dbf").read_bytes()).hexdigest(),
        "extdata_sha256": {
            f"{channel}.{suffix}": hashlib.sha256(
                (ext / f"extdata_{channel}.{suffix}").read_bytes()
            ).hexdigest()
            for channel in (6, 7)
            for suffix in ("idx", "dat")
        },
    }
    if args.version == "v2":
        audit["v2_gate_pass"] = {
            key: int(table[key].eq(True).sum())
            for key in table.columns
            if key.startswith("v2_") and key not in ("v2_failed", "v2_missing")
        }
        audit["v1_stages_same_inputs"] = table.v1_stage.value_counts().to_dict()
        table[table.setup].to_csv(out / "setup.csv", index=False, encoding="utf-8-sig")
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = render_watchlist(table, args.date, charts)
    (out / "report.html").write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                k: audit[k]
                for k in ("evaluated", "baseline", "corrected_base", "confirmed", "stages")
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    print(out / "report.html", flush=True)


if __name__ == "__main__":
    main()
