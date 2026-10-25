from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .weekly_rps_screener import daily_to_weekly, calculate_weekly_rps


@dataclass
class WeeklyTrendConfig:
    rps120w_min: float = 90.0
    ma_rising_weeks: int = 3
    near_high_weeks: int = 52
    near_high_ratio_min: float = 0.75
    max_vs_ma20w: float = 0.35
    max_ret4w: float = 0.60
    min_weekly_amount: float = 2e8
    a_pullback_vs_ma20d_max: float = 0.12
    a_ret20_max: float = 0.35


def _add_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    weekly = weekly.copy()
    for p in [10, 20, 30]:
        weekly[f"MA_{p}W"] = weekly.groupby("ts_code")["close"].transform(
            lambda x: x.rolling(p, min_periods=p).mean()
        )
    weekly["pre_close_4w"] = weekly.groupby("ts_code")["close"].shift(4)
    weekly["ret4w"] = (weekly["close"] - weekly["pre_close_4w"]) / weekly["pre_close_4w"]
    weekly.drop(columns=["pre_close_4w"], inplace=True)
    return weekly


def _add_daily_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()
    daily = daily.sort_values(["ts_code", "trade_date"])
    for p in [20, 60]:
        daily[f"MA_{p}D"] = daily.groupby("ts_code")["close"].transform(
            lambda x: x.rolling(p, min_periods=p).mean()
        )
    daily["pre_close_20d"] = daily.groupby("ts_code")["close"].shift(20)
    daily["ret20d"] = (daily["close"] - daily["pre_close_20d"]) / daily["pre_close_20d"]
    daily.drop(columns=["pre_close_20d"], inplace=True)
    return daily


def _is_rising(values: pd.Series, weeks: int) -> bool:
    tail = values.dropna().tail(weeks + 1)
    if len(tail) < weeks + 1:
        return False
    vals = tail.to_list()
    return all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))


def _resolve_target_week(weekly: pd.DataFrame, target_date: Optional[str]) -> pd.Timestamp | None:
    if target_date is None:
        return pd.Timestamp(weekly["trade_date"].max())
    td = pd.Timestamp(target_date)
    dates = weekly["trade_date"].unique()
    valid = dates[dates <= td]
    if len(valid) == 0:
        return None
    return pd.Timestamp(valid.max())


def _resolve_target_day(daily: pd.DataFrame, target_date: Optional[str]) -> pd.Timestamp | None:
    if target_date is None:
        return pd.Timestamp(daily["trade_date"].max())
    td = pd.Timestamp(target_date)
    dates = daily["trade_date"].unique()
    valid = dates[dates <= td]
    if len(valid) == 0:
        return None
    return pd.Timestamp(valid.max())


