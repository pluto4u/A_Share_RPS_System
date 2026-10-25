"""Market environment classification: volume regime + ice-point detection + position gate.

User hypothesis under test (research only, no trading instructions):
  - 缩量环境 + 连续冰点后: allow pattern pullback entries (buy-the-ice), short holds.
  - 放量环境: allow momentum-chase entries (breakout / re-strengthen).
  - The environment decides WHETHER to act and the position size; individual
    stocks still come from the strict pattern rules.

Causality: day t's classification uses only data up to and including day t's
close. An entry at day t's tail/close (or the next session's open) may use
regime[t]; it never uses future data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

VOLUME_SHRINK = "缩量"
VOLUME_NEUTRAL = "中性"
VOLUME_SURGE = "放量"

GATE_ICE_BUY = "ice_buy"        # 缩量 + 近期连续冰点: 冰点买入窗口
GATE_SURGE_CHASE = "surge_chase"  # 放量: 追涨窗口
GATE_NEUTRAL = "neutral"        # 中性: 半仓
GATE_OFF = "off"                # 缩量但无冰点: 观望


@dataclass(frozen=True)
class RegimeConfig:
    vol_short: int = 5          # short window for market turnover
    vol_long: int = 60          # long window baseline for market turnover
    shrink_ratio: float = 0.8   # short/long <= 0.8 -> 缩量
    surge_ratio: float = 1.2    # short/long >= 1.2 -> 放量
    breadth_ma: int = 20        # breadth = share of stocks with close > MA(breadth_ma)
    ice_breadth: float = 0.20   # breadth <= 20% -> 冰点日
    ice_min_streak: int = 2     # 连续冰点: streak of ice days >= 2 counts as 连续冰点
    ice_window: int = 3         # 连续冰点出现后 N 个交易日内仍是冰点买入窗口
    mult_ice: float = 1.0       # position multiplier per gate
    mult_surge: float = 1.0
    mult_neutral: float = 0.5
    mult_off: float = 0.0


def market_aggregates(close: np.ndarray, amount: np.ndarray,
                      breadth_ma: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-session breadth (share above MA) and total turnover. NaN-safe."""
    c = pd.DataFrame(close.astype(np.float64))
    ma = c.T.rolling(breadth_ma, min_periods=breadth_ma).mean().T
    above = (c > ma) & np.isfinite(ma.to_numpy())
    valid = np.isfinite(c.to_numpy()) & np.isfinite(ma.to_numpy())
    n_valid = valid.sum(axis=0)
    breadth = np.where(n_valid > 0, above.sum(axis=0) / np.maximum(n_valid, 1), np.nan)
    total_amount = np.nansum(amount.astype(np.float64), axis=0)
    total_amount = np.where(np.isfinite(amount).any(axis=0), total_amount, np.nan)
    return breadth, total_amount


def volume_regime(total_amount: np.ndarray, cfg: RegimeConfig) -> tuple[np.ndarray, np.ndarray]:
    """Volume regime per session plus the short/long amount ratio (causal)."""
    s = pd.Series(total_amount, dtype=np.float64)
    short = s.rolling(cfg.vol_short, min_periods=cfg.vol_short).mean()
    long = s.rolling(cfg.vol_long, min_periods=cfg.vol_long).mean()
    ratio = (short / long).to_numpy()
    regime = np.full(len(s), VOLUME_NEUTRAL, dtype=object)
    regime[np.isfinite(ratio) & (ratio <= cfg.shrink_ratio)] = VOLUME_SHRINK
    regime[np.isfinite(ratio) & (ratio >= cfg.surge_ratio)] = VOLUME_SURGE
    regime[~np.isfinite(ratio)] = VOLUME_NEUTRAL
    return regime, ratio


def ice_streaks(breadth: np.ndarray, cfg: RegimeConfig) -> tuple[np.ndarray, np.ndarray]:
    """Ice day flags, consecutive-ice streak, and the post-ice buy window flag."""
    ice_day = np.isfinite(breadth) & (breadth <= cfg.ice_breadth)
    streak = np.zeros(len(breadth), dtype=np.int64)
    for t in range(1, len(breadth)):
        streak[t] = streak[t - 1] + 1 if ice_day[t] else 0
    if ice_day[0]:
        streak[0] = 1
    # 连续冰点出现: any day in the trailing window reached the min streak
    hit = streak >= cfg.ice_min_streak
    recent = pd.Series(hit).rolling(cfg.ice_window, min_periods=1).max().to_numpy(dtype=bool)
    return ice_day, recent


DEFAULT_REGIME = RegimeConfig()  # frozen dataclass; safe shared default


def classify(breadth: np.ndarray, total_amount: np.ndarray,
             cfg: RegimeConfig = DEFAULT_REGIME) -> pd.DataFrame:
    """Full environment frame: volume regime, ice flags, gate, position multiplier."""
    vol_reg, ratio = volume_regime(total_amount, cfg)
    ice_day, ice_recent = ice_streaks(breadth, cfg)
    gate = np.full(len(breadth), GATE_OFF, dtype=object)
    mult = np.full(len(breadth), cfg.mult_off, dtype=np.float64)
    neutral = vol_reg == VOLUME_NEUTRAL
    gate[neutral] = GATE_NEUTRAL
    mult[neutral] = cfg.mult_neutral
    surge = vol_reg == VOLUME_SURGE
    gate[surge] = GATE_SURGE_CHASE
    mult[surge] = cfg.mult_surge
    ice_buy = (vol_reg == VOLUME_SHRINK) & ice_recent
    gate[ice_buy] = GATE_ICE_BUY
    mult[ice_buy] = cfg.mult_ice
    return pd.DataFrame(
        {
            "breadth": breadth,
            "amount_ratio5_60": ratio,
            "volume_regime": vol_reg,
            "ice_day": ice_day,
            "ice_recent": ice_recent,
            "gate": gate,
            "position_mult": mult,
        }
    )


def from_panel(panel: object, cfg: RegimeConfig = DEFAULT_REGIME) -> pd.DataFrame:
    """Classify from a trend_start.panel.Panel (close/amount matrices aligned to dates)."""
    breadth, total_amount = market_aggregates(
        panel.field("close"), panel.field("amount"), cfg.breadth_ma  # type: ignore[attr-defined]
    )
    frame = classify(breadth, total_amount, cfg)
    frame.index = pd.DatetimeIndex(panel.dates)  # type: ignore[attr-defined]
    return frame
