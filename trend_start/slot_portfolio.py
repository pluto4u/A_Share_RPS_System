"""Slot-aware cash-compounding portfolio simulator (research only, pure).

Unlike trend_start.pattern_backtest.portfolio_from_trades (daily mark-to-market
equity curve from price panels), this module compounds realized cash flows only:
a position locks one slot from entry day to entry+hold (trading days), open
positions are marked at cost for sizing. Suited when only per-trade
(ret, hold) is known, e.g. exit-variant grids.

No buy/sell instructions; statistics only.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class SlotResult:
    total_ret: float          # final equity / 1 - 1
    filled: int               # trades actually entered
    skipped_locked: int       # candidate trades dropped: no free slot / no cash
    open_at_end: int          # positions still open at the last calendar day
    final_equity: float
    equity_by_day: dict[int, float] = field(default_factory=dict)


def slot_compound(trades: pd.DataFrame, slots: int = 20,
                  cal_end: int | None = None) -> SlotResult:
    """Compound trades with cash accounting over calendar-index days.

    Required columns: sig_cal (int calendar index of the entry day), code,
    ret (net realized return incl. costs), hold (trading days in market).
    Optional column: prio (lower fills first) — entry priority within a day
    is ascending (prio, code); deterministic.
    Entry at sig day close; the slot frees at sig_cal + hold. Position size =
    (cash + locked cost) / slots, capped by available cash.
    """
    need = {"sig_cal", "code", "ret", "hold"}
    missing = need - set(trades.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if trades.empty:
        return SlotResult(0.0, 0, 0, 0, 1.0, {})

    sort_cols = ["sig_cal", "code"]
    if "prio" in trades.columns:
        sort_cols = ["sig_cal", "prio", "code"]
    by_day = {int(d): g.sort_values(sort_cols[1:])
              for d, g in trades.sort_values(sort_cols).groupby("sig_cal")}
    last_entry = int(trades.sig_cal.max())
    last = last_entry if cal_end is None else max(cal_end, last_entry)
    drain = last + int(trades.hold.max()) + 1  # keep draining exits after cal_end
    cash, locked = 1.0, 0.0
    open_pos: list[tuple[int, float, float]] = []  # (exit_cal, cost, ret)
    filled = skipped = 0
    eq_by_day: dict[int, float] = {}

    for d in range(int(trades.sig_cal.min()), drain + 1):
        while open_pos and open_pos[0][0] <= d:
            _, cost, ret = heapq.heappop(open_pos)
            cash += cost * (1 + ret)
            locked -= cost
        if d > last:
            continue  # drain-only zone: no new entries
        g = by_day.get(d)
        if g is not None and len(open_pos) < slots and cash > 0:
            equity = cash + locked
            free = slots - len(open_pos)
            room = min(len(g), free)
            take = min(room, int(cash / (equity / slots)) if equity > 0 else 0)
            for r in g.head(take).itertuples(index=False):
                cost = min(equity / slots, cash)
                cash -= cost
                locked += cost
                heapq.heappush(open_pos, (d + int(r.hold), cost, float(r.ret)))
                filled += 1
            skipped += max(0, min(len(g), slots) - take)
        elif g is not None:
            skipped += min(len(g), slots)
        eq_by_day[d] = cash + locked

    final = cash + sum(cost * (1 + ret) for _, cost, ret in open_pos)
    return SlotResult(final - 1.0, filled, skipped, len(open_pos), final,
                      eq_by_day)
