"""Causal trend membership; price/volume descriptions never veto membership."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class TrendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    minimum_bars: int = Field(default=251, ge=65)
    slope_bars: int = Field(default=5, ge=1, le=20)
    breakout_bars: int = Field(default=10, ge=5, le=60)


def trend_state(d: pd.DataFrame, cfg: TrendConfig) -> pd.DataFrame:
    """Own-stock traded bars; all reference ranges/volume bases exclude today.

    Labels are descriptions, not executable instructions or predictive scores.
    'Pullback' means a quieter down-close inside the trend, not a proven pivot retest.
    """
    needed = ["open", "high", "low", "close", "vol", "amount"]
    if not np.isfinite(d[needed].to_numpy(dtype=float)).all():
        raise ValueError("Missing/nonfinite trend input")
    c, h, lo, v = (d[k].astype(float) for k in ("close", "high", "low", "vol"))
    if (d[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Nonpositive prices")
    if (h < d[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Invalid high")
    if (lo > d[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid low")
    if (v <= 0).any() or (d.amount < 0).any():
        raise ValueError("Expected traded bars with positive volume")
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    past20 = ma20.shift(cfg.slope_bars)
    eligible = np.arange(len(d)) >= cfg.minimum_bars - 1
    up = (c > ma20) & (ma20 > past20) & eligible
    reference_volume = v.shift(1).rolling(5).mean()
    volume_ratio = v / reference_volume
    prior_high = h.shift(1).rolling(cfg.breakout_bars).max()
    warning = (c < lo.shift(1).rolling(5).min()) & (v > reference_volume)
    breakout = c > prior_high
    pullback = (c < c.shift(1)) & (v < reference_volume)
    width = h.rolling(5).max() / lo.rolling(5).min() - 1
    quiet = (v.rolling(5).mean() < v.shift(5).rolling(5).mean()) & (width < width.shift(5))
    stage = pd.Series("趋势延续", index=d.index)
    stage.loc[quiet] = "缩量整理"
    stage.loc[pullback] = "回踩观察"
    stage.loc[breakout] = "突破扩展"
    stage.loc[warning] = "转弱警示"
    stage.loc[~up] = "趋势条件不满足"
    return pd.DataFrame(
        {
            "trend": up,
            "aligned": up & (ma20 > ma60),
            "stage": stage,
            "ma20": ma20,
            "past_ma20": past20,
            "ma60": ma60,
            "bias20_pct": (c / ma20 - 1) * 100,
            "volume_ratio5": volume_ratio,
            "volume_change5": v.rolling(5).mean() / v.shift(5).rolling(5).mean(),
            "reference_high": prior_high,
            "breakout_flag": breakout & up,
            "quiet_flag": quiet & up,
            "pullback_flag": pullback & up,
            "warning_flag": warning & up,
        },
        index=d.index,
    )
