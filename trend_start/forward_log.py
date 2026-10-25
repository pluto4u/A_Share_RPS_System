"""Forward (paper) ledger for the frozen combo-F engines. Record only; never places orders.

Engines and priority follow final combo F: pool-ice (0) > pattern-ice (1) > chase (2), one shared
20-slot pool. Definitions are the research ones, unchanged:
  pool-ice     train-track circle in the 60 sessions BEFORE today (circle day excluded), ice gate
               a0.22_s2_w1_none and not overheat, >=260 traded bars, >=5 traded bars since the last
               pool entry of the same stock; tail buy at the close; exit fixed5
  pattern-ice  drop_base_retest 'quiet' event on today's bar, same gate; tail buy; exit fixed5
  chase        'breakout'/'confirmed' event on a weak-resonance repair day; tail buy; exit fixed3
A one-price limit-up close cannot be bought. Exits use pattern_backtest._sell_close: close of the
h-th traded bar after entry, a one-price limit-down rolls to the next sellable open.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from trend_start.daily_board import engine_days, pattern_candidates
from trend_start.drop_base_retest import PatternConfig
from trend_start.engine import BUY_COST
from trend_start.icepoint import limit_matrix
from trend_start.engine import LIMIT_TOL
from trend_start.pattern_backtest import _sell_close, exec_flags
from trend_start.panel import Panel
from trend_start.train_track import train_track_signals

SLOTS = 20
POOL_DAYS = 60
ENTRY_GAP = 5
MIN_BARS = 260
HOLD = {"pool_ice": 5, "pattern_ice": 5, "chase": 3}
PRIO = {"pool_ice": 0, "pattern_ice": 1, "chase": 2}
FROZEN_FILES = ("trend_start/forward_log.py", "trend_start/daily_board.py", "trend_start/drop_base_retest.py",
                "trend_start/icepoint.py", "trend_start/ice_opt.py", "trend_start/train_track.py",
                "trend_start/pattern_backtest.py", "trend_start/engine.py", "run_local_tdx_compare.py",
                "run_daily_rps.py", "run_forward_log.py")


def code_hashes(root: Path) -> dict[str, str]:
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


def sessions_to_process(calendar: pd.DatetimeIndex, freeze_date: pd.Timestamp,
                        last_done: pd.Timestamp | None) -> list[pd.Timestamp]:
    """Strictly after the freeze date and after the last processed session, in order."""
    floor = freeze_date if last_done is None else max(freeze_date, last_done)
    return [d for d in calendar if d > floor]


def pool_members(circles: np.ndarray, t: int, days: int = POOL_DAYS) -> np.ndarray:
    """Circle within sessions [t-days, t-1]: the research pool excludes the circle day itself."""
    lo = max(0, t - days)
    return circles[:, lo:t].any(axis=1) if t > lo else np.zeros(circles.shape[0], bool)


def candidates(panel: Panel, ledger: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Same-day engine candidates on the panel's last session (research definitions)."""
    t = len(panel.dates) - 1
    day = engine_days(panel).iloc[-1]
    state = {"ice_engine_on": bool(day.ice_engine_on), "chase_engine_on": bool(day.chase_engine_on),
             "advance_share": float(day.advance_share), "zt": float(day.zt_count),
             "max_streak": float(day.max_streak), "amount_vs_prev5": float(day.amount_vs_prev5)}
    c, h, lo = (panel.field(f) for f in ("close", "high", "low"))
    with np.errstate(invalid="ignore", divide="ignore"):
        lim = limit_matrix(panel.codes, panel.dates)[:, t] - LIMIT_TOL
        up_locked = (c[:, t] / c[:, t - 1] - 1 >= lim) & (h[:, t] == lo[:, t])
    traded = np.isfinite(c[:, t])
    bars = np.isfinite(c).sum(axis=1)
    codes = panel.codes.astype(str)
    rows = []
    if state["ice_engine_on"]:
        frame = {f: pd.DataFrame(panel.field(f).astype(float), index=codes) for f in ("open", "high", "low", "close")}
        circles = train_track_signals(frame["open"], frame["high"], frame["low"], frame["close"]).to_numpy(bool)
        member = pool_members(circles, t)
        last_pool = (ledger.loc[ledger.engine == "pool_ice"].groupby("code").signal_date.max()
                     if len(ledger) else pd.Series(dtype=object))
        for i in np.flatnonzero(member & traded & ~up_locked & (bars >= MIN_BARS)):
            prev = last_pool.get(codes[i])
            if prev is not None:
                cols = np.flatnonzero(np.isfinite(c[i]))
                k = np.searchsorted(panel.dates[cols], np.datetime64(pd.Timestamp(prev).date()))
                if len(cols) - 1 - k < ENTRY_GAP:
                    continue
            rows.append({"engine": "pool_ice", "code": codes[i]})
    if state["ice_engine_on"] or state["chase_engine_on"]:
        pats = pattern_candidates(panel, PatternConfig())
        idx = {cd: i for i, cd in enumerate(codes)}
        for r in pats.itertuples():
            i = idx[r.code]
            if up_locked[i]:
                continue
            if r.kind == "quiet" and state["ice_engine_on"]:
                rows.append({"engine": "pattern_ice", "code": r.code})
            if r.kind in ("breakout", "confirmed") and state["chase_engine_on"]:
                rows.append({"engine": "chase", "code": r.code})
    cand = pd.DataFrame(rows, columns=["engine", "code"])
    if len(cand):
        cand["prio"] = cand.engine.map(PRIO)
        cand["close"] = [float(c[np.flatnonzero(codes == cd)[0], t]) for cd in cand.code]
    return cand, state


def allocate(cand: pd.DataFrame, held: set[str], free: int) -> pd.DataFrame:
    """Fill free slots by (prio, code); a stock is held at most once across engines."""
    if free <= 0 or cand.empty:
        return cand.iloc[:0]
    c = cand.sort_values(["prio", "code"]).drop_duplicates("code")
    return c.loc[~c.code.isin(held)].head(free)


def settle(open_trades: pd.DataFrame, panel: Panel) -> pd.DataFrame:
    """Exit price/date for open trades whose h-th traded bar is in the panel; others stay open."""
    codes = {cd: i for i, cd in enumerate(panel.codes.astype(str))}
    out = []
    for r in open_trades.itertuples():
        i = codes.get(r.code)
        if i is None:
            continue
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        dts = panel.dates[cols]
        e = int(np.searchsorted(dts, np.datetime64(pd.Timestamp(r.signal_date).date())))
        if e >= len(cols) or dts[e] != np.datetime64(pd.Timestamp(r.signal_date).date()):
            continue
        o, h, lo, c = (panel.field(f)[i, cols].astype(float) for f in ("open", "high", "low", "close"))
        j = e + HOLD[r.engine]
        if j >= len(cols):
            continue
        fl = exec_flags(r.code, o, h, lo, c, dts)
        bar, px, unfinished, _ = _sell_close(j, len(cols), fl["locked"], fl["down_open"], o, c)
        if unfinished:
            continue
        ret = px * (1 - fl["sc"][bar]) / (c[e] * (1 + BUY_COST)) - 1
        out.append({"trade_id": r.trade_id, "exit_date": str(pd.Timestamp(dts[bar]).date()),
                    "exit_px_adj": px, "ret": float(ret), "held_bars": int(bar - e)})
    return pd.DataFrame(out, columns=["trade_id", "exit_date", "exit_px_adj", "ret", "held_bars"])
