"""Read-only daily board: engine gates, same-day pattern candidates, leader-zone ranking.

Reuses the frozen research definitions unchanged and produces NO orders:
  ice engines (pattern-ice, pool-ice)  gate a0.22_s2_w1_none, skipped on overheat days
  chase engine                         repair day & amount/prev5 >= 1.05 & zt >= 20 & streak > 2
                                       (the loose variant used by final combo F)
  patterns                             trend_start.drop_base_retest: quiet / breakout / confirmed
  circle pool                          train-track signal within the last 60 sessions
  leader zone                          weekly score with the IC weights recorded in
                                       output/monitor/score_model_report.md (contaminated
                                       history; ranking aid, not out-of-sample evidence)
Every value at the as-of session uses bars <= as-of only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trend_start.drop_base_retest import PatternConfig, pattern_entry_events
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment, limit_matrix
from trend_start.engine import LIMIT_TOL
from trend_start.panel import FIELDS, PREFIXES, Panel
from trend_start.train_track import train_track_signals

ICE_GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
OVERHEAT = 0.85
CHASE_AMOUNT = 1.05
CHASE_ZT = 20
CHASE_STREAK = 2
POOL_DAYS = 60
LEADER_WEIGHTS = {"rps120": -0.21, "close_recover": -0.23, "low_touch": -0.14,
                  "vol_dry": -0.11, "ret52": -0.10, "trend_age": 0.09, "ma_align": -0.08}
RECENT_SESSIONS = 420  # covers RPS250, MA250, 52-week bars and the pattern state machine


def build_recent_panel(tdx_root: Path, as_of: pd.Timestamp,
                       sessions: int = RECENT_SESSIONS) -> tuple[Panel, dict[str, Any]]:
    """Same read/adjust path as trend_start.panel.build_panel, kept to the last `sessions`
    sessions and not cached (the full panel is ~540MB per day)."""
    from pytdx.reader import GbbqReader

    from run_daily_rps import read_day
    from run_local_tdx_compare import adjust

    cal = pd.DatetimeIndex(read_day(tdx_root / "vipdoc/sh/lday/sh000001.day").trade_date)
    cal = cal[cal <= as_of]
    if len(cal) == 0 or cal[-1] != as_of:
        raise ValueError(f"index calendar has no session {as_of.date()}")
    cal = cal[-sessions:]
    pos = pd.Series(np.arange(len(cal)), index=cal)
    events = GbbqReader().get_df(str(tdx_root / "T0002/hq_cache/gbbq"))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d", errors="raise")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    grouped = dict(tuple(events.groupby("ts_code")))
    empty = events.iloc[:0]

    paths = [(f"{p.stem[2:]}.{market.upper()}", p)
             for market, prefixes in PREFIXES.items()
             for p in sorted((tdx_root / "vipdoc" / market / "lday").glob("*.day"))
             if p.stem[:2] == market and p.stem[2:].startswith(prefixes) and len(p.stem) == 8]
    n, m = len(paths), len(cal)
    arrays = {f: np.full((n, m), np.nan, dtype=np.float32) for f in FIELDS}
    kept: list[str] = []
    excluded = 0
    for code, path in paths:
        try:
            d = read_day(path)
            d = d.loc[(d.trade_date <= as_of) & (d.vol > 0)].reset_index(drop=True)
            if d.empty or d.trade_date.iloc[-1] < cal[0]:
                continue  # delisted or long suspended before the window
            d, _ = adjust(d, grouped.get(code, empty), as_of, bars=None)
            d = d.loc[d.trade_date >= cal[0]]
            if (~d.trade_date.isin(pos.index)).any():
                raise ValueError("bars not on SSE calendar")
        except Exception:  # noqa: BLE001 - counted, security excluded like build_panel
            excluded += 1
            continue
        cols = pos.loc[d.trade_date].to_numpy()
        row = len(kept)
        for f in FIELDS:
            arrays[f][row, cols] = d[f].to_numpy(dtype=np.float32)
        kept.append(code)
    for f in FIELDS:
        arrays[f] = arrays[f][:len(kept)]
    hs300 = read_day(tdx_root / "vipdoc/sh/lday/sh000300.day").set_index("trade_date").close.reindex(cal)
    panel = Panel(dates=cal.to_numpy().astype("datetime64[D]"), codes=np.array(kept),
                  data=arrays, index_close=hs300.to_numpy(dtype=np.float64))
    return panel, {"as_of": str(as_of.date()), "sessions": m, "first_session": str(cal[0].date()),
                   "files_seen": n, "kept": len(kept), "excluded": excluded}


def engine_days(panel: Panel) -> pd.DataFrame:
    """Per-session market sentiment plus each engine's gate, frozen definitions."""
    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"), panel.field("high"),
                           panel.field("close"), panel.field("amount"))
    ice = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, ICE_GATE)
    hot = sent.advance_share.to_numpy() >= OVERHEAT
    with np.errstate(invalid="ignore", divide="ignore"):
        surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    chase = (ice.repair.to_numpy(bool) & (surge >= CHASE_AMOUNT)
             & (sent.zt_count.to_numpy() >= CHASE_ZT) & (sent.max_streak.to_numpy() > CHASE_STREAK))
    out = pd.concat([sent, ice[["ice_level", "ice_extreme", "repair", "reversal"]]], axis=1)
    out["amount_vs_prev5"] = surge
    out["ice_gate"] = gate
    out["overheat"] = hot
    out["ice_engine_on"] = gate & ~hot
    out["chase_engine_on"] = chase
    return out


