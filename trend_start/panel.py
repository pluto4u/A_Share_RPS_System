"""Forward-adjusted wide panel (stocks x sessions) built from local TDX files.

Ratio forward adjustment preserves every bar-to-bar return, so using the
latest factor set does not leak future information into return-based signals.
Stocks are NOT filtered by current names here: dropping names that are ST
today would remove later losers from history (look-ahead survivorship bias).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FIELDS = ("open", "high", "low", "close", "vol", "amount")
PREFIXES = {
    "sh": ("600", "601", "603", "605", "688", "689"),
    "sz": ("000", "001", "002", "003", "300", "301"),
}


@dataclass
class Panel:
    dates: np.ndarray  # datetime64[D], ascending sessions of the SSE index
    codes: np.ndarray  # str, e.g. "600519.SH"
    data: dict[str, np.ndarray]  # field -> float32 (n_codes, n_dates), NaN = no bar
    index_close: np.ndarray  # CSI300 close aligned to dates

    def field(self, name: str) -> np.ndarray:
        return self.data[name]


def _calendar(tdx_root: Path, read_day: Any) -> pd.DatetimeIndex:
    idx = read_day(tdx_root / "vipdoc/sh/lday/sh000001.day")
    return pd.DatetimeIndex(idx.trade_date)


def build_panel(tdx_root: Path, as_of: pd.Timestamp, cache: Path) -> tuple[Panel, dict[str, Any]]:
    """Strict read, fail-closed adjustment per security, all failures logged."""
    from pytdx.reader import GbbqReader

    from run_daily_rps import read_day
    from run_local_tdx_compare import adjust

    cal = _calendar(tdx_root, read_day)
    cal = cal[cal <= as_of]
    if cal[-1] != as_of:
        raise ValueError(f"index calendar has no session {as_of.date()}")
    pos = pd.Series(np.arange(len(cal)), index=cal)
    events = GbbqReader().get_df(str(tdx_root / "T0002/hq_cache/gbbq"))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d", errors="raise")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]

    paths: list[tuple[str, Path]] = []
    for market, prefixes in PREFIXES.items():
        for p in sorted((tdx_root / "vipdoc" / market / "lday").glob("*.day")):
            code = p.stem[2:]
            if p.stem[:2] == market and code.startswith(prefixes) and len(code) == 6:
                paths.append((f"{code}.{market.upper()}", p))

    n, m = len(paths), len(cal)
    arrays = {f: np.full((n, m), np.nan, dtype=np.float32) for f in FIELDS}
    kept: list[str] = []
    excluded: list[dict[str, str]] = []
    row = 0
    for i, (code, path) in enumerate(paths):
        if i % 500 == 0:
            print(f"[PANEL] {i}/{n}", flush=True)
        try:
            d = read_day(path)
            d = d.loc[(d.trade_date <= as_of) & (d.vol > 0)].reset_index(drop=True)
            if d.empty:
                raise ValueError("no traded bars up to as_of")
            d, _ = adjust(d, grouped.get(code, empty), as_of, bars=None)
            missing = ~d.trade_date.isin(pos.index)
            if missing.any():
                raise ValueError(f"{int(missing.sum())} bars not on SSE calendar")
        except Exception as exc:  # noqa: BLE001 - recorded, security excluded
            excluded.append({"code": code, "reason": str(exc)})
            continue
        cols = pos.loc[d.trade_date].to_numpy()
        for f in FIELDS:
            arrays[f][row, cols] = d[f].to_numpy(dtype=np.float32)
        kept.append(code)
        row += 1
    for f in FIELDS:
        arrays[f] = arrays[f][:row]
    hs300 = read_day(tdx_root / "vipdoc/sh/lday/sh000300.day")
    hs300 = hs300.set_index("trade_date").close.reindex(cal)
    panel = Panel(
        dates=cal.to_numpy().astype("datetime64[D]"),
        codes=np.array(kept),
        data=arrays,
        index_close=hs300.to_numpy(dtype=np.float64),
    )
    audit = {
        "as_of": str(as_of.date()),
        "tdx_root": str(tdx_root),
        "sessions": m,
        "first_session": str(cal[0].date()),
        "files_seen": n,
        "kept": len(kept),
        "excluded": len(excluded),
        "excluded_detail": excluded,
    }
    cache.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache / "panel.npz",
        dates=panel.dates,
        codes=panel.codes,
        index_close=panel.index_close,
        **arrays,
    )
    (cache / "panel_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return panel, audit


def load_panel(cache: Path) -> Panel:
    z = np.load(cache / "panel.npz", allow_pickle=False)
    return Panel(
        dates=z["dates"],
        codes=z["codes"],
        data={f: z[f] for f in FIELDS},
        index_close=z["index_close"],
    )
