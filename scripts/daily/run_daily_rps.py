"""Local daily RPS universe -> percentile ranks -> right-side research screen.

No EXTDATA, network, minute data or orders. See DAILY_RPS.md for definitions.
"""

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
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

# Editable strategy parameters: shared universe first, strategy filters second.
RPS250_MIN = 87.0
RPS120_MIN = 87.0
HIGH_DISTANCE_MAX = 25.0
LOW_GAIN_MIN = 30.0
DRAWDOWN20_MAX = 15.0
YEAR_BARS = 250
MIN_BARS = 251
LONG_SUSPENSION_SESSIONS = 20
ROOT = PROJECT_ROOT
PREFIXES = {
    "sh": ("600", "601", "603", "605", "688", "689"),
    "sz": ("000", "001", "002", "003", "300", "301"),
}
DTYPE = np.dtype(
    [
        (k, "<f4" if k == "amount" else "<u4")
        for k in ("date", "open", "high", "low", "close", "amount", "vol", "reserved")
    ]
)
OUTPUT_COLUMNS = [
    "代码",
    "名称",
    "现价",
    "RPS50",
    "RPS120",
    "RPS250",
    "距52周高点%",
    "距52周低点%",
    "20日最大回撤%",
    "最近一次跌破MA20距今天数",
    "口袋低点",
    "MA10",
    "MA20",
    "MA60",
    "MA120",
    "52周最高收盘",
    "52周最低收盘",
    "回调段日均量",
    "上涨段日均量",
    "数据日期",
    "行业",
    "价格口径",
    "RPS分母",
    "入选",
    "未通过条件",
]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    rps250_min: float = Field(default=RPS250_MIN, ge=0, le=100)
    rps120_min: float = Field(default=RPS120_MIN, ge=0, le=100)
    high_distance_max: float = Field(default=HIGH_DISTANCE_MAX, ge=0, le=100)
    low_gain_min: float = Field(default=LOW_GAIN_MIN, ge=0)
    drawdown20_max: float = Field(default=DRAWDOWN20_MAX, ge=0, le=100)
    year_bars: int = Field(default=YEAR_BARS, ge=120)
    min_bars: int = Field(default=MIN_BARS, ge=251)
    long_suspension_sessions: int = Field(default=LONG_SUSPENSION_SESSIONS, ge=1)


def discover(scan_root: Path) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    errors: list[str] = []
    for parent, dirs, _ in os.walk(scan_root, onerror=lambda e: errors.append(str(e))):
        if Path(parent).name.lower() == "vipdoc":
            if all((Path(parent) / market / "lday").is_dir() for market in PREFIXES):
                found.append(Path(parent).parent)
            dirs[:] = []
    return sorted(set(found)), errors


def read_day(path: Path) -> Any:
    """Strict 32-byte little-endian parser; no truncation, coercion or silent skip."""
    content = path.read_bytes()
    if not content or len(content) % 32:
        raise ValueError(f"invalid byte length {len(content)} (expected a positive multiple of 32)")
    raw = np.frombuffer(content, dtype=DTYPE)
    dates = pd.to_datetime(raw["date"].astype(str), format="%Y%m%d", errors="raise")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("duplicate/unordered daily dates")
    data = pd.DataFrame({"trade_date": dates})
    for field in ("open", "high", "low", "close"):
        data[field] = raw[field].astype(float) / 100
    data["amount"] = raw["amount"].astype(float)
    data["vol"] = raw["vol"].astype(float)
    p = data[["open", "high", "low", "close"]]
    if not np.isfinite(data.drop(columns="trade_date").to_numpy()).all():
        raise ValueError("nonfinite daily values")
    if (
        (p <= 0).any().any()
        or (data.high < p.max(axis=1)).any()
        or (data.low > p.min(axis=1)).any()
    ):
        raise ValueError("invalid daily OHLC")
    if (data.amount < 0).any() or ((data.vol == 0) != (data.amount == 0)).any():
        raise ValueError("invalid amount/volume pair")
    return data


