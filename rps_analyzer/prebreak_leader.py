"""0721参考线附近的潜在突破龙头筛选。

本模块只识别“技术龙胚”：价格靠近参考线、趋势正在修复、相对强度领先且量能没有
失控。它不能代替行业地位、业绩和催化剂核验。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .fixed_window_drawdown import (
    FixedWindowDrawdownConfig,
    _normalise_codes,
    _one_stock_metrics,
    _prepare_and_audit,
)


@dataclass(frozen=True)
class PrebreakLeaderConfig:
    reference_date: str = "2026-07-21"
    target_date: str = "2026-08-06"
    min_drawdown: float = 0.25
    max_drawdown: float = 0.55
    min_gap_to_reference: float = -0.08
    max_gap_to_reference: float = 0.03
    min_amount20: float = 1e8
    min_vs_ma20: float = -0.01
    max_vs_ma20: float = 0.06
    min_ma5_vs_ma20: float = -0.04
    min_ma20_slope5: float = -0.03
    min_ma5_slope3: float = 0.0
    min_ret5: float = -0.05
    max_ret5: float = 0.15
    max_ret1: float = 0.07
    min_vol_ratio20: float = 0.35
    max_vol_ratio20: float = 1.80
    max_range5: float = 0.16
    min_rps20: float = 65.0
    min_bars: int = 125


def _band_score(value: float, bands: list[tuple[float, float]]) -> float:
    """按从高到低的阈值返回分数。"""
    for threshold, score in bands:
        if value >= threshold:
            return score
    return 0.0


def _score(row: pd.Series) -> float:
    gap = float(row["gap_to_reference_pct"])
    if -3.0 <= gap < 0.0:
        proximity = 15.0
    elif -5.0 <= gap < -3.0:
        proximity = 12.0
    elif 0.0 <= gap <= 1.5:
        proximity = 12.0
    elif -8.0 <= gap < -5.0:
        proximity = 8.0
    else:
        proximity = 6.0

    trend = 0.0
    trend += 5.0 if row["close_vs_ma20_pct"] >= 0 else 2.0
    trend += 5.0 if row["ma5_above_ma20_pct"] >= 0 else (
        3.0 if row["ma5_above_ma20_pct"] >= -2.0 else 1.0
    )
    trend += _band_score(float(row["ma20_slope5_pct"]), [(0.0, 5.0), (-1.0, 4.0), (-2.0, 2.0)])
    trend += 5.0 if row["ma5_slope3_pct"] >= 2.0 else 3.0

    strength = 0.0
    strength += _band_score(float(row["rps5"]), [(90.0, 7.0), (80.0, 6.0), (70.0, 4.0)])
    strength += _band_score(float(row["rps20"]), [(90.0, 10.0), (80.0, 8.0), (70.0, 6.0), (65.0, 4.0)])
    strength += _band_score(float(row["rps50"]), [(85.0, 8.0), (70.0, 6.0), (55.0, 4.0), (40.0, 2.0)])

    accumulation = 0.0
    accumulation += 5.0 if row["vol_contraction3"] <= 0.90 else (
        3.0 if row["vol_contraction3"] <= 1.10 else 1.0
    )
    accumulation += 5.0 if row["up_down_vol_ratio10"] >= 1.30 else (
        3.0 if row["up_down_vol_ratio10"] >= 1.0 else 1.0
    )
    accumulation += 5.0 if row["close_location5"] >= 0.75 else (
        3.0 if row["close_location5"] >= 0.60 else 1.0
    )

    drawdown = float(row["july_max_drawdown_pct"])
    risk = 6.0 if 30.0 <= drawdown <= 45.0 else 3.0
    ret5 = float(row["ret5_pct"])
    risk += 4.0 if 0.0 <= ret5 <= 12.0 else 2.0
    risk += 3.0 if row["range5_pct"] <= 10.0 else 1.5
    risk += 2.0 if row["atr14_pct"] <= 5.0 else (1.0 if row["atr14_pct"] <= 7.0 else 0.0)

    setup = {"回踩再突破": 5.0, "临界待突破": 4.0, "线边突破确认": 3.0}[row["setup_type"]]
    setup += 3.0 if row["close_location5"] >= 0.80 else 2.0
    setup += 2.0 if row["ret1_pct"] <= 4.0 else 1.0
    return round(float(np.clip(proximity + trend + strength + accumulation + risk + setup, 0, 100)), 1)


def screen_prebreak_leaders(
    df: pd.DataFrame,
    config: Optional[PrebreakLeaderConfig] = None,
    *,
    exclude_codes: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """筛选0721收盘参考线附近的技术龙胚，并返回评分及触发/失效参考价。"""

    cfg = config or PrebreakLeaderConfig()
    fixed_cfg = FixedWindowDrawdownConfig(
        reference_date=cfg.reference_date,
        target_date=cfg.target_date,
    )
    work, audit = _prepare_and_audit(df, fixed_cfg)
    excluded = _normalise_codes(exclude_codes)
    quality_codes = set(work["ts_code"].unique())
    audit["explicit_codes_excluded"] = int(len(quality_codes & excluded))
    if excluded:
        work = work[~work["ts_code"].isin(excluded)]

    reference_date = pd.Timestamp(cfg.reference_date)
    target_date = pd.Timestamp(cfg.target_date)
    rows: list[dict] = []
    for _, group in work.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date").reset_index(drop=True)
        if len(group) < cfg.min_bars:
            continue
        base = _one_stock_metrics(group, fixed_cfg)
        if base is None:
            continue
        history = group[group["trade_date"] <= target_date].copy()
        reference = history[history["trade_date"].eq(reference_date)]
        if reference.empty or len(history) < cfg.min_bars:
            continue

        reference_close = float(reference.iloc[-1]["close"])
        close = float(history.iloc[-1]["close"])
        close_returns = history["close"].pct_change(fill_method=None)
        ma5_series = history["close"].rolling(5, min_periods=5).mean()
        ma5_now = float(ma5_series.iloc[-1])
        ma5_ref3 = float(ma5_series.iloc[-4])
        last5 = history.tail(5)
        prior20 = history.iloc[-23:-3]
        last10 = history.tail(10).copy()
        last10["ret"] = last10["close"].pct_change(fill_method=None)
        up_vol = float(last10.loc[last10["ret"] > 0, "vol"].sum())
        down_vol = float(last10.loc[last10["ret"] < 0, "vol"].sum())
        up_down_ratio = up_vol / down_vol if down_vol > 0 else 3.0
        prior_close = history["close"].shift(1)
        true_range = pd.concat(
            [
                history["high"] - history["low"],
                (history["high"] - prior_close).abs(),
                (history["low"] - prior_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        range5 = float(last5["high"].max() / last5["low"].min() - 1.0)
        range5_den = float(last5["high"].max() - last5["low"].min())
        close_location5 = (
            float((close - last5["low"].min()) / range5_den) if range5_den > 0 else 0.5
        )
        post_reference = history[history["trade_date"] > reference_date]
        prior_cross = bool(
            (post_reference.iloc[:-1]["close"] > reference_close).any()
            if len(post_reference) > 1
            else False
        )
        gap = close / reference_close - 1.0
        if gap < 0:
            setup_type = "回踩再突破" if prior_cross else "临界待突破"
        else:
            setup_type = "线边突破确认"

        previous5 = history.iloc[-6:-1]
        trigger = max(reference_close, float(previous5["high"].max())) * 1.002
        structure_stop = min(float(last5["low"].min()), float(base["ma20"])) * 0.995
        row = dict(base)
        row.update(
            {
                "reference_close_20260721": reference_close,
                "gap_to_reference_pct": gap * 100.0,
                "gap_to_reference_high_pct": (
                    close / float(base["reference_high_20260721"]) - 1.0
                )
                * 100.0,
                "setup_type": setup_type,
                "prior_cross_since_reference": prior_cross,
                "ma5_slope3_pct": (ma5_now / ma5_ref3 - 1.0) * 100.0,
                "ret20_pct": float(
                    history["close"].pct_change(20, fill_method=None).iloc[-1]
                )
                * 100.0,
                "ret50_pct": float(history["close"].pct_change(50, fill_method=None).iloc[-1]) * 100.0,
                "ret120_pct": float(history["close"].pct_change(120, fill_method=None).iloc[-1]) * 100.0,
                "vol_contraction3": (
                    float(history.tail(3)["vol"].mean()) / float(prior20["vol"].mean())
                    if not prior20.empty and float(prior20["vol"].mean()) > 0
                    else np.nan
                ),
                "up_down_vol_ratio10": min(up_down_ratio, 3.0),
                "range5_pct": range5 * 100.0,
                "close_location5": close_location5,
                "atr14_pct": float(true_range.tail(14).mean() / close * 100.0),
                "trigger_price": trigger,
                "trigger_gap_pct": (trigger / close - 1.0) * 100.0,
                "strong_confirmation_price": float(base["reference_high_20260721"])
                * 1.002,
                "structure_stop": structure_stop,
                "trigger_risk_pct": (trigger / structure_stop - 1.0) * 100.0,
            }
        )
        rows.append(row)

    metrics = pd.DataFrame(rows)
    if metrics.empty:
        audit.update({"prebreak_raw": 0, "prebreak_candidates": 0, "leader_tier": 0})
        metrics.attrs["audit"] = audit
        return metrics

    for period in (5, 20, 50, 120):
        metrics[f"rps{period}"] = metrics[f"ret{period}_pct"].rank(pct=True) * 100.0

    gate = (
        metrics["july_max_drawdown_pct"].between(
            cfg.min_drawdown * 100.0, cfg.max_drawdown * 100.0, inclusive="both"
        )
        & metrics["gap_to_reference_pct"].between(
            cfg.min_gap_to_reference * 100.0,
            cfg.max_gap_to_reference * 100.0,
            inclusive="both",
        )
        & (metrics["amount20_yi"] >= cfg.min_amount20 / 1e8)
        & metrics["close_vs_ma20_pct"].between(
            cfg.min_vs_ma20 * 100.0, cfg.max_vs_ma20 * 100.0, inclusive="both"
        )
        & (metrics["ma5_above_ma20_pct"] >= cfg.min_ma5_vs_ma20 * 100.0)
        & (metrics["ma20_slope5_pct"] >= cfg.min_ma20_slope5 * 100.0)
        & (metrics["ma5_slope3_pct"] >= cfg.min_ma5_slope3 * 100.0)
        & metrics["ret5_pct"].between(
            cfg.min_ret5 * 100.0, cfg.max_ret5 * 100.0, inclusive="both"
        )
        & (metrics["ret1_pct"] <= cfg.max_ret1 * 100.0)
        & metrics["vol_ratio20"].between(
            cfg.min_vol_ratio20, cfg.max_vol_ratio20, inclusive="both"
        )
        & (metrics["range5_pct"] <= cfg.max_range5 * 100.0)
        & (metrics["rps20"] >= cfg.min_rps20)
        & ~metrics["suspected_corporate_action"]
    )
    result = metrics.loc[gate].copy()
    if result.empty:
        result["leader_score"] = pd.Series(dtype=float)
        result["leader_tier"] = pd.Series(dtype=str)
        result["drawdown_grade"] = pd.Series(dtype=str)
        audit.update(
            {
                "strategy_config": asdict(cfg),
                "prebreak_raw": int(len(metrics)),
                "prebreak_candidates": 0,
                "leader_tier": 0,
                "potential_tier": 0,
                "watch_tier": 0,
            }
        )
        result.attrs["audit"] = audit
        return result
    result["leader_score"] = result.apply(_score, axis=1)
    result["leader_tier"] = np.select(
        [
            (result["leader_score"] >= 75.0)
            & (result["rps20"] >= 80.0)
            & (result["ma5"] >= result["ma20"])
            & (result["ma20_slope5_pct"] >= -1.0),
            result["leader_score"] >= 65.0,
        ],
        ["A_技术龙胚", "B_潜在突破"],
        default="C_观察",
    )
    result["drawdown_grade"] = np.where(
        result["july_max_drawdown_pct"].between(30.0, 45.0, inclusive="both"),
        "S_回撤30-45",
        "扩展回撤",
    )
    result = result.sort_values(
        ["leader_tier", "leader_score", "rps20", "gap_to_reference_pct"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)
    audit.update(
        {
            "strategy_config": asdict(cfg),
            "prebreak_raw": int(len(metrics)),
            "prebreak_candidates": int(len(result)),
            "leader_tier": int(result["leader_tier"].eq("A_技术龙胚").sum()),
            "potential_tier": int(result["leader_tier"].eq("B_潜在突破").sum()),
            "watch_tier": int(result["leader_tier"].eq("C_观察").sum()),
        }
    )
    result.attrs["audit"] = audit
    return result