def _stock_frame(panel: Panel, i: int) -> tuple[pd.DataFrame, np.ndarray]:
    cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
    return pd.DataFrame({f: panel.field(f)[i, cols].astype(float) for f in FIELDS}), cols


def pattern_candidates(panel: Panel, cfg: PatternConfig) -> pd.DataFrame:
    """Stocks whose pattern state machine emits an entry event on the as-of bar."""
    last = len(panel.dates) - 1
    lim = limit_matrix(panel.codes, panel.dates)[:, last] - LIMIT_TOL
    rows = []
    for i, code in enumerate(panel.codes):
        d, cols = _stock_frame(panel, i)
        if len(cols) < 2 or cols[-1] != last:
            continue
        try:
            events = pattern_entry_events(d, cfg)
        except ValueError:
            continue
        for bar, kind, pivot, floor in events:
            if bar != len(d) - 1:
                continue
            c, pc = d.close.iloc[-1], d.close.iloc[-2]
            rows.append({"code": str(code), "kind": kind, "close": c, "pct": (c / pc - 1) * 100,
                         "pivot": pivot, "floor": floor, "amount_yi": d.amount.iloc[-1] / 1e8,
                         "at_limit_up": bool(c / pc - 1 >= lim[i])})
    return pd.DataFrame(rows, columns=["code", "kind", "close", "pct", "pivot", "floor",
                                       "amount_yi", "at_limit_up"])


def circle_pool(panel: Panel, days: int = POOL_DAYS) -> pd.DataFrame:
    """Train-track circle within the last `days` sessions (the pool-ice watchlist)."""
    frame = {f: pd.DataFrame(panel.field(f).astype(float), index=panel.codes)
             for f in ("open", "high", "low", "close")}
    sig = train_track_signals(frame["open"], frame["high"], frame["low"], frame["close"]).to_numpy(bool)
    recent = sig[:, -days:]
    hit = recent.any(axis=1)
    last_sig = np.where(hit, days - 1 - np.argmax(recent[:, ::-1], axis=1), -1)
    return pd.DataFrame({"code": panel.codes[hit].astype(str), "sessions_since_circle": last_sig[hit]})


def _pct_rank(x: np.ndarray) -> np.ndarray:
    out = np.full(len(x), np.nan)
    ok = np.isfinite(x)
    if ok.sum() >= 2:
        out[ok] = pd.Series(x[ok]).rank(method="average", pct=True).to_numpy() * 100
    return out


def _roll(a: np.ndarray, w: int, fn: str) -> np.ndarray:
    return getattr(pd.DataFrame(a).T.rolling(w, min_periods=w), fn)().T.to_numpy()