def names_from_tnf(cache: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for market in PREFIXES:
        path = cache / f"{market}s.tnf"
        raw = path.read_bytes()
        if len(raw) <= 50 or (len(raw) - 50) % 360:
            raise ValueError(f"unsupported TNF layout: {path}")
        for offset in range(50, len(raw), 360):
            b = raw[offset : offset + 360]
            code = b[:6].decode("ascii")
            if not code.isdigit() or not code.startswith(PREFIXES[market]):
                continue
            name = b[31:63].split(b"\0")[0].decode("gbk").strip()
            if not name:
                raise ValueError(f"missing name in {path}: {code}")
            key = code + "." + market.upper()
            if key in names and names[key] != name:
                raise ValueError(f"conflicting names for {key}")
            names[key] = name
    return names


def daily_features(data: Any, cfg: Settings) -> dict[str, Any]:
    """250 trading-bar year; close-to-close max drawdown; down-day volume proxy."""
    if len(data) < max(cfg.min_bars, cfg.year_bars):
        raise ValueError("insufficient valid trading bars")
    d = data.reset_index(drop=True)
    c = d.close
    ma = {n: c.rolling(n).mean() for n in (10, 20, 60, 120)}
    w = c.tail(cfg.year_bars)
    recent = c.tail(20)
    dd = float((1 - recent / recent.cummax()).max() * 100)
    # Segments operationalized as all down/up close-to-close days within the last 20.
    delta = c.diff()
    down = (delta < 0).tail(20)
    up = (delta > 0).tail(20)
    volume = d.vol.tail(20)
    down_mean = float(volume[down].mean()) if down.any() else None
    up_mean = float(volume[up].mean()) if up.any() else None
    pocket = float(d.low.tail(20)[down].min()) if down.any() else None
    cross = (c < ma[20]) & (c.shift(1) >= ma[20].shift(1))
    positions = np.flatnonzero(cross.to_numpy())
    age = len(c) - 1 - int(positions[-1]) if len(positions) else None
    shrink = down_mean is not None and up_mean is not None and down_mean < up_mean
    result = {
        "现价": float(c.iloc[-1]),
        "距52周高点%": float((1 - c.iloc[-1] / w.max()) * 100),
        "距52周低点%": float((c.iloc[-1] / w.min() - 1) * 100),
        "20日最大回撤%": dd,
        "最近一次跌破MA20距今天数": age,
        "口袋低点": pocket,
        "52周最高收盘": float(w.max()),
        "52周最低收盘": float(w.min()),
        "回调段日均量": down_mean,
        "上涨段日均量": up_mean,
        "均线多头": bool(c.iloc[-1] > ma[20].iloc[-1] > ma[60].iloc[-1] > ma[120].iloc[-1]),
        "MA60向上": bool(ma[60].iloc[-1] > ma[60].iloc[-6]),
        "缩量回调": shrink,
        "缩量数据齐全": down_mean is not None and up_mean is not None,
        "跌破MA20查找历史根数": len(d),
    }
    result.update({f"MA{n}": float(series.iloc[-1]) for n, series in ma.items()})
    result.update({f"return{n}": float(c.iloc[-1] / c.iloc[-n - 1] - 1) for n in (50, 120, 250)})
    return result


def rank_then_filter(universe: Any, cfg: Settings) -> Any:
    """NEVER filter by trend, liquidity or strength before this ranking step."""
    d = universe.copy()
    if d.empty or d["代码"].duplicated().any():
        raise ValueError("empty or duplicate universe")
    n = len(d)
    for period in (50, 120, 250):
        values = d[f"return{period}"]
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError("RPS denominator must be the same complete universe for all periods")
        d[f"RPS{period}"] = values.rank(method="average", ascending=True) / n * 100
    gates = {
        "均线多头": d["均线多头"],
        "MA60向上": d["MA60向上"],
        "距52周高点": d["距52周高点%"] <= cfg.high_distance_max,
        "距52周低点": d["距52周低点%"] >= cfg.low_gain_min,
        "20日回撤": d["20日最大回撤%"] <= cfg.drawdown20_max,
        "缩量回调": d["缩量回调"],
        "RPS120": d.RPS120 >= cfg.rps120_min,
        "RPS250": d.RPS250 >= cfg.rps250_min,
    }
    checks = pd.DataFrame(gates, index=d.index)
    d["入选"] = checks.all(axis=1)
    d["未通过条件"] = checks.apply(lambda row: ",".join(str(k) for k in row.index[~row]), axis=1)
    d["RPS分母"] = n
    return d.sort_values(["RPS250", "RPS120", "代码"], ascending=[False, False, True])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    parser.add_argument("--as-of", help="YYYY-MM-DD; default latest local index session")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--scan-root", type=Path, default=Path("D:/"))
    parser.add_argument(
        "--price-basis",
        choices=("raw", "qfq"),
        default="raw",
        help="raw: exact .day prices (default); qfq: strict local gbbq forward adjustment",
    )
    parser.add_argument("--rps120-min", type=float, default=RPS120_MIN)
    parser.add_argument("--rps250-min", type=float, default=RPS250_MIN)
    args = parser.parse_args()
    if args.discover_only:
        roots, errors = discover(args.scan_root)
        print(
            json.dumps(
                {"installations": [str(p) for p in roots], "scan_errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if errors or not roots else 0
    cfg = Settings(rps120_min=args.rps120_min, rps250_min=args.rps250_min)
    root = args.tdx_root.resolve()
    if root.name.lower() == "vipdoc":
        root = root.parent
    cache, vip = root / "T0002/hq_cache", root / "vipdoc"
    for market in PREFIXES:
        if not (vip / market / "lday").is_dir():
            raise FileNotFoundError(f"missing {market} daily directory under {vip}")
    names = names_from_tnf(cache)
    index = read_day(vip / "sh/lday/sh000001.day")
    target = pd.Timestamp(args.as_of) if args.as_of else index.trade_date.iloc[-1]
    if target not in set(index.trade_date) or target > pd.Timestamp.now().normalize():
        raise ValueError("as-of must be an observed local index session, not in the future")
    sessions = index.loc[index.trade_date <= target, "trade_date"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output or ROOT / "output" / f"daily_rps_{target:%Y%m%d}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ("audit.json", "selected.csv", "all_valid.csv")):
        raise FileExistsError("output already contains a run; use a new output directory")
    print(f"[START] {root}; as_of={target:%Y-%m-%d}; price_basis={args.price_basis}", flush=True)
    source_files = [cache / "shs.tnf", cache / "szs.tnf"]
    if args.price_basis == "qfq":
        from pytdx.reader import GbbqReader
        from run_local_tdx_compare import adjust

        events = GbbqReader().get_df(str(cache / "gbbq"))
        events["date"] = pd.to_datetime(
            events.datetime.astype(str), format="%Y%m%d", errors="raise"
        )
        events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
        grouped = dict(tuple(events.groupby("ts_code")))
        empty = events.iloc[:0]
        source_files.append(cache / "gbbq")
    else:
        print(
            "[WARNING] RAW prices: corporate actions can distort returns, MA and drawdown.",
            file=sys.stderr,
            flush=True,
        )
    files = sorted(p for market in PREFIXES for p in (vip / market / "lday").glob("*.day"))
    rows: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    for i, path in enumerate(files):
        if i % 500 == 0:
            print(f"[SCAN] {i}/{len(files)} files; valid={len(rows)}", flush=True)
        market, code = path.stem[:2].lower(), path.stem[2:]
        entry: dict[str, Any] = {"path": str(path), "代码": code + "." + market.upper()}
        if len(code) != 6 or not code.isdigit() or not code.startswith(PREFIXES.get(market, ())):
            log.append({**entry, "status": "EXCLUDED", "reason": "非沪深A股代码"})
            continue
        try:
            raw = read_day(path)
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            entry["file_latest"] = str(raw.trade_date.iloc[-1].date())
            entry["名称"] = names.get(entry["代码"])
            clipped = raw.loc[(raw.trade_date <= target) & (raw.vol > 0)]
            if clipped.empty:
                raise ValueError("no traded bars on/before target")
            stale = int((sessions > clipped.trade_date.iloc[-1]).sum())
            entry["stale_sessions"] = stale
            name = str(entry["名称"]).replace(" ", "").upper()
            reason = (
                "ST/退市风险名称"
                if "ST" in name or "退" in name
                else "长期无交易记录/停牌或未更新"
                if stale >= cfg.long_suspension_sessions
                else "缺目标日日线/停牌或未更新"
                if stale > 0
                else "有效交易历史不足251根"
                if len(clipped) < max(cfg.min_bars, cfg.year_bars)
                else ""
            )
            if reason:
                log.append({**entry, "status": "EXCLUDED", "reason": reason})
                continue
            if not entry["名称"]:
                raise ValueError("missing local security name; cannot verify ST status")
            if args.price_basis == "qfq":
                adjusted, count = adjust(
                    clipped, grouped.get(entry["代码"], empty), target, bars=None
                )
            else:
                adjusted, count = clipped.copy(), 0
            row = daily_features(adjusted, cfg)
            rows.append(
                {
                    "代码": entry["代码"],
                    "名称": entry["名称"],
                    "数据日期": str(target.date()),
                    "行业": None,
                    "价格口径": args.price_basis,
                    **row,
                }
            )
            log.append(
                {
                    **entry,
                    "status": "VALID",
                    "reason": "",
                    "adjustment_events": count,
                    "valid_bars": len(adjusted),
                }
            )
        except (OSError, ValueError, KeyError, UnicodeError) as exc:
            log.append({**entry, "status": "ERROR", "reason": str(exc)})
            print(f"[ERROR] {entry['代码']}: {exc}", file=sys.stderr, flush=True)
    inventory = pd.DataFrame(log)
    inventory.to_csv(out / "inventory.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    errors = inventory.loc[inventory.status == "ERROR"]
    errors.to_csv(out / "errors.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    audit: dict[str, Any] = {
        "version": "daily_rps_v1",
        "as_of": str(target.date()),
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "parameters": cfg.model_dump(),
        "day_files": len(files),
        "valid_universe": len(rows),
        "errors": len(errors),
        "exclusions": inventory.loc[inventory.status == "EXCLUDED", "reason"]
        .value_counts()
        .to_dict(),
        "universe_scope": (
            "local SH/SZ A-share universe, non-ST, target bar present, >=251 traded bars"
        ),
        "rank_method": "average rank / SAME complete valid universe N * 100, BEFORE trend filters",
        "price_basis": args.price_basis,
        "price_warning": "Raw prices are not corporate-action-adjusted; returns may be distorted"
        if args.price_basis == "raw"
        else "Local gbbq forward-adjusted, not independently reconciled",
        "current_name_cache_not_point_in_time": True,
        "industry": "missing; no guessed mapping",
        "absence_of_day_file_cannot_be_detected_without_external_master": True,
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        "status": "BLOCKED" if len(errors) else "OK",
    }
    local_codes = set(inventory["代码"])
    audit["name_cache_without_day_file"] = [
        {"code": code, "name": names[code]} for code in sorted(set(names) - local_codes)
    ]
    for missing in audit["name_cache_without_day_file"]:
        print(
            f"[COVERAGE WARNING] name cache has no .day file: {missing}; "
            "listing age unknown; exchange-wide coverage is unverified",
            file=sys.stderr,
        )
    if not rows or len(errors):
        audit["status"] = "BLOCKED"
        (out / "audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"[BLOCKED] {len(errors)} errors; see {out}; no ranking/selection published", flush=True
        )
        return 2
    ranked = rank_then_filter(pd.DataFrame(rows), cfg)
    selected = ranked.loc[ranked["入选"]]
    ranked.to_csv(out / "all_valid.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    selected[OUTPUT_COLUMNS].to_csv(
        out / "selected.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    audit["selected"] = len(selected)
    audit["missing_pullback_comparison"] = int((~ranked["缩量数据齐全"]).sum())
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": audit["status"],
                "as_of": audit["as_of"],
                "denominator": len(ranked),
                "selected": len(selected),
                "output": str(out),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"[FATAL] {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
