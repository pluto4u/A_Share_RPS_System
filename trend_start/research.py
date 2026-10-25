"""Daily-only, causal research simulator. No broker or live-order interfaces.

Prices/units are adjusted research prices/units, NOT actual exchange shares.
Historical security status is missing; limit rules are declared proxies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from trend_start.panel import Panel


class StudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capital: float = Field(default=1_000_000, gt=0)
    slots: int = Field(default=10, ge=1, le=100)
    commission: float = Field(default=0.00025, ge=0, le=0.02)
    slippage: float = Field(default=0.001, ge=0, le=0.05)
    exit_ma: Literal[20, 60] = 20
    stop_loss: float = Field(default=0.07, gt=0, lt=1)
    market_filter: bool = False
    main_limit: float = Field(default=0.05, gt=0, le=0.10)
    retest_window: int = Field(default=20, ge=3, le=60)
    retest_volume: float = Field(default=0.8, gt=0, le=1)
    max_entry_gap: float = Field(default=0.03, ge=0, le=0.20)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def source_manifest(root: Path) -> dict[str, str]:
    paths = sorted((root / "vipdoc/sh/lday").glob("*.day"))
    paths += sorted((root / "vipdoc/sz/lday").glob("*.day"))
    paths.append(root / "T0002/hq_cache/gbbq")
    if len(paths) < 3:
        raise ValueError("Missing local daily files")
    return {str(p.relative_to(root)): sha256(p) for p in paths}


def validate_panel(panel: Panel) -> None:
    if not len(panel.dates) or not len(panel.codes):
        raise ValueError("Empty panel")
    if len(np.unique(panel.codes)) != len(panel.codes) or np.any(
        np.diff(panel.dates) <= np.timedelta64(0, "D")
    ):
        raise ValueError("Duplicate codes or non-increasing calendar")
    c = panel.field("close")
    for name in ("open", "high", "low", "close", "vol", "amount"):
        a = panel.field(name)
        if a.shape != (len(panel.codes), len(panel.dates)):
            raise ValueError(f"{name}: bad shape")
        if not np.array_equal(np.isnan(a), np.isnan(c)) or np.isinf(a).any():
            raise ValueError(f"{name}: inconsistent missingness or infinity")
    o, h, lo = (panel.field(f) for f in ("open", "high", "low"))
    valid = np.isfinite(c)
    if np.any(c[valid] <= 0) or np.any(lo[valid] <= 0):
        raise ValueError("Nonpositive prices")
    if np.any(h[valid] < np.maximum(o, c)[valid]) or np.any(lo[valid] > np.minimum(o, c)[valid]):
        raise ValueError("Invalid OHLC envelope")
    if not np.isfinite(panel.index_close).all():
        raise ValueError("Missing benchmark closes")


def linked_retest(d: pd.DataFrame, breakout: np.ndarray, cfg: StudyConfig) -> pd.DataFrame:
    """Freeze base/pivot at breakout; require a later quiet touch then a later reclaim.

    A new breakout cannot move an existing event's pivot or overwrite its identity.
    A touch on today's bar cannot also confirm on today's bar.
    """
    c, h, lo, v = (d[f].to_numpy(dtype=float) for f in ("close", "high", "low", "vol"))
    ma20 = d.close.rolling(20).mean().to_numpy()
    pivot = d.high.shift(1).rolling(20).max().to_numpy()
    floor = d.low.shift(1).rolling(20).min().to_numpy()
    rps = d.RPS120.to_numpy()
    rows: list[dict[str, Any]] = []
    event = -1
    touch = -1
    frozen_pivot = frozen_floor = event_volume = np.nan
    for t in range(len(c)):
        state, confirmed = "idle", False
        if event >= 0:
            age = t - event
            if c[t] < frozen_floor or c[t] < ma20[t] or c[t] < frozen_pivot * 0.97:
                state = "invalid"
            elif age > cfg.retest_window:
                state = "expired"
            else:
                state = "waiting" if touch < 0 else "touched"
                if (
                    touch >= 0
                    and t > touch
                    and c[t] > h[t - 1]
                    and c[t] > frozen_pivot
                    and rps[t] >= 87
                ):
                    state, confirmed = "confirmed", True
                elif age >= 2 and v[t] <= event_volume * cfg.retest_volume:
                    near_pivot = frozen_pivot * 0.97 <= lo[t] <= frozen_pivot * 1.02
                    near_ma = ma20[t] * 0.98 <= lo[t] <= ma20[t] * 1.02
                    if (near_pivot or near_ma) and c[t] >= frozen_pivot * 0.97:
                        touch, state = t, "touched"
        if event < 0 and breakout[t]:
            event, touch = t, -1
            frozen_pivot, frozen_floor, event_volume = pivot[t], floor[t], v[t]
            state = "breakout"
        rows.append(
            {
                "state": state,
                "confirmed": confirmed,
                "event_bar": event,
                "touch_bar": touch,
                "frozen_pivot": frozen_pivot,
                "frozen_floor": frozen_floor,
            }
        )
        if state in ("invalid", "expired", "confirmed"):
            event, touch = -1, -1
            frozen_pivot = frozen_floor = event_volume = np.nan
    return pd.DataFrame(rows, index=d.index)


def limit_proxy(code: str, date: np.datetime64, cfg: StudyConfig) -> float:
    if code.startswith(("688", "689")):
        return 0.20
    if code.startswith(("300", "301")) and date >= np.datetime64("2020-08-24"):
        return 0.20
    return cfg.main_limit


def sell_tax(date: np.datetime64) -> float:
    return 0.0005 if date >= np.datetime64("2023-08-28") else 0.001


@dataclass
class Position:
    row: int
    units: float
    cost: float
    entry_cal: int
    entry_price: float
    signal_cal: int
    pending_reason: str = ""


@dataclass
class Simulation:
    daily: pd.DataFrame
    trades: pd.DataFrame
    open_positions: pd.DataFrame
    orders: pd.DataFrame


def simulate(
    panel: Panel,
    signals: np.ndarray,
    strength: np.ndarray,
    moving_average: np.ndarray,
    cfg: StudyConfig,
    start: int,
    end: int,
) -> Simulation:
    """Close signal -> next market session open. No same-close executions.

    Entry blocked by suspension/limit/gap is cancelled; exit remains pending.
    Any down-limit opening defers exit (never assumes the later close is fillable).
    Last-day positions remain open, marked gross, with no fictional sell cost.
    """
    if (
        signals.shape != panel.field("close").shape
        or strength.shape != signals.shape
        or moving_average.shape != signals.shape
    ):
        raise ValueError("Signal/strength/MA dimensions do not match panel")
    if not 0 <= start <= end < len(panel.dates):
        raise ValueError("Invalid simulation window")
    op, close = panel.field("open"), panel.field("close")
    marked = pd.DataFrame(close.T).ffill().to_numpy().T
    market = panel.index_close > pd.Series(panel.index_close).rolling(60).mean().to_numpy()
    held: dict[int, Position] = {}
    cash, nav = cfg.capital, cfg.capital
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    last_trade_close = np.full(len(panel.codes), np.nan)
    if start:
        last_trade_close = marked[:, start - 1].astype(float).copy()

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
        for i, p in list(held.items()):
            if not p.pending_reason or t <= p.entry_cal:
                continue
            if not np.isfinite(op[i, t]):
                log(t, i, "exit", "deferred", "no_daily_bar")
                continue
            gap = op[i, t] / last_trade_close[i] - 1
            if gap <= -limit_proxy(str(panel.codes[i]), panel.dates[t], cfg) + 0.003:
                log(t, i, "exit", "deferred", "down_limit_proxy")
                continue
            price = float(op[i, t]) * (1 - cfg.slippage)
            proceeds = p.units * price * (1 - cfg.commission - sell_tax(panel.dates[t]))
            cash += proceeds
            trades.append(
                {
                    "code": str(panel.codes[i]),
                    "signal_date": str(panel.dates[p.signal_cal]),
                    "entry_date": str(panel.dates[p.entry_cal]),
                    "exit_date": str(panel.dates[t]),
                    "entry_price_adjusted": p.entry_price,
                    "exit_price_adjusted": price,
                    "cost": p.cost,
                    "proceeds": proceeds,
                    "return_pct": (proceeds / p.cost - 1) * 100,
                    "reason": p.pending_reason,
                    "holding_sessions": t - p.entry_cal,
                }
            )
            log(t, i, "exit", "filled", p.pending_reason)
            del held[i]
        if t > start and (not cfg.market_filter or market[t - 1]):
            ranked = np.flatnonzero(signals[:, t - 1])
            ranked = ranked[np.argsort(-strength[ranked, t - 1], kind="stable")]
            target = nav / cfg.slots
            for i in ranked:
                if i in held:
                    continue
                if len(held) >= cfg.slots:
                    log(t, int(i), "entry", "rejected", "slots_full")
                    continue
                if not np.isfinite(op[i, t]) or not np.isfinite(close[i, t - 1]):
                    log(t, int(i), "entry", "rejected", "no_next_session_bar")
                    continue
                gap = op[i, t] / close[i, t - 1] - 1
                if gap >= limit_proxy(str(panel.codes[i]), panel.dates[t], cfg) - 0.003:
                    log(t, int(i), "entry", "rejected", "up_limit_proxy")
                    continue
                if gap > cfg.max_entry_gap:
                    log(t, int(i), "entry", "rejected", "entry_gap")
                    continue
                alloc = min(target, cash)
                if alloc <= cfg.capital * 1e-8:
                    log(t, int(i), "entry", "rejected", "cash_empty")
                    continue
                price = float(op[i, t]) * (1 + cfg.slippage)
                held[int(i)] = Position(
                    int(i), alloc / (price * (1 + cfg.commission)), alloc, t, price, t - 1
                )
                cash -= alloc
                log(t, int(i), "entry", "filled", "prior_close_signal")
        for i, p in held.items():
            if np.isfinite(close[i, t]) and not p.pending_reason:
                if close[i, t] < p.entry_price * (1 - cfg.stop_loss):
                    p.pending_reason = "close_stop_loss"
                elif close[i, t] < moving_average[i, t]:
                    p.pending_reason = f"close_below_ma{cfg.exit_ma}"
        mtm = sum(p.units * float(marked[i, t]) for i, p in held.items())
        nav = cash + mtm
        if not np.isfinite(nav) or cash < -1e-6 or len(held) > cfg.slots:
            raise ArithmeticError("Cash/valuation/position invariant failed")
        daily.append(
            {
                "date": str(panel.dates[t]),
                "equity": nav,
                "cash": cash,
                "market_value": mtm,
                "positions": len(held),
                "exposure": mtm / nav,
            }
        )
        today = np.isfinite(close[:, t])
        last_trade_close[today] = close[today, t]
    opened = [
        {
            "code": str(panel.codes[i]),
            "entry_date": str(panel.dates[p.entry_cal]),
            "cost": p.cost,
            "market_value": p.units * float(marked[i, end]),
            "unrealized_pct": (p.units * float(marked[i, end]) / p.cost - 1) * 100,
            "pending_reason": p.pending_reason,
        }
        for i, p in held.items()
    ]
    return Simulation(
        pd.DataFrame(daily), pd.DataFrame(trades), pd.DataFrame(opened), pd.DataFrame(orders)
    )


def metrics(result: Simulation, capital: float) -> dict[str, Any]:
    nav = result.daily.equity.astype(float)
    ret = nav.pct_change().dropna()
    peak = np.maximum.accumulate(np.r_[capital, nav.to_numpy()])[1:]
    years = max(
        (pd.Timestamp(result.daily.date.iloc[-1]) - pd.Timestamp(result.daily.date.iloc[0])).days
        / 365.25,
        1 / 365.25,
    )
    trade_ret = result.trades.return_pct if not result.trades.empty else pd.Series(dtype=float)
    return {
        "total_return_pct": float((nav.iloc[-1] / capital - 1) * 100),
        "cagr_pct": float(((nav.iloc[-1] / capital) ** (1 / years) - 1) * 100),
        "max_drawdown_pct": float(np.min(nav.to_numpy() / peak - 1) * 100),
        "sharpe_rf0": float(ret.mean() / ret.std() * np.sqrt(244)) if ret.std() > 0 else None,
        "closed_trades": len(result.trades),
        "open_positions": len(result.open_positions),
        "win_rate_pct": float((trade_ret > 0).mean() * 100) if len(trade_ret) else None,
        "mean_trade_return_pct": float(trade_ret.mean()) if len(trade_ret) else None,
        "average_exposure_pct": float(result.daily.exposure.mean() * 100),
    }
