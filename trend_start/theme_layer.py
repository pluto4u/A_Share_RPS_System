"""Short-term theme layer for the daily board: THS concept-index strength + limit-up reasons. Display only.

Data (THS via hithink-finance CLI), cached under data/theme_cache/:
  concepts.json            concept catalog (tag cn_concept), refreshed weekly
  concept_close.csv        wide table: session x concept close; history on first use, then
                           snapshot appends verified by prev_price == cached previous close
  limit_up/YYYYMMDD.json   full limit-up pool of a session (reasons, streak, seal time); THS keeps
                           history from about 2024-02
Nothing here feeds an engine, a gate or an order.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

CACHE_DAYS = 60          # calendar days fetched on a history refill (covers 20 sessions)
SNAPSHOT_BATCH = 80
PREV_TOL = 1e-4          # snapshot prev_price must equal the cached previous close


def _ms(day: pd.Timestamp) -> int:
    return int(pd.Timestamp(day.date()).tz_localize("Asia/Shanghai").timestamp() * 1000)


def ths(args: list[str], out: Path) -> object:
    """One hithink-finance call written to `out`; returns the data payload or raises."""
    p = subprocess.run(["hithink-finance.cmd", *args, "--output", str(out), "--format", "json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    head = json.loads((p.stdout or p.stderr or "{}").lstrip("﻿"))
    if not head.get("ok"):
        raise RuntimeError(str(head.get("error"))[:300])
    return json.loads(out.read_text(encoding="utf-8-sig"))["data"]


def _items(data: object) -> list[dict]:
    if isinstance(data, dict):
        return data.get("item") or next((v for v in data.values() if isinstance(v, list)), [])
    return data or []


def concept_catalog(cache: Path) -> dict[str, str]:
    fp = cache / "concepts.json"
    if fp.exists() and datetime.now().timestamp() - fp.stat().st_mtime < 7 * 86400:
        return json.loads(fp.read_text(encoding="utf-8"))
    rows = _items(ths(["index", "catalog", "--tag", "cn_concept"], cache / "_catalog.json"))
    names = {r["thscode"]: r["name"] for r in rows}
    fp.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    return names


def _history(code: str, start: pd.Timestamp, end: pd.Timestamp, tmp: Path) -> pd.Series:
    rows = _items(ths(["index", "history", "--thscode", code, "--start-ms", str(_ms(start)),
                       "--end-ms", str(_ms(end))], tmp))
    if not rows:
        return pd.Series(dtype=float)
    d = pd.DataFrame(rows)
    idx = pd.to_datetime(d.date_ms, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
    return pd.Series(d.close_price.astype(float).to_numpy(), index=idx)


def ensure_concept_closes(cache: Path, as_of: pd.Timestamp, calendar: pd.DatetimeIndex,
                          latest_session: pd.Timestamp, log=print) -> tuple[pd.DataFrame, dict]:
    """Closes for all concepts covering the 21 sessions up to as_of; refills only what is missing."""
    cache.mkdir(parents=True, exist_ok=True)
    names = concept_catalog(cache)
    fp = cache / "concept_close.csv"
    wide = pd.read_csv(fp, index_col=0, parse_dates=True) if fp.exists() else pd.DataFrame()
    need = calendar[calendar <= as_of][-21:]
    have = wide.reindex(index=need, columns=list(names)).notna()
    complete = have.all(axis=0)
    via = "cache"
    prev = need[-2] if len(need) >= 2 else None
    if not complete.all() and as_of == latest_session and prev is not None and prev in wide.index:
        # snapshot shortcut: only for concepts whose earlier 20 sessions are already cached
        ready = [c for c in names if have[c].iloc[:-1].all()]
        snap = []
        for k in range(0, len(ready), SNAPSHOT_BATCH):
            snap += _items(ths(["index", "snapshot", "--thscodes", ",".join(ready[k:k + SNAPSHOT_BATCH])],
                               cache / "_snap.json"))
        add = {}
        for r in snap:
            c, pp = r.get("thscode"), r.get("prev_price")
            if c in wide.columns and pp is not None and abs(float(pp) / wide.at[prev, c] - 1) <= PREV_TOL:
                add[c] = float(r["last_price"])
        if add:
            wide = wide.combine_first(pd.DataFrame(add, index=[as_of]))
        via = f"snapshot({len(add)})"
        have = wide.reindex(index=need, columns=list(names)).notna()
        complete = have.all(axis=0)
    missing = [c for c in names if not complete[c]]
    if missing:
        log(f"[THEME] fetching history for {len(missing)} concepts (~{len(missing) * 0.8 / 60:.0f} min)")
        start = need[0] - timedelta(days=5)
        fetched = {}
        for k, c in enumerate(missing):
            try:
                fetched[c] = _history(c, start, as_of, cache / "_hist.json")
            except RuntimeError:
                continue
            if k % 50 == 49:
                log(f"[THEME] {k + 1}/{len(missing)}")
        if fetched:
            wide = pd.DataFrame(fetched).combine_first(wide)
        via += f"+history({len(fetched)}/{len(missing)})"
    wide = wide.sort_index()
    wide.to_csv(fp, encoding="utf-8")
    sub = wide.reindex(index=need, columns=list(names))
    return sub, {"concepts": len(names), "complete": int(sub.notna().all().sum()), "source": via}


def concept_strength(closes: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """1/5/20-session returns on the last row; concepts lacking any of them are dropped."""
    c = closes.astype(float)
    if len(c) < 21:
        return pd.DataFrame(columns=["code", "name", "ret1", "ret5", "ret20"])
    last = c.iloc[-1]
    out = pd.DataFrame({"code": c.columns, "name": [names.get(x, x) for x in c.columns],
                        "ret1": (last / c.iloc[-2] - 1).to_numpy() * 100,
                        "ret5": (last / c.iloc[-6] - 1).to_numpy() * 100,
                        "ret20": (last / c.iloc[-21] - 1).to_numpy() * 100})
    out = out.dropna(subset=["ret1", "ret5", "ret20"])
    out["rank5"] = out.ret5.rank(ascending=False, method="min").astype(int)
    return out.sort_values("ret5", ascending=False).reset_index(drop=True)


def ensure_limit_pools(cache: Path, sessions: list[pd.Timestamp], log=print) -> dict[str, list[dict]]:
    """Full limit-up pool per session (all pages), cached once per session."""
    folder = cache / "limit_up"
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {}
    for d in sessions:
        fp = folder / f"{d:%Y%m%d}.json"
        if fp.exists():
            out[str(d.date())] = json.loads(fp.read_text(encoding="utf-8"))
            continue
        rows, page = [], 1
        try:
            while True:
                data = ths(["special", "limit-up-pool", "--date-ms", str(_ms(d)), "--size", "200",
                            "--page", str(page)], folder / "_page.json")
                rows += _items(data)
                if page >= (data.get("pagination") or {}).get("pages", 1):
                    break
                page += 1
        except RuntimeError as exc:
            log(f"[THEME] limit-up pool {d.date()} unavailable: {exc}")
            continue
        fp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        out[str(d.date())] = rows
    return out


def split_reasons(reason: str | None) -> list[str]:
    return [t.strip() for t in (reason or "").replace("＋", "+").split("+") if t.strip()]


def reason_heat(pools: dict[str, list[dict]], as_of: str) -> pd.DataFrame:
    """Keyword counts today and across the cached sessions, days present, and today's stocks."""
    today, total, days = Counter(), Counter(), Counter()
    stocks: dict[str, list[str]] = {}
    for d, rows in pools.items():
        seen = set()
        for r in rows:
            for k in split_reasons(r.get("limit_up_reason")):
                total[k] += 1
                seen.add(k)
                if d == as_of:
                    today[k] += 1
                    stocks.setdefault(k, []).append(r.get("name", ""))
        for k in seen:
            days[k] += 1
    rows = [{"keyword": k, "today": today.get(k, 0), "window": n, "days": days[k],
             "today_stocks": "、".join(stocks.get(k, [])[:6])} for k, n in total.items()]
    df = pd.DataFrame(rows, columns=["keyword", "today", "window", "days", "today_stocks"])
    return df.sort_values(["window", "today"], ascending=False).reset_index(drop=True)


