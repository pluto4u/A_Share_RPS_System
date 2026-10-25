"""Price/volume launch events. RPS is deliberately neither read nor ranked here."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class LaunchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_bars: int = Field(default=20, ge=10, le=60)
    contraction_max: float = Field(default=0.85, gt=0, le=1.5)
    dry_max: float = Field(default=0.85, gt=0, le=1.5)
    base_depth_max: float = Field(default=0.18, gt=0, lt=1)
    prior_advance_min: float = Field(default=0.15, ge=0, lt=1)
    amount_min: float = Field(default=100_000_000, ge=0)
    breakout_volume_min: float = Field(default=1.3, gt=0)
    close_location_min: float = Field(default=0.65, ge=0, le=1)
    max_pivot_extension: float = Field(default=0.04, gt=0, lt=1)
    max_pivot_atr: float = Field(default=0.8, gt=0)
    max_structure_risk: float = Field(default=0.08, gt=0, lt=1)
    retest_days: int = Field(default=20, ge=3, le=60)
    retest_dry: float = Field(default=0.8, gt=0, le=1)
    touch_atr: float = Field(default=0.5, gt=0)
    proven_days: int = Field(default=5, ge=2, le=20)


def launch_features(d: pd.DataFrame, cfg: LaunchConfig) -> pd.DataFrame:
    """Every platform field is known BEFORE the prospective breakout bar."""
    c, h, lo, v, amount = (d[f].astype(float) for f in ("close", "high", "low", "vol", "amount"))
    if not np.isfinite(d[["open", "high", "low", "close", "vol", "amount"]]).all().all():
        raise ValueError("Missing/nonfinite launch inputs")
    if (c <= 0).any() or (v < 0).any() or (amount < 0).any():
        raise ValueError("Invalid launch inputs")
    half = cfg.base_bars // 2
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.shift(1).rolling(20).mean()
    pivot = h.shift(1).rolling(cfg.base_bars).max()
    floor = lo.shift(1).rolling(cfg.base_bars).min()
    pocket = lo.shift(1).rolling(5).min()
    contraction = tr.shift(1).rolling(half).mean() / tr.shift(half + 1).rolling(half).mean()
    dry = v.shift(1).rolling(half).mean() / v.shift(half + 1).rolling(half).mean()
    early_low = lo.shift(half + 1).rolling(half).min()
    late_low = lo.shift(1).rolling(half).min()
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    prior = c.shift(cfg.base_bars + 1)
    advance = prior / prior.rolling(40).min() - 1
    width = pivot / floor - 1
    background = (c.shift(1) > ma60.shift(1)) & (ma60.shift(1) >= ma60.shift(6))
    liquidity = amount.shift(1).rolling(20).mean() >= cfg.amount_min
    platform = (
        background
        & liquidity
        & (advance >= cfg.prior_advance_min)
        & (width <= cfg.base_depth_max)
        & (contraction <= cfg.contraction_max)
        & (dry <= cfg.dry_max)
        & (late_low >= early_low * 0.98)
        & (c.shift(1) >= pivot * 0.94)
        & (c.shift(1) <= pivot)
        & (np.arange(len(d)) >= 250)
    )
    vr = v / v.shift(1).rolling(20).mean()
    clv = (c - lo) / (h - lo).replace(0, np.nan)
    raw_breakout = (
        platform & (c > pivot) & (vr >= cfg.breakout_volume_min) & (clv >= cfg.close_location_min)
    )
    extension = c / pivot - 1
    risk = 1 - pocket / c
    location = (extension <= cfg.max_pivot_extension) & ((c - pivot) / atr <= cfg.max_pivot_atr)
    location &= (risk > 0) & (risk <= cfg.max_structure_risk)
    # Fixed explanatory score, not fitted to past winners; no RPS input.
    score = (
        30 * (1 - contraction).clip(0, 1)
        + 20 * (1 - dry).clip(0, 1)
        + 20 * (late_low / early_low - 0.98).clip(0, 0.1) / 0.1
        + 30 * (1 - risk / cfg.max_structure_risk).clip(0, 1)
    )
    return pd.DataFrame(
        {
            "platform": platform.fillna(False),
            "raw_breakout": raw_breakout.fillna(False),
            "location_ok": location.fillna(False),
            "pivot": pivot,
            "base_floor": floor,
            "pocket": pocket,
            "atr": atr,
            "compression": contraction,
            "dry": dry,
            "depth": width,
            "advance": advance,
            "volume_ratio": vr,
            "close_location": clv,
            "extension": extension,
            "structure_risk": risk,
            "quality": score,
            "ma20": ma20,
            "ma60": ma60,
        },
        index=d.index,
    )


def launch_events(d: pd.DataFrame, f: pd.DataFrame, cfg: LaunchConfig) -> pd.DataFrame:
    """Deduplicated events, with a later quiet touch and a still later confirmation.

    Freeze the same pivot/pocket for all stages. No MA20 failure during the
    waiting phase: structural failure and eventual trend exit are distinct.
    """
    c, h, lo, v = (d[k].to_numpy(dtype=float) for k in ("close", "high", "low", "vol"))
    features = {k: f[k].to_numpy() for k in f.columns}
    rows: list[dict[str, Any]] = []
    event = touch = -1
    pivot = pocket = atr = event_volume = base_volume = np.nan
    accepted = False
    below = 0
    for t in range(len(d)):
        state, raw, direct, retest = "idle", False, False, False
        if event >= 0:
            age = t - event
            below = below + 1 if c[t] < pivot else 0
            if c[t] < pocket or below >= 2:
                state = "invalid"
            elif age > cfg.retest_days:
                state = "expired"
            else:
                state = "waiting" if touch < 0 else "touched"
                risk = 1 - pocket / c[t]
                location = (
                    0 < risk <= cfg.max_structure_risk
                    and 0 < c[t] - pivot <= cfg.max_pivot_atr * atr
                    and c[t] / pivot - 1 <= cfg.max_pivot_extension
                )
                if (
                    accepted
                    and touch >= 0
                    and t > touch
                    and c[t] > h[t - 1]
                    and c[t] > pivot
                    and v[t] >= v[t - 1]
                    and location
                ):
                    state, retest = "confirmed", True
                elif (
                    age >= 2
                    and accepted
                    and lo[t] <= pivot + cfg.touch_atr * atr
                    and lo[t] >= pocket
                    and c[t] >= pivot - cfg.touch_atr * atr
                    and v[t] <= min(event_volume * cfg.retest_dry, base_volume)
                ):
                    touch, state = t, "touched"
        if event < 0 and features["raw_breakout"][t]:
            event, touch, below = t, -1, 0
            pivot, pocket, atr = (float(features[k][t]) for k in ("pivot", "pocket", "atr"))
            event_volume = v[t]
            base_volume = float(v[max(0, t - 20) : t].mean())
            accepted = bool(features["location_ok"][t])
            state, raw, direct = "breakout", True, accepted
        risk_now = 1 - pocket / c[t] if event >= 0 else np.nan
        quality = float(features["quality"][event]) if event >= 0 else np.nan
        rows.append(
            {
                "state": state,
                "event_bar": event,
                "touch_bar": touch,
                "raw": raw,
                "direct": direct,
                "retest": retest,
                "frozen_pivot": pivot,
                "frozen_pocket": pocket,
                "structure_risk": risk_now,
                "quality": quality,
            }
        )
        # Keep event identity even after confirmation until failure/expiry;
        # retest is a one-shot event, not a repeated daily entry condition.
        if state == "confirmed":
            accepted = False
        if state in ("invalid", "expired"):
            event = touch = -1
            pivot = pocket = atr = event_volume = base_volume = np.nan
    return pd.DataFrame(rows, index=d.index)
