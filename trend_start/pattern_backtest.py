"""Trade simulation for drop-base-retest pattern entries (research only).

Entry kinds (signal bar flags from trend_start.drop_base_retest.pattern_events):
  breakout    放量突破当日
  quiet       缩量回踩当日 (默认主名单口径)
  confirmed   回踩后再转强当日
  loud        放量回踩当日 (独立对照)

Entry timings:
  tail       buy at the SIGNAL bar close (proxy for a 14:50 tail-session fill)
  next_open  signal at close -> buy next session open (existing convention)

Exit rules (checked causally from the entry bar onward):
  invalidation: close < frozen floor, or two consecutive closes below
                pivot * (1 - close_below) -> sell next open (limit-down rolls)
  time stop:    max_hold bars after entry -> sell at that close
                (a limit-down locked close rolls to the next open)
Unfinished trades are marked at the last close with selling cost deducted.

Costs, limit percentages and T+1 follow trend_start.engine exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trend_start.engine import BUY_COST, LIMIT_TOL, limit_pct, sell_cost

ENTRY_KINDS = ("breakout", "quiet", "confirmed", "loud")
TIMINGS = ("tail", "next_open")
FWD_HORIZONS = (1, 2, 5, 10, 20)


@dataclass(frozen=True)
class ExitConfig:
    close_below: float = 0.02   # matches PatternConfig.close_below
    max_hold: int = 20          # time stop in traded bars
    allow_breakout_entry: bool = True


DEFAULT_EXIT = ExitConfig()  # frozen dataclass; safe shared default


def entry_bars(events: pd.DataFrame) -> list[tuple[int, str]]:
    """(bar, kind) pairs from one stock's pattern_events frame, causal order."""
    out: list[tuple[int, str]] = []
    for t, row in events.iterrows():
        if bool(row.breakout):
            out.append((int(t), "breakout"))
        if bool(row.quiet_touch):
            out.append((int(t), "quiet"))
        if bool(row.confirmed):
            out.append((int(t), "confirmed"))
        if bool(row.loud_touch):
            out.append((int(t), "loud"))
    return out


def _sell_next_open(j: int, n: int, down_open: np.ndarray, locked: np.ndarray,
                    o: np.ndarray, c: np.ndarray) -> tuple[int, float, bool, bool]:
    """Trigger on bar j -> sell at open of j+1; limit-down rules as engine._sell_open.

    Returns (bar, price, unfinished, at_close).
    """
    k = j + 1
    while k < n and locked[k]:
        k += 1
    if k >= n:
        return n - 1, float(c[-1]), True, True
    if down_open[k]:  # limit-down open on a non-one-price bar sells at that close
        return k, float(c[k]), False, True
    return k, float(o[k]), False, False


def _sell_close(j: int, n: int, locked: np.ndarray, down_open: np.ndarray,
                o: np.ndarray, c: np.ndarray) -> tuple[int, float, bool, bool]:
    """Sell at close of bar j; a one-price limit-down close rolls to the next open."""
    if locked[j]:
        return _sell_next_open(j, n, down_open, locked, o, c)
    return j, float(c[j]), False, True


def exec_flags(code: str, o: np.ndarray, h: np.ndarray, lo: np.ndarray,
               c: np.ndarray, dates: np.ndarray) -> dict[str, np.ndarray]:
    """Per-stock execution flags, hoisted so many trades share one computation."""
    prev = np.r_[np.nan, c[:-1]]
    lim = limit_pct(code, dates) - LIMIT_TOL
    with np.errstate(invalid="ignore", divide="ignore"):
        up_open = o / prev - 1 >= lim
        up_locked = (c / prev - 1 >= lim) & (h == lo)
        down_open = o / prev - 1 <= -lim
        down_close = c / prev - 1 <= -lim
    return {
        "up_open": up_open,
        "up_locked": up_locked,
        "down_open": down_open,
        "locked": down_close & (h == lo),
        "sc": sell_cost(dates),
    }