def ladder(rows: list[dict], min_streak: int = 2) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty or "continue_day_cnt" not in df:
        return pd.DataFrame(columns=["thscode", "name", "continue_day_cnt", "limit_up_time", "limit_up_reason"])
    df = df.loc[df.continue_day_cnt.fillna(1) >= min_streak]
    return df.sort_values(["continue_day_cnt", "limit_up_time"], ascending=[False, True])[
        ["thscode", "name", "continue_day_cnt", "limit_up_time", "limit_up_reason"]].reset_index(drop=True)


def latest_reason(pools: dict[str, list[dict]]) -> dict[str, tuple[str, str]]:
    """code -> (most recent limit-up date in the window, its reason)."""
    out: dict[str, tuple[str, str]] = {}
    for d in sorted(pools):
        for r in pools[d]:
            out[r["thscode"]] = (d, r.get("limit_up_reason") or "")
    return out


def strong_stocks(codes: np.ndarray, close: np.ndarray, amount: np.ndarray, exclude: np.ndarray,
                  top: int = 25, min_amount: float = 1e8) -> pd.DataFrame:
    """Top 10-session gainers among liquid, non-excluded stocks (as-of = last column)."""
    t = close.shape[1] - 1
    with np.errstate(invalid="ignore", divide="ignore"):
        r10 = close[:, t] / close[:, t - 10] - 1
        r5 = close[:, t] / close[:, t - 5] - 1
        r1 = close[:, t] / close[:, t - 1] - 1
    amt = np.nanmean(amount[:, t - 4:t + 1], axis=1)
    ok = np.isfinite(r10) & ~exclude & (amt >= min_amount)
    idx = np.flatnonzero(ok)[np.argsort(-r10[ok])][:top]
    return pd.DataFrame({"code": codes[idx].astype(str), "ret10": r10[idx] * 100, "ret5": r5[idx] * 100,
                         "ret1": r1[idx] * 100, "amount_yi": amt[idx] / 1e8})