class WeeklyTrendScreener:
    def __init__(self, config: Optional[WeeklyTrendConfig] = None):
        self.config = config or WeeklyTrendConfig()

    def screen(
        self,
        daily_df: pd.DataFrame,
        target_date: Optional[str] = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        cfg = self.config

        daily = daily_df.copy()
        daily["trade_date"] = pd.to_datetime(daily["trade_date"])
        daily = _add_daily_indicators(daily)

        weekly = daily_to_weekly(daily)
        weekly = calculate_weekly_rps(weekly, period=120)
        weekly = _add_weekly_indicators(weekly)

        target_week = _resolve_target_week(weekly, target_date)
        target_day = _resolve_target_day(daily, target_date)
        if target_week is None or target_day is None:
            if verbose:
                print("[WeeklyTrend] no data for target date")
            return pd.DataFrame()

        if verbose:
            print(f"[WeeklyTrend] target day : {target_day.strftime('%Y-%m-%d')}")
            print(f"[WeeklyTrend] target week: {target_week.strftime('%Y-%m-%d')} (completed week)")

        latest_daily = (
            daily[daily["trade_date"] <= target_day]
            .sort_values(["ts_code", "trade_date"])
            .groupby("ts_code")
            .tail(1)
            .set_index("ts_code")
        )

        lookback = max(cfg.near_high_weeks, cfg.ma_rising_weeks + 1, 30) + 5
        records: list[dict] = []
        for code, sdf in weekly.groupby("ts_code", sort=False):
            window = sdf[sdf["trade_date"] <= target_week].tail(lookback)
            if len(window) < max(cfg.near_high_weeks, 30):
                continue
            today = window.iloc[-1]
            if (
                pd.isna(today.get("W_RPS_120"))
                or pd.isna(today.get("MA_10W"))
                or pd.isna(today.get("MA_20W"))
                or pd.isna(today.get("MA_30W"))
            ):
                continue

            close_w = float(today["close"])
            ma10w = float(today["MA_10W"])
            ma20w = float(today["MA_20W"])
            ma30w = float(today["MA_30W"])
            near_high = close_w / window.tail(cfg.near_high_weeks)["close"].max()
            vs_ma20w = close_w / ma20w - 1 if ma20w > 0 else pd.NA
            ret4w = today.get("ret4w")
            recent_amount = window.tail(4)["amount"].mean()

            cond_rps = today["W_RPS_120"] >= cfg.rps120w_min
            cond_ma_order = close_w > ma10w > ma20w > ma30w
            cond_ma_rising = (
                _is_rising(window["MA_10W"], cfg.ma_rising_weeks)
                and _is_rising(window["MA_20W"], cfg.ma_rising_weeks)
            )
            cond_near_high = near_high >= cfg.near_high_ratio_min
            cond_not_extended = pd.notna(vs_ma20w) and vs_ma20w <= cfg.max_vs_ma20w
            cond_ret4w = pd.notna(ret4w) and ret4w <= cfg.max_ret4w
            cond_amount = recent_amount >= cfg.min_weekly_amount

            if code in latest_daily.index:
                drow = latest_daily.loc[code]
                close_d = float(drow["close"])
                ma20d = drow.get("MA_20D")
                ma60d = drow.get("MA_60D")
                ret20d = drow.get("ret20d")
                vs_ma20d = close_d / ma20d - 1 if pd.notna(ma20d) and ma20d > 0 else pd.NA
                vs_ma60d = close_d / ma60d - 1 if pd.notna(ma60d) and ma60d > 0 else pd.NA
            else:
                close_d = pd.NA
                ma20d = pd.NA
                ma60d = pd.NA
                ret20d = pd.NA
                vs_ma20d = pd.NA
                vs_ma60d = pd.NA

            trend_pass = all(
                [
                    cond_rps,
                    cond_ma_order,
                    cond_ma_rising,
                    cond_near_high,
                    cond_not_extended,
                    cond_ret4w,
                    cond_amount,
                ]
            )
            a_pass = (
                trend_pass
                and pd.notna(vs_ma20d)
                and pd.notna(ret20d)
                and close_d > ma20d
                and vs_ma20d <= cfg.a_pullback_vs_ma20d_max
                and ret20d <= cfg.a_ret20_max
            )

            trend_score = (
                float(today["W_RPS_120"]) * 0.5
                + float(near_high) * 30
                + max(0.0, 20 - float(vs_ma20w) * 100) * 0.5
            )

            records.append(
                {
                    "trade_date": target_day,
                    "week_date": target_week,
                    "ts_code": code,
                    "close": close_d,
                    "weekly_close": close_w,
                    "W_RPS_120": today["W_RPS_120"],
                    "MA_10W": ma10w,
                    "MA_20W": ma20w,
                    "MA_30W": ma30w,
                    "near_high_52w": near_high,
                    "vs_ma20w": vs_ma20w,
                    "ret4w": ret4w,
                    "weekly_amount_4w": recent_amount,
                    "MA_20D": ma20d,
                    "MA_60D": ma60d,
                    "vs_ma20d": vs_ma20d,
                    "vs_ma60d": vs_ma60d,
                    "ret20d": ret20d,
                    "WT_RPS": cond_rps,
                    "WT_MA_ORDER": cond_ma_order,
                    "WT_MA_RISING": cond_ma_rising,
                    "WT_NEAR_HIGH": cond_near_high,
                    "WT_NOT_EXTENDED": cond_not_extended,
                    "WT_RET4W": cond_ret4w,
                    "WT_AMOUNT": cond_amount,
                    "WEEKLY_TREND": trend_pass,
                    "TREND_TIER": "A" if a_pass else ("B" if trend_pass else ""),
                    "trend_score": trend_score,
                }
            )

        if not records:
            if verbose:
                print("[WeeklyTrend] no computable samples")
            return pd.DataFrame()

        signals = pd.DataFrame(records)
        cond_cols = [
            "WT_RPS",
            "WT_MA_ORDER",
            "WT_MA_RISING",
            "WT_NEAR_HIGH",
            "WT_NOT_EXTENDED",
            "WT_RET4W",
            "WT_AMOUNT",
        ]
        result = signals[signals["WEEKLY_TREND"]].copy()
        result = result.sort_values(
            ["TREND_TIER", "trend_score", "W_RPS_120"],
            ascending=[True, False, False],
        ).reset_index(drop=True)

        if verbose:
            total = len(signals)
            for col in cond_cols:
                cnt = int(signals[col].sum())
                print(f"  {col}: {cnt}/{total} ({cnt / total * 100:.1f}%)")
            print(f"  WEEKLY_TREND: {len(result)}/{total}")
            if not result.empty:
                print(f"  Tier A: {(result['TREND_TIER'] == 'A').sum()}")
                print(f"  Tier B: {(result['TREND_TIER'] == 'B').sum()}")

        return result