def simulate_trade(
    code: str,
    sig: int,
    kind: str,
    timing: str,
    pivot: float,
    floor: float,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cal: np.ndarray,
    dates: np.ndarray,
    exit_cfg: ExitConfig = DEFAULT_EXIT,
    flags: dict[str, np.ndarray] | None = None,
) -> dict | None:
    """One entry -> one trade record; None when the entry is not executable."""
    n = len(c)
    if flags is None:
        flags = exec_flags(code, o, h, lo, c, dates)
    up_open = flags["up_open"]
    up_locked = flags["up_locked"]
    down_open = flags["down_open"]
    locked = flags["locked"]
    sc = flags["sc"]

    if timing == "tail":
        e = sig
        if up_locked[e]:  # cannot buy into a one-price limit-up close
            return None
        raw_entry = float(c[e])
    else:
        e = sig + 1
        if e >= n or int(cal[e]) != int(cal[sig]) + 1:
            return None  # suspension gap: next traded bar is not the next session
        if up_open[e]:
            return None
        raw_entry = float(o[e])
    entry_cost = raw_entry * (1 + BUY_COST)

    below = 0
    exit_bar, exit_px, unfinished, at_close, reason = -1, np.nan, True, True, "unfinished"
    for j in range(e, n):
        below = below + 1 if c[j] < pivot * (1 - exit_cfg.close_below) else 0
        if c[j] < floor or below >= 2:
            # invalidation flagged at close of j; T+1 -> sell next open
            k, px, unf, ac = _sell_next_open(j, n, down_open, locked, o, c)
            exit_bar, exit_px, unfinished, at_close, reason = k, px, unf, ac, "invalidation"
            break
        if j - e >= exit_cfg.max_hold:
            # held for max_hold bars after entry -> sell at this close
            k, px, unf, ac = _sell_close(j, n, locked, down_open, o, c)
            exit_bar, exit_px, unfinished, at_close, reason = k, px, unf, ac, "time_stop"
            break
    if exit_bar < 0:
        exit_bar, exit_px = n - 1, float(c[-1])

    ret = exit_px * (1 - sc[exit_bar]) / entry_cost - 1
    row = {
        "code": code,
        "sig_bar": sig,
        "sig_cal": int(cal[sig]),
        "kind": kind,
        "timing": timing,
        "entry_bar": e,
        "entry_cal": int(cal[e]),
        "entry_px": raw_entry,
        "exit_bar": exit_bar,
        "exit_cal": int(cal[exit_bar]),
        "exit_px": float(exit_px),
        "exit_at_close": bool(at_close),
        "exit_reason": reason,
        "unfinished": bool(unfinished),
        "hold": exit_bar - e,
        "ret": float(ret),
    }
    for hz in FWD_HORIZONS:
        k = e + hz
        r = np.nan
        if k < n:
            r = float(c[k] * (1 - sc[k]) / entry_cost - 1)
        row[f"fwd{hz}"] = r
    return row


def simulate_entries(
    code: str,
    entries: list[tuple[int, str, float, float]],
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cal: np.ndarray,
    dates: np.ndarray,
    exit_cfg: ExitConfig = DEFAULT_EXIT,
    timings: tuple[str, ...] = TIMINGS,
) -> list[dict]:
    """All trades for one stock from (bar, kind, pivot, floor) entry tuples."""
    flags = exec_flags(code, o, h, lo, c, dates)
    rows: list[dict] = []
    for sig, kind, pivot, floor in entries:
        if not (np.isfinite(pivot) and np.isfinite(floor)):
            continue
        if kind == "breakout" and not exit_cfg.allow_breakout_entry:
            continue
        for timing in timings:
            rec = simulate_trade(code, sig, kind, timing, pivot, floor,
                                 o, h, lo, c, cal, dates, exit_cfg, flags=flags)
            if rec is not None:
                rows.append(rec)
    return rows


def simulate_stock(
    code: str,
    events: pd.DataFrame,
    offset: int,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cal: np.ndarray,
    dates: np.ndarray,
    exit_cfg: ExitConfig = DEFAULT_EXIT,
    timings: tuple[str, ...] = TIMINGS,
) -> list[dict]:
    """All trades for one stock.

    `events` is pattern_events output indexed by position within the sliced
    arrays; `offset` maps event index 0 to the sliced bar index (0 when the
    slice is the full history).
    """
    entries = [
        (t + offset, kind, float(events.loc[t, "pivot"]), float(events.loc[t, "floor"]))
        for t, kind in entry_bars(events)
    ]
    return simulate_entries(code, entries, o, h, lo, c, cal, dates, exit_cfg, timings)


