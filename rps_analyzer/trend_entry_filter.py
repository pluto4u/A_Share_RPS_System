from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class TrendEntryConfig:
    max_ready_vs_ma20d: float = 0.08
    max_watch_vs_ma20d: float = 0.12
    max_ready_ret20d: float = 0.30
    max_watch_ret20d: float = 0.40
    max_ret5d: float = 0.16
    max_vol3_ratio_ready: float = 1.05
    max_vol3_ratio_watch: float = 1.30
    max_range5_ready: float = 0.16
    max_range5_watch: float = 0.22
    min_pullback_from_10d_high: float = 0.02
    max_pullback_from_20d_high: float = 0.22
    ma20_buffer: float = 0.00
    min_weekly_tier: Optional[str] = None


def _add_entry_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily = daily.sort_values(["ts_code", "trade_date"])

    grouped = daily.groupby("ts_code", group_keys=False)
    for p in [10, 20, 60]:
        daily[f"MA_{p}D"] = grouped["close"].transform(
            lambda x: x.rolling(p, min_periods=p).mean()
        )

    daily["pre_close_5d"] = grouped["close"].shift(5)
    daily["pre_close_20d"] = grouped["close"].shift(20)
    daily["ret5d"] = (daily["close"] - daily["pre_close_5d"]) / daily["pre_close_5d"]
    daily["ret20d"] = (daily["close"] - daily["pre_close_20d"]) / daily["pre_close_20d"]

    daily["vol3"] = grouped["vol"].transform(lambda x: x.rolling(3, min_periods=3).mean())
    daily["vol20"] = grouped["vol"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    daily["vol3_ratio"] = daily["vol3"] / daily["vol20"]

    daily["high5"] = grouped["high"].transform(lambda x: x.rolling(5, min_periods=5).max())
    daily["low5"] = grouped["low"].transform(lambda x: x.rolling(5, min_periods=5).min())
    daily["high10"] = grouped["high"].transform(lambda x: x.rolling(10, min_periods=10).max())
    daily["high20"] = grouped["high"].transform(lambda x: x.rolling(20, min_periods=20).max())
    daily["range5"] = (daily["high5"] - daily["low5"]) / daily["low5"]
    daily["pullback_10d"] = daily["close"] / daily["high10"] - 1
    daily["pullback_20d"] = daily["close"] / daily["high20"] - 1

    daily.drop(columns=["pre_close_5d", "pre_close_20d"], inplace=True)
    return daily


def _resolve_target_day(daily: pd.DataFrame, target_date: Optional[str]) -> pd.Timestamp | None:
    if target_date is None:
        return pd.Timestamp(daily["trade_date"].max())
    td = pd.Timestamp(target_date)
    dates = daily["trade_date"].unique()
    valid = dates[dates <= td]
    if len(valid) == 0:
        return None
    return pd.Timestamp(valid.max())


def _entry_score(row: pd.Series) -> float:
    trend_score = float(row.get("trend_score", 0) or 0)
    vs_ma20d = float(row.get("vs_ma20d", 0) or 0)
    ret20d = float(row.get("ret20d", 0) or 0)
    vol_ratio = float(row.get("vol3_ratio", 1) or 1)
    range5 = float(row.get("range5", 0) or 0)
    pullback_10d = abs(float(row.get("pullback_10d", 0) or 0))

    proximity_bonus = max(0.0, 12.0 - vs_ma20d * 100)
    calm_bonus = max(0.0, 1.4 - vol_ratio) * 8 + max(0.0, 0.22 - range5) * 30
    heat_penalty = max(0.0, ret20d - 0.20) * 25
    pullback_bonus = min(pullback_10d * 100, 8.0)
    return trend_score * 0.65 + proximity_bonus + calm_bonus + pullback_bonus - heat_penalty


def filter_trend_entries(
    daily_df: pd.DataFrame,
    trend_pool: pd.DataFrame,
    target_date: Optional[str] = None,
    config: Optional[TrendEntryConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    cfg = config or TrendEntryConfig()
    if trend_pool.empty:
        if verbose:
            print("[TrendEntry] empty trend pool")
        return pd.DataFrame()

    daily = _add_entry_indicators(daily_df)
    target_day = _resolve_target_day(daily, target_date)
    if target_day is None:
        if verbose:
            print("[TrendEntry] no daily data for target date")
        return pd.DataFrame()

    pool = trend_pool.copy()
    if cfg.min_weekly_tier:
        pool = pool[pool["TREND_TIER"].astype(str) <= cfg.min_weekly_tier]
    pool_codes = set(pool["ts_code"].dropna().astype(str))
    if not pool_codes:
        if verbose:
            print("[TrendEntry] no codes after tier filter")
        return pd.DataFrame()

    latest = (
        daily[(daily["trade_date"] <= target_day) & daily["ts_code"].isin(pool_codes)]
        .sort_values(["ts_code", "trade_date"])
        .groupby("ts_code")
        .tail(1)
    )
    if latest.empty:
        if verbose:
            print("[TrendEntry] no latest daily rows for trend pool")
        return pd.DataFrame()

    keep_cols = [
        "ts_code",
        "trade_date",
        "close",
        "MA_10D",
        "MA_20D",
        "MA_60D",
        "ret5d",
        "ret20d",
        "vol3_ratio",
        "range5",
        "pullback_10d",
        "pullback_20d",
    ]
    merged = pool.merge(latest[keep_cols], on="ts_code", how="left", suffixes=("_trend", ""))
    merged["entry_trade_date"] = target_day
    if "close" not in merged.columns and "close_trend" in merged.columns:
        merged["close"] = merged["close_trend"]
    elif "close_trend" in merged.columns:
        merged["close"] = merged["close"].fillna(merged["close_trend"])

    merged["vs_ma10d"] = merged["close"] / merged["MA_10D"] - 1
    merged["vs_ma20d"] = merged["close"] / merged["MA_20D"] - 1
    merged["vs_ma60d"] = merged["close"] / merged["MA_60D"] - 1

    above_ma20 = merged["close"] >= merged["MA_20D"] * (1 + cfg.ma20_buffer)
    ma20_above_ma60 = merged["MA_20D"] > merged["MA_60D"]
    near_ma20_ready = merged["vs_ma20d"].between(cfg.ma20_buffer, cfg.max_ready_vs_ma20d)
    near_ma20_watch = merged["vs_ma20d"].between(cfg.ma20_buffer, cfg.max_watch_vs_ma20d)
    not_hot_ready = (merged["ret20d"] <= cfg.max_ready_ret20d) & (merged["ret5d"] <= cfg.max_ret5d)
    not_hot_watch = (merged["ret20d"] <= cfg.max_watch_ret20d) & (merged["ret5d"] <= cfg.max_ret5d)
    calm_ready = (merged["vol3_ratio"] <= cfg.max_vol3_ratio_ready) & (merged["range5"] <= cfg.max_range5_ready)
    calm_watch = (merged["vol3_ratio"] <= cfg.max_vol3_ratio_watch) & (merged["range5"] <= cfg.max_range5_watch)
    has_pullback = merged["pullback_10d"] <= -cfg.min_pullback_from_10d_high
    not_broken = merged["pullback_20d"] >= -cfg.max_pullback_from_20d_high

    merged["ENTRY_READY"] = (
        above_ma20
        & ma20_above_ma60
        & near_ma20_ready
        & not_hot_ready
        & calm_ready
        & has_pullback
        & not_broken
    )
    merged["ENTRY_WATCH"] = (
        above_ma20
        & ma20_above_ma60
        & near_ma20_watch
        & not_hot_watch
        & calm_watch
        & not_broken
        & ~merged["ENTRY_READY"]
    )
    merged["ENTRY_TIER"] = ""
    merged.loc[merged["ENTRY_WATCH"], "ENTRY_TIER"] = "WATCH"
    merged.loc[merged["ENTRY_READY"], "ENTRY_TIER"] = "READY"
    merged["entry_score"] = merged.apply(_entry_score, axis=1)

    result = merged[merged["ENTRY_TIER"] != ""].copy()
    result = result.sort_values(
        ["ENTRY_TIER", "entry_score", "TREND_TIER", "W_RPS_120"],
        ascending=[True, False, True, False],
    ).reset_index(drop=True)

    if verbose:
        total = len(merged)
        print(f"[TrendEntry] target day: {target_day.strftime('%Y-%m-%d')}")
        print(f"  trend pool: {total}")
        print(f"  above MA20D: {int(above_ma20.sum())}/{total}")
        print(f"  near MA20D ready: {int(near_ma20_ready.sum())}/{total}")
        print(f"  calm ready: {int(calm_ready.sum())}/{total}")
        print(f"  READY: {int(merged['ENTRY_READY'].sum())}")
        print(f"  WATCH: {int(merged['ENTRY_WATCH'].sum())}")

    return result
