"""Causal event chain: completed fall -> quiet base -> volume break -> retest.

No MA or RPS admission gates; quiet and loud retests are separate observations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PatternConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    drop_lookback: int = Field(default=80, ge=30, le=250)
    drop_min: float = Field(default=0.25, gt=0, lt=1)
    trough_gap_max: int = Field(default=40, ge=1, le=100)
    base_min: int = Field(default=10, ge=10)
    base_max: int = Field(default=30, le=60)
    base_width_max: float = Field(default=0.18, gt=0, lt=1)
    base_drift_max: float = Field(default=0.08, gt=0, lt=1)
    base_dry_max: float = Field(default=0.75, gt=0, le=1)
    amount_min: float = Field(default=100_000_000, ge=0)
    breakout_volume_min: float = Field(default=1.3, gt=1)
    breakout_clv_min: float = Field(default=0.6, ge=0, le=1)
    event_days: int = Field(default=15, ge=3, le=60)
    touch_above: float = Field(default=0.03, gt=0, lt=1)
    close_below: float = Field(default=0.02, ge=0, lt=1)
    extension_max: float = Field(default=0.08, gt=0, lt=1)
    quiet_vs_break: float = Field(default=0.85, gt=0, le=1)
    loud_vs_prior5: float = Field(default=1.2, gt=1)
    review_fresh_days: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def windows(self) -> PatternConfig:
        if self.base_max < self.base_min:
            raise ValueError("base_max must be >= base_min")
        return self


@dataclass(frozen=True)
class Base:
    peak: int
    trough: int
    start: int
    end: int
    pivot: float
    floor: float
    quiet_volume: float
    drop_pct: float
    width_pct: float
    dry_ratio: float


def find_base(a: dict[str, np.ndarray], t: int, cfg: PatternConfig) -> Base | None:
    """Choose longest passing base ending YESTERDAY, never fit a future segment."""
    c, h, lo, v, amount = (a[k] for k in ("close", "high", "low", "vol", "amount"))
    for n in range(cfg.base_max, cfg.base_min - 1, -1):
        start = t - n
        left = max(0, start - cfg.drop_lookback)
        if start - left < 20:
            continue
        old = c[left:start]
        running_high = np.maximum.accumulate(old)
        drawdown = 1 - old / running_high
        trough = left + int(np.argmax(drawdown))
        peak = left + int(np.argmax(c[left : trough + 1]))
        drop = float(drawdown[trough - left])
        if drop < cfg.drop_min or peak >= trough or start - trough > cfg.trough_gap_max:
            continue
        pivot, floor = float(h[start:t].max()), float(lo[start:t].min())
        width = pivot / floor - 1
        drift = abs(c[t - 1] / c[start] - 1)
        dry = float(v[t - 5 : t].mean() / v[start : start + 5].mean())
        if (
            width > cfg.base_width_max
            or drift > cfg.base_drift_max
            or dry > cfg.base_dry_max
            or floor < lo[trough] * 0.98
            or amount[start:t].mean() < cfg.amount_min
        ):
            continue
        return Base(
            peak,
            trough,
            start,
            t - 1,
            pivot,
            floor,
            float(v[t - 5 : t].mean()),
            drop * 100,
            width * 100,
            dry,
        )
    return None


def pattern_events(d: pd.DataFrame, cfg: PatternConfig, scan_start: int = 0) -> pd.DataFrame:
    needed = ("open", "high", "low", "close", "vol", "amount")
    a = {k: d[k].to_numpy(dtype=float) for k in needed}
    if not np.isfinite(d[list(needed)].to_numpy(dtype=float)).all():
        raise ValueError("Missing/nonfinite pattern input")
    if (d[["open", "high", "low", "close"]] <= 0).any().any() or (a["vol"] <= 0).any():
        raise ValueError("Nonpositive price/traded volume")
    if (a["amount"] < 0).any() or (d.high < d[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("Invalid amount/high")
    if (d.low > d[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("Invalid low")
    c, h, lo, v = (a[k] for k in ("close", "high", "low", "vol"))
    base: Base | None = None
    event = touch = last_review = -1
    break_volume = np.nan
    below = 0
    review_stage = ""
    touch_kind = review_kind = ""
    records: list[dict[str, Any]] = []
    for t in range(max(0, scan_start), len(d)):
        stage, attempt, broken = "未触发完整模式", False, False
        quiet_touch = loud_touch = confirmed = False
        ratio = float(v[t] / v[t - 5 : t].mean()) if t >= 5 else np.nan
        if t >= scan_start and base is not None:
            below = below + 1 if c[t] < base.pivot * (1 - cfg.close_below) else 0
            if c[t] < base.floor or below >= 2:
                stage = "形态失效"
            elif t - event > cfg.event_days:
                stage = "事件过期"
            else:
                stage = "放量突破后待回踩"
                near = (
                    lo[t] <= base.pivot * (1 + cfg.touch_above)
                    and lo[t] >= base.floor
                    and base.pivot * (1 - cfg.close_below)
                    <= c[t]
                    <= base.pivot * (1 + cfg.extension_max)
                )
                if (
                    touch >= 0
                    and t > touch
                    and c[t] > h[t - 1]
                    and c[t] > base.pivot
                    and v[t] >= v[t - 1]
                ):
                    confirmed = True
                    last_review, review_stage = t, "回踩后再转强"
                    review_kind = touch_kind
                    touch = -1
                elif near and t > event:
                    quiet_touch = v[t] <= break_volume * cfg.quiet_vs_break and ratio <= 1
                    clv = (c[t] - lo[t]) / (h[t] - lo[t]) if h[t] > lo[t] else 0
                    loud_touch = ratio >= cfg.loud_vs_prior5 and c[t] >= base.pivot and clv >= 0.5
                    if quiet_touch or loud_touch:
                        touch = t
                        last_review = t
                        review_stage = "缩量回踩待确认" if quiet_touch else "放量回踩守位待确认"
                        touch_kind = review_kind = "quiet" if quiet_touch else "loud"
                if last_review >= 0 and t - last_review <= cfg.review_fresh_days:
                    stage = review_stage
        if t >= max(scan_start, cfg.base_min + 20, 10) and base is None:
            # Necessary price prefilter for any base of >=10 bars, not an extra veto.
            attempt = bool(c[t] > h[t - 10 : t].max())
            if attempt:
                found = find_base(a, t, cfg)
                clv = (c[t] - lo[t]) / (h[t] - lo[t]) if h[t] > lo[t] else 0
                if (
                    found is not None
                    and c[t] > found.pivot
                    and v[t] >= found.quiet_volume * cfg.breakout_volume_min
                    and clv >= cfg.breakout_clv_min
                ):
                    base, event, below = found, t, 0
                    touch = last_review = -1
                    review_stage = ""
                    touch_kind = review_kind = ""
                    break_volume = v[t]
                    stage, broken = "放量突破后待回踩", True
        eligible = (
            base is not None
            and last_review >= 0
            and t - last_review <= cfg.review_fresh_days
            and stage not in ("形态失效", "事件过期")
            and base.pivot * (1 - cfg.close_below) <= c[t] <= base.pivot * (1 + cfg.extension_max)
        )
        record: dict[str, Any] = {
            "stage": stage,
            "review": bool(eligible),
            "attempt": attempt,
            "breakout": broken,
            "quiet_touch": bool(quiet_touch),
            "loud_touch": bool(loud_touch),
            "confirmed": bool(confirmed),
            "event_bar": event,
            "touch_bar": touch,
            "last_review_bar": last_review,
            "volume_ratio5": ratio,
            "review_kind": review_kind,
            "break_volume": break_volume,
        }
        record.update(asdict(base) if base is not None else {k: None for k in Base.__annotations__})
        records.append(record)
        if stage in ("形态失效", "事件过期"):
            base = None
            event = touch = last_review = -1
            break_volume = np.nan
    return pd.DataFrame(records, index=d.index[max(0, scan_start) :])


def pattern_entry_events(d: pd.DataFrame, cfg: PatternConfig,
                         scan_start: int = 0) -> list[tuple[int, str, float, float]]:
    """Slim causal scan: (bar, kind, pivot, floor) entry tuples, no per-bar records.

    Mirrors pattern_events branch-for-branch (same state machine, same order);
    tests assert flag equivalence against pattern_events. Used by the full-history
    backtest where building a DataFrame per stock per bar is the bottleneck.
    """
    needed = ("open", "high", "low", "close", "vol", "amount")
    a = {k: d[k].to_numpy(dtype=float) for k in needed}
    if not np.isfinite(d[list(needed)].to_numpy(dtype=float)).all():
        raise ValueError("Missing/nonfinite pattern input")
    if (d[["open", "high", "low", "close"]] <= 0).any().any() or (a["vol"] <= 0).any():
        raise ValueError("Nonpositive price/traded volume")
    if (a["amount"] < 0).any() or (d.high < d[["open", "low", "close"]].max(axis=1)).any():
        raise ValueError("Invalid amount/high")
    if (d.low > d[["open", "high", "close"]].min(axis=1)).any():
        raise ValueError("Invalid low")
    c, h, lo, v = (a[k] for k in ("close", "high", "low", "vol"))
    base: Base | None = None
    event = touch = -1
    break_volume = np.nan
    below = 0
    out: list[tuple[int, str, float, float]] = []
    start_bar = max(0, scan_start)
    for t in range(start_bar, len(d)):
        reset = False
        if base is not None:
            below = below + 1 if c[t] < base.pivot * (1 - cfg.close_below) else 0
            if c[t] < base.floor or below >= 2:
                reset = True  # 形态失效
            elif t - event > cfg.event_days:
                reset = True  # 事件过期
            else:
                if (
                    touch >= 0
                    and t > touch
                    and c[t] > h[t - 1]
                    and c[t] > base.pivot
                    and v[t] >= v[t - 1]
                ):
                    out.append((t, "confirmed", base.pivot, base.floor))
                    touch = -1
                else:
                    near = (
                        lo[t] <= base.pivot * (1 + cfg.touch_above)
                        and lo[t] >= base.floor
                        and base.pivot * (1 - cfg.close_below)
                        <= c[t]
                        <= base.pivot * (1 + cfg.extension_max)
                    )
                    if near and t > event:
                        ratio = float(v[t] / v[t - 5 : t].mean()) if t >= 5 else np.nan
                        quiet = v[t] <= break_volume * cfg.quiet_vs_break and ratio <= 1
                        clv = (c[t] - lo[t]) / (h[t] - lo[t]) if h[t] > lo[t] else 0
                        loud = ratio >= cfg.loud_vs_prior5 and c[t] >= base.pivot and clv >= 0.5
                        if quiet or loud:
                            touch = t
                            out.append((t, "quiet" if quiet else "loud",
                                        base.pivot, base.floor))
        # as in pattern_events: the reset takes effect only AFTER this bar's
        # breakout check, so a broken base cannot re-break on the same bar
        if t >= max(scan_start, cfg.base_min + 20, 10) and base is None:
            attempt = bool(c[t] > h[t - 10 : t].max())
            if attempt:
                found = find_base(a, t, cfg)
                clv = (c[t] - lo[t]) / (h[t] - lo[t]) if h[t] > lo[t] else 0
                if (
                    found is not None
                    and c[t] > found.pivot
                    and v[t] >= found.quiet_volume * cfg.breakout_volume_min
                    and clv >= cfg.breakout_clv_min
                ):
                    base, event, below = found, t, 0
                    touch = -1
                    break_volume = v[t]
                    out.append((t, "breakout", base.pivot, base.floor))
        if reset:
            base = None
            event = touch = -1
            break_volume = np.nan
    return out