def leader_ranking(panel: Panel, exclude: np.ndarray) -> pd.DataFrame:
    """Leader-zone mask and composite score on the as-of (possibly partial) week."""
    dates = pd.DatetimeIndex(panel.dates)
    wk = dates.to_period("W-FRI")
    uniq = wk.unique().sort_values()
    wid = np.searchsorted(uniq, wk)
    nw = len(uniq)
    last_day = np.array([np.flatnonzero(wid == w)[-1] for w in range(nw)])
    close, low, vol = (panel.field(f).astype(np.float64) for f in ("close", "low", "vol"))
    n = len(panel.codes)
    wc = close[:, last_day]
    wl = np.full((n, nw), np.inf)
    np.minimum.at(wl, (slice(None), wid), np.where(np.isfinite(low), low, np.inf))
    wl[np.isinf(wl)] = np.nan
    wv = np.zeros((n, nw))
    np.add.at(wv, (slice(None), wid), np.where(np.isfinite(vol), vol, 0.0))
    ma10, ma20 = _roll(wc, 10, "mean"), _roll(wc, 20, "mean")
    hi52, lo52 = _roll(wc, 52, "max"), _roll(wc, 52, "min")
    universe = close.copy()
    universe[exclude] = np.nan
    r120 = np.full((n, nw), np.nan)
    for w, j in enumerate(last_day):
        if j >= 120:
            r120[:, w] = _pct_rank(universe[:, j] / universe[:, j - 120] - 1)
    above = np.isfinite(ma20) & (wc > ma20)
    age = np.zeros((n, nw))
    for w in range(1, nw):
        age[:, w] = np.where(above[:, w], age[:, w - 1] + 1, 0)
    prev4 = _roll(np.hstack([np.full((n, 1), np.nan), wv[:, :-1]]), 4, "mean")
    hi90 = (r120 >= 90).astype(float)
    peg = pd.DataFrame(hi90).T.rolling(4, min_periods=1).sum().T.to_numpy()
    w = nw - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        dims = {"rps120": r120[:, w], "close_recover": wc[:, w] / ma20[:, w] - 1,
                "low_touch": wl[:, w] / ma20[:, w] - 1, "vol_dry": wv[:, w] / prev4[:, w],
                "ret52": wc[:, w] / lo52[:, w] - 1, "trend_age": age[:, w],
                "ma_align": ma10[:, w] / ma20[:, w] - 1}
        near_high = wc[:, w] / hi52[:, w]
    mask = ((r120[:, w] >= 90) & (wc[:, w] > ma20[:, w]) & (ma10[:, w] > ma20[:, w])
            & (near_high >= 0.70) & ~exclude)
    idx = np.flatnonzero(mask)
    norm = sum(abs(v) for v in LEADER_WEIGHTS.values())
    score = np.zeros(len(idx))
    for k, wgt in LEADER_WEIGHTS.items():
        v = dims[k][idx]
        r = np.full(len(v), 0.5)
        ok = np.isfinite(v)
        if ok.sum() >= 30:
            r[ok] = (pd.Series(v[ok]).rank(method="average").to_numpy() - 1) / (ok.sum() - 1)
        score += wgt / norm * r
    out = pd.DataFrame({"code": panel.codes[idx].astype(str), "score": score,
                        "near_high": near_high[idx], "rps_weeks_4": peg[idx, w],
                        **{k: v[idx] for k, v in dims.items()}})
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out.attrs.update({"week_end": str(dates[last_day[w]].date()),
                      "week_complete": bool(dates[last_day[w]].dayofweek == 4),
                      "zone_size": int(mask.sum()), "score_ranked": bool(len(idx) >= 30)})
    return out


def industry_of(codes: pd.Series, industry_map: pd.DataFrame) -> pd.Series:
    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    return codes.map(ind)


def whitelist_tag(industry: pd.Series, sectors: pd.DataFrame, whitelist: set[str]) -> pd.DataFrame:
    """Longest sector-code prefix gives the quadrant; whitelist membership is by prefix,
    since the whitelist mixes TDX industry levels (e.g. T0401 and T060102)."""
    codes = sorted(sectors.index.astype(str), key=len, reverse=True)
    def match(c: Any) -> str | None:
        return next((s for s in codes if isinstance(c, str) and c.startswith(s)), None)
    hit = industry.map(match)
    return pd.DataFrame({
        "industry_name": hit.map(lambda s: sectors.at[s, "name"] if s else None),
        "quadrant": hit.map(lambda s: sectors.at[s, "quadrant_cn"] if s else None),
        "in_whitelist": industry.map(lambda c: isinstance(c, str) and any(c.startswith(w) for w in whitelist)),
    }, index=industry.index)
