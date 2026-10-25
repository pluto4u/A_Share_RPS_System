"""Daily descriptions and retrospective MA20 episodes, not trading rules."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict


class CaseWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: str = "2026-06-01"
    end: str = "2026-09-30"


def describe_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """Input contains traded stock bars, including pre-window history."""
    required = ["date", "open", "high", "low", "close", "vol", "RPS50", "RPS120", "RPS250"]
    if missing := set(required) - set(frame.columns):
        raise ValueError(f"missing fields: {sorted(missing)}")
    d = frame.copy().reset_index(drop=True)
    dates = pd.to_datetime(d.date, errors="raise")
    if d.empty or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("empty, duplicated or unsorted traded calendar")
    ohlcv = d[["open", "high", "low", "close", "vol"]].to_numpy(dtype=float)
    if not np.isfinite(ohlcv).all() or np.any(ohlcv <= 0):
        raise ValueError("invalid OHLCV")
    if (d.high < d[["open", "close"]].max(axis=1)).any():
        raise ValueError("high below open/close")
    if (d.low > d[["open", "close"]].min(axis=1)).any():
        raise ValueError("low above open/close")
    for period in (50, 120, 250):
        values = d[f"RPS{period}"].to_numpy(dtype=float)
        valid = np.isfinite(values)
        if np.isinf(values).any() or np.any((values[valid] < 0) | (values[valid] > 100)):
            raise ValueError(f"invalid RPS{period}")
        d[f"RPS{period}_change_1bar_pp"] = d[f"RPS{period}"].diff()
    for period in (10, 13, 20, 30, 60, 120, 250):
        d[f"MA{period}"] = d.close.rolling(period).mean()
    d["price_vs_ma20_pct"] = (d.close / d.MA20 - 1) * 100
    prior = d.close.shift(1) >= d.MA20.shift(1)
    current = d.close >= d.MA20
    complete = d.MA20.notna() & d.MA20.shift(1).notna()
    d["cross_below_ma20"] = complete & prior & ~current
    d["cross_above_ma20"] = complete & ~prior & current
    d["below_ma20"] = d.MA20.notna() & ~current
    return d


def ma20_episodes(d: pd.DataFrame, window: CaseWindow) -> pd.DataFrame:
    """Troughs are explicitly hindsight descriptions, never entry features.

    Prior reference uses 20 already known traded closes at the break, matching
    the MA20 measurement scale. No execution, optimisation or new stop rule.
    """
    records: list[dict[str, Any]] = []
    mask = d.below_ma20.to_numpy(dtype=bool)
    i = 0
    while i < len(d):
        if not mask[i]:
            i += 1
            continue
        start = i
        while i < len(d) and mask[i]:
            i += 1
        end = i - 1
        episode = d.iloc[start:i]
        recovery = d.iloc[i] if i < len(d) else None
        if str(d.iloc[end].date) < window.start or str(d.iloc[start].date) > window.end:
            continue
        prior = d.iloc[max(0, start - 20) : start]
        trough = d.loc[episode.close.idxmin()]
        reference = float(prior.close.max()) if len(prior) == 20 else np.nan
        row: dict[str, Any] = {
            "break_date": str(d.iloc[start].date),
            "last_below_date": str(d.iloc[end].date),
            "recovery_date": str(recovery.date) if recovery is not None else None,
            "unfinished": recovery is None,
            "traded_bars_below": len(episode),
            "started_before_window": str(d.iloc[start].date) < window.start,
            "prior20_peak_close": reference,
            "review_trough_date": str(trough.date),
            "review_trough_close": float(trough.close),
            "review_peak_to_trough_pct": (float(trough.close) / reference - 1) * 100,
            "volume_vs_prior20_ratio": (
                float(episode.vol.mean() / prior.vol.mean()) if len(prior) == 20 else np.nan
            ),
            "trough_uses_episode_future_bars": True,
        }
        for period in (50, 120, 250):
            key = f"RPS{period}"
            row[f"{key}_break"] = float(d.iloc[start][key])
            row[f"{key}_at_price_trough"] = float(trough[key])
            row[f"{key}_episode_min"] = float(episode[key].min())
            row[f"{key}_recovery"] = float(recovery[key]) if recovery is not None else np.nan
        records.append(row)
    return pd.DataFrame(records)
