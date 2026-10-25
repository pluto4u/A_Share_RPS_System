"""Daily research lifecycle: launch trial -> proven trend -> exit pending."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from trend_start.panel import Panel
from trend_start.price_volume import LaunchConfig
from trend_start.research import Simulation, StudyConfig, limit_proxy, sell_tax


@dataclass
class LaunchPosition:
    units: float
    cost: float
    entry: int
    entry_price: float
    signal: int
    pivot: float
    pocket: float
    stage: str = "trial"
    above: int = 0
    below: int = 0
    trial_bars: int = 0
    pending: str = ""


def simulate_launch(
    panel: Panel,
    signals: np.ndarray,
    priority: np.ndarray,
    pivots: np.ndarray,
    pockets: np.ndarray,
    atrs: np.ndarray,
    ma20: np.ndarray,
    ma60: np.ndarray,
    cfg: StudyConfig,
    launch: LaunchConfig,
    mode: Literal["ma20", "ma60", "staged"],
    location_guard: bool,
    start: int,
    end: int,
) -> Simulation:
    """No intraday stops, no same-close fills, no fictional terminal sale.

    In staged mode, two closes back under frozen pivot or one close under pocket
    ends a trial. A proven trend needs proven_days successive closes above its
    pivot AND a rising MA20. Only after that is MA60 the trend-ending rule.
    A 7% close-based protective ceiling remains for every mode, not a fill guarantee.
    """
    shape = panel.field("close").shape
    if any(a.shape != shape for a in (signals, priority, pivots, pockets, atrs, ma20, ma60)):
        raise ValueError("Launch matrix shape mismatch")
    if not 0 <= start <= end < len(panel.dates):
        raise ValueError("Invalid simulation window")
    op, close = panel.field("open"), panel.field("close")
    marked = pd.DataFrame(close.T).ffill().to_numpy().T
    lag20 = np.full(shape, np.nan)
    for i in range(shape[0]):
        columns = np.flatnonzero(np.isfinite(ma20[i]))
        lag20[i, columns[5:]] = ma20[i, columns[:-5]]
    held: dict[int, LaunchPosition] = {}
    cash = nav = cfg.capital
    previous = marked[:, start - 1].copy() if start else np.full(shape[0], np.nan)
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []

    def log(t: int, i: int, side: str, status: str, reason: str) -> None:
        orders.append(
            {
                "date": str(panel.dates[t]),
                "code": str(panel.codes[i]),
                "side": side,
                "status": status,
                "reason": reason,
            }
        )

    for t in range(start, end + 1):
        exited: set[int] = set()
        for i, p in list(held.items()):
            if not p.pending or t <= p.entry:
                continue
            if not np.isfinite(op[i, t]):
                log(t, i, "exit", "deferred", "no_daily_bar")
                continue
            if (
                op[i, t] / previous[i] - 1
                <= -limit_proxy(str(panel.codes[i]), panel.dates[t], cfg) + 0.003
            ):
                log(t, i, "exit", "deferred", "down_limit_proxy")
                continue
            price = float(op[i, t]) * (1 - cfg.slippage)
            proceeds = p.units * price * (1 - cfg.commission - sell_tax(panel.dates[t]))
            cash += proceeds
            trades.append(
                {
                    "code": str(panel.codes[i]),
                    "signal_date": str(panel.dates[p.signal]),
                    "entry_date": str(panel.dates[p.entry]),
                    "exit_date": str(panel.dates[t]),
                    "entry_price_adjusted": p.entry_price,
                    "exit_price_adjusted": price,
                    "cost": p.cost,
                    "proceeds": proceeds,
                    "return_pct": (proceeds / p.cost - 1) * 100,
                    "reason": p.pending,
                    "holding_sessions": t - p.entry,
                    "exit_stage": p.stage,
                    "frozen_pivot": p.pivot,
                    "frozen_pocket": p.pocket,
                }
            )
            log(t, i, "exit", "filled", p.pending)
            del held[i]
            exited.add(i)
        if t > start:
            ranked = np.flatnonzero(signals[:, t - 1])
            if not np.isfinite(priority[ranked, t - 1]).all():
                raise ValueError("Missing signal priority")
            ranked = ranked[np.argsort(-priority[ranked, t - 1], kind="stable")]
            target = nav / cfg.slots
            for raw_i in ranked:
                i = int(raw_i)
                if i in held:
                    continue
                if i in exited:
                    log(t, i, "entry", "rejected", "same_day_reentry")
                    continue
                if len(held) >= cfg.slots:
                    log(t, i, "entry", "rejected", "slots_full")
                    continue
                if not np.isfinite(op[i, t]) or not np.isfinite(close[i, t - 1]):
                    log(t, i, "entry", "rejected", "no_next_session_bar")
                    continue
                gap = op[i, t] / close[i, t - 1] - 1
                if gap >= limit_proxy(str(panel.codes[i]), panel.dates[t], cfg) - 0.003:
                    log(t, i, "entry", "rejected", "up_limit_proxy")
                    continue
                if gap > cfg.max_entry_gap:
                    log(t, i, "entry", "rejected", "entry_gap")
                    continue
                price = float(op[i, t]) * (1 + cfg.slippage)
                pivot, pocket, atr = (float(a[i, t - 1]) for a in (pivots, pockets, atrs))
                if not np.isfinite([pivot, pocket, atr]).all():
                    raise ValueError("Missing frozen launch levels")
                if location_guard and not (
                    pivot < price <= pivot * (1 + launch.max_pivot_extension)
                    and price - pivot <= launch.max_pivot_atr * atr
                    and 0 < 1 - pocket / price <= launch.max_structure_risk
                ):
                    log(t, i, "entry", "rejected", "next_open_structure_location")
                    continue
                allocation = min(target, cash)
                if allocation <= cfg.capital * 1e-8:
                    log(t, i, "entry", "rejected", "cash_empty")
                    continue
                held[i] = LaunchPosition(
                    allocation / (price * (1 + cfg.commission)),
                    allocation,
                    t,
                    price,
                    t - 1,
                    pivot,
                    pocket,
                )
                cash -= allocation
                log(t, i, "entry", "filled", "prior_close_launch")
        for i, p in held.items():
            if p.pending or not np.isfinite(close[i, t]):
                continue
            p.above = p.above + 1 if close[i, t] >= p.pivot else 0
            p.below = p.below + 1 if close[i, t] < p.pivot else 0
            p.trial_bars += 1
            proven = p.above >= launch.proven_days and ma20[i, t] > lag20[i, t]
            # Trial failure takes precedence over phase promotion on this close.
            reason = ""
            if close[i, t] < p.entry_price * (1 - cfg.stop_loss):
                reason = "close_stop_loss"
            elif mode == "staged" and p.stage == "trial":
                if close[i, t] < p.pocket:
                    reason = "trial_pocket_failed"
                elif p.below >= 2:
                    reason = "trial_pivot_failed"
                elif proven:
                    p.stage = "trend"
                    log(t, i, "phase", "proven", "held_pivot_and_ma20_rising")
                elif p.trial_bars >= launch.retest_days:
                    reason = "trial_timeout"
            if not reason and (mode != "staged" or p.stage == "trend"):
                average = ma20[i, t] if mode == "ma20" else ma60[i, t]
                if close[i, t] < average:
                    reason = "close_below_ma20" if mode == "ma20" else "close_below_ma60"
            p.pending = reason
        value = sum(p.units * float(marked[i, t]) for i, p in held.items())
        nav = cash + value
        if not np.isfinite(nav) or cash < -1e-6 or len(held) > cfg.slots:
            raise ArithmeticError("Launch ledger invariant failed")
        daily.append(
            {
                "date": str(panel.dates[t]),
                "equity": nav,
                "cash": cash,
                "market_value": value,
                "positions": len(held),
                "exposure": value / nav,
            }
        )
        traded = np.isfinite(close[:, t])
        previous[traded] = close[traded, t]
    opened = [
        {
            "code": str(panel.codes[i]),
            "entry_date": str(panel.dates[p.entry]),
            "cost": p.cost,
            "market_value": p.units * float(marked[i, end]),
            "unrealized_pct": (p.units * float(marked[i, end]) / p.cost - 1) * 100,
            "pending_reason": p.pending,
            "stage": p.stage,
        }
        for i, p in held.items()
    ]
    return Simulation(
        pd.DataFrame(daily), pd.DataFrame(trades), pd.DataFrame(opened), pd.DataFrame(orders)
    )