def control_fwd_returns(close: np.ndarray, dates: np.ndarray,
                        horizons: tuple[int, ...] = FWD_HORIZONS) -> dict[int, np.ndarray]:
    """Same-day cross-sectional mean forward return of ALL stocks (tail timing).

    close: (n_stocks, n_sessions) panel matrix. Returns horizon -> (n_sessions,)
    array of equal-weight means, NaN where no stock has a valid pair.
    """
    c = close.astype(np.float64)
    sc = sell_cost(dates)
    out: dict[int, np.ndarray] = {}
    for hz in horizons:
        fut = np.full_like(c, np.nan)
        fut[:, :-hz] = c[:, hz:]
        cost = np.full(c.shape[1], np.nan)
        cost[:-hz] = sc[hz:]
        with np.errstate(invalid="ignore", divide="ignore"):
            r = fut * (1 - cost) / (c * (1 + BUY_COST)) - 1
        valid = np.isfinite(r)
        cnt = valid.sum(axis=0)
        out[hz] = np.where(cnt > 0, np.nansum(r, axis=0) / np.maximum(cnt, 1), np.nan)
    return out


def portfolio_from_trades(
    trades: pd.DataFrame,
    close_ff: np.ndarray,
    code_row: dict[str, int],
    dates: np.ndarray,
    start: int,
    end: int,
    slots: int = 20,
    gate_mult: dict[int, float] | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Equal-weight slot portfolio over calendar [start, end] from simulated trades.

    Entry/exit prices and dates come from the per-trade simulation (no
    re-decision here). When gate_mult is given (calendar day -> position
    multiplier), each day's new entries are scaled by the multiplier of the
    SIGNAL day; a multiplier of 0 blocks entries that day.
    """
    sc = sell_cost(dates)
    ev = trades.loc[(trades.entry_cal >= start) & (trades.entry_cal <= end)].copy()
    ev = ev.sort_values(["entry_cal", "code"])
    by_day = {d: g for d, g in ev.groupby("entry_cal")}
    cash, equity_prev = 1.0, 1.0
    held: dict[str, dict] = {}
    curve = np.full(end - start + 1, np.nan)
    done: list[dict] = []

    def close_pos(code: str, d: int, px: float, unfinished: bool) -> None:
        nonlocal cash
        p = held.pop(code)
        proceeds = p["shares"] * px * (1 - sc[d])
        cash += proceeds
        done.append({**p["row"], "pf_exit_cal": d, "pf_ret": proceeds / p["cost"] - 1,
                     "pf_unfinished": unfinished})

    for d in range(start, end + 1):
        for code in [k for k, p in held.items()
                     if p["exit_cal"] == d and not p["exit_at_close"]]:
            close_pos(code, d, held[code]["exit_px"], False)
        if d in by_day and len(held) < slots:
            target = equity_prev / slots
            for r in by_day[d].itertuples(index=False):
                mult = 1.0 if gate_mult is None else gate_mult.get(int(r.sig_cal), 0.0)
                if mult <= 0 or len(held) >= slots or cash <= target * mult * 0.25:
                    continue
                if r.code in held:
                    continue
                alloc = min(target * mult, cash)
                cash -= alloc
                held[r.code] = {
                    "shares": alloc / (r.entry_px * (1 + BUY_COST)),
                    "cost": alloc,
                    "exit_cal": int(r.exit_cal),
                    "exit_px": float(r.exit_px),
                    "exit_at_close": bool(r.exit_at_close),
                    "row": r._asdict(),
                }
        for code in [k for k, p in held.items()
                     if p["exit_cal"] == d and p["exit_at_close"]]:
            close_pos(code, d, held[code]["exit_px"], False)
        mtm = sum(p["shares"] * close_ff[code_row[c], d] for c, p in held.items())
        equity_prev = cash + mtm
        curve[d - start] = equity_prev
    for code in list(held):
        close_pos(code, end, float(close_ff[code_row[code], end]), True)
    return pd.Series(curve, index=pd.DatetimeIndex(dates[start: end + 1])), pd.DataFrame(done)
