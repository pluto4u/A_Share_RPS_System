"""User SXHCG reference and separately versioned right-side confirmation rules.

No orders, prices to act on, or evidence DB writes. Unknown turnover stays unknown.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


class ConfirmationConfig(BaseModel):
    version: str = "right_confirmation_v1"
    rps_sum: float = Field(default=180, ge=0, le=200)
    rps120_min: float = Field(default=90, ge=0, le=100)
    rps250_min: float = Field(default=85, ge=0, le=100)
    near_high: float = Field(default=0.90, gt=0, le=1)
    max_bias20: float = Field(default=0.12, gt=0)
    platform_width: float = Field(default=0.15, gt=0)
    dry_ratio: float = Field(default=0.90, gt=0)
    breakout_volume_min: float = Field(default=1.30, gt=0)
    breakout_volume_max: float = Field(default=3.0, gt=0)
    close_location_min: float = Field(default=0.65, ge=0, le=1)


def tri_and(values: list[bool | None]) -> bool | None:
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def drawdown_series(high: Any, low: Any) -> tuple[Any, Any, Any]:
    """HHVBARS latest-tie convention; dynamic LLVBARS excludes the high bar.

    COUNT(X,0) is cumulative from the first valid value, per TDX documentation.
    """
    h, lo = np.asarray(high, float), np.asarray(low, float)
    windows = np.lib.stride_tricks.sliding_window_view(
        np.pad(h, (19, 0), constant_values=-np.inf), 20
    )
    age = np.argmax(windows[:, ::-1], axis=1)
    peak = h[np.arange(len(h)) - age]
    trough = lo.copy()
    for n in range(1, 20):
        rolling = pd.Series(lo).rolling(n, min_periods=1).min().to_numpy()
        trough[age == n] = rolling[age == n]
    dd = (peak - trough) / peak
    cumulative = np.r_[0, np.cumsum(dd > 0.25)]
    end = np.arange(len(dd)) + 1
    start = np.where(age == 0, 0, end - age)
    count = cumulative[end] - cumulative[start]
    return age, dd, count


def evaluate(
    daily: Any,
    rps120: float,
    rps250: float,
    turnover_pct: float | None,
    config: ConfirmationConfig | None = None,
) -> dict[str, Any]:
    cfg = config or ConfirmationConfig()
    if len(daily) < 280:
        raise ValueError("need 280 dated bars for complete MA250/30-day conditions")
    c, h, lo, v, amount = [
        daily[x].reset_index(drop=True) for x in ("close", "high", "low", "vol", "amount")
    ]
    ma = {n: c.rolling(n).mean() for n in (5, 10, 20, 60, 200, 250)}
    age, dd, count = drawdown_series(h, lo)
    c2 = (
        (c.iloc[-1] > ma[20].iloc[-1])
        and ((c > ma[250]).tail(30).sum() >= 20)
        and ((c > ma[200]).tail(30).sum() >= 20)
        and (
            ((c > ma[20]).tail(10).sum() >= 7)
            or (((c > ma[10]).tail(4).sum() >= 3) and ((c > ma[20]).tail(4).sum() >= 3))
        )
    )
    strict = (ma[20].diff() >= 0).tail(5).all() and (ma[10] >= ma[20]).tail(5).all()
    loose = (
        ma[10].diff().iloc[-1] >= 0
        and ma[20].diff().iloc[-1] >= 0
        and ma[10].iloc[-1] >= ma[20].iloc[-1]
    )
    turnover = None if turnover_pct is None else bool(turnover_pct < 10)
    cond: dict[str, bool | None] = {
        "SXHCG1": bool(rps120 + rps250 > cfg.rps_sum),
        "SXHCG2": bool(c2),
        "SXHCG3": bool(dd[-1] <= 0.25 and count[-1] == 0 and c.iloc[-1] / c.tail(120).max() > 0.8),
        "SXHCG4": bool(strict or loose),
        "SXHCG5": turnover,
        "SXHCG6": bool((c > ma[5]).tail(10).sum() >= 6),
        "SXHCG7": bool((amount.tail(10) > 1e8).sum() >= 7),
        "SXHCG8": bool((c < ma[5]).tail(3).sum() <= 1),
    }
    corrected_c3 = bool(
        dd[-1] <= 0.25 and (age[-1] == 0 or count[-1] == 0) and c.iloc[-1] / c.tail(120).max() > 0.8
    )
    corrected = {**cond, "SXHCG3": corrected_c3}
    base = tri_and(list(corrected.values()))
    # All breakout references exclude the signal bar.
    prev_high20 = h.shift(1).rolling(20).max()
    prev_vol20 = v.shift(1).rolling(20).mean()
    vr = v / prev_vol20
    clv = (c - lo) / (h - lo).replace(0, np.nan)
    bias = c / ma[20] - 1
    trend = (
        (c > ma[20]) & (ma[20] > ma[60]) & (ma[20] > ma[20].shift(5)) & (ma[60] > ma[60].shift(5))
    )
    width = h.shift(1).rolling(10).max() / lo.shift(1).rolling(10).min() - 1
    dry = v.shift(1).rolling(5).mean() / prev_vol20
    breakout = (
        (c > prev_high20)
        & vr.between(cfg.breakout_volume_min, cfg.breakout_volume_max)
        & (clv >= cfg.close_location_min)
        & (width <= cfg.platform_width)
        & (dry <= cfg.dry_ratio)
        & trend
        & (bias <= cfg.max_bias20)
    ).fillna(False)
    recent = np.flatnonzero(breakout.to_numpy()[:-1])
    break_age = len(c) - 1 - int(recent[-1]) if len(recent) else None
    retest = bool(
        break_age is not None
        and 2 <= break_age <= 10
        and lo.iloc[-1] <= ma[20].iloc[-1] * 1.03
        and c.iloc[-1] >= ma[20].iloc[-1]
        and c.iloc[-1] > h.shift(1).rolling(5).max().iloc[-1]
        and dry.iloc[-1] <= cfg.dry_ratio
        and vr.iloc[-1] >= 1.2
        and clv.iloc[-1] >= 0.6
    )
    strong = rps120 >= cfg.rps120_min and rps250 >= cfg.rps250_min
    near = float(c.iloc[-1] / c.tail(120).max())
    if base is None:
        stage = "数据待核验"
    elif not base or not strong or not trend.iloc[-1] or near < cfg.near_high:
        stage = "未满足确认基础"
    elif bias.iloc[-1] > cfg.max_bias20:
        stage = "偏离过大观察"
    elif breakout.iloc[-1]:
        stage = "放量突破确认"
    elif retest:
        stage = "回踩再确认"
    elif width.iloc[-1] <= cfg.platform_width and dry.iloc[-1] <= cfg.dry_ratio:
        stage = "缩量整理观察"
    else:
        stage = "趋势观察"
    return {
        **cond,
        "baseline": tri_and(list(cond.values())),
        "corrected_base": base,
        "stage": stage,
        "confirmed": stage in ("放量突破确认", "回踩再确认"),
        "failed_conditions": ",".join(k for k, x in cond.items() if x is False),
        "missing_conditions": ",".join(k for k, x in cond.items() if x is None),
        "high_age20": int(age[-1]),
        "drawdown_pct": float(dd[-1] * 100),
        "rps120": rps120,
        "rps250": rps250,
        "turnover_pct": turnover_pct,
        "close": float(c.iloc[-1]),
        "ma20_reference": float(ma[20].iloc[-1]),
        "prior_high20": float(prev_high20.iloc[-1]),
        "prior_low10": float(lo.shift(1).rolling(10).min().iloc[-1]),
        "bias20_pct": float(bias.iloc[-1] * 100),
        "volume_ratio": float(vr.iloc[-1]),
        "platform_width_pct": float(width.iloc[-1] * 100),
        "dry_ratio": float(dry.iloc[-1]),
        "clv": None if pd.isna(clv.iloc[-1]) else float(clv.iloc[-1]),
        "near_high120": near,
        "volacc_defined_not_used": bool(v.tail(5).mean() > v.tail(60).mean() * 1.8),
        "recent_breakout_age": break_age,
    }
