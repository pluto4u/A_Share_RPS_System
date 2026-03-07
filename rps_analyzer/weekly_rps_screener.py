"""
================================================================================
周线 RPS 吸筹突破选股策略
================================================================================
基于周线级别，寻找:
  1. 周线 RPS_120 翻红 (从弱转强)
  2. 股价穿越 120 周均线
  3. 突破前有紧凑的吸筹过程 (窄幅整理、缩量)
  4. 突破时放量确认
  5. 短期周均线开始多头排列

参考标的: 亨通光电(601126) 长光华芯(688048) 通鼎互联(002491)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, Dict, List


@dataclass
class WeeklyScreenerConfig:
    """周线策略可调参数"""

    # --- 周线 RPS ---
    rps_threshold: float = 80.0          # 周 RPS_120 翻红阈值
    rps_was_weak_within: int = 12        # 过去 N 周内曾低于阈值 (确认是"翻红"而非一直强)
    rps_weak_level: float = 60.0         # "曾经弱"的判断线

    # --- 穿越 120 周线 ---
    ma120w_cross_lookback: int = 8       # 在最近 N 周内完成穿越 (不要求刚好本周)
    price_above_ma120w_min: float = 1.0  # 当前价需高于 MA120W 至少 1%

    # --- 紧凑吸筹 ---
    accum_lookback: int = 20             # 吸筹阶段回看周数
    accum_range_max: float = 0.35        # 吸筹区间振幅上限 (高-低)/低 <=35%
    accum_vol_shrink: float = 0.7        # 吸筹期均量 / 前段均量 <=0.7 (缩量)

    # --- 放量突破 ---
    breakout_vol_ratio: float = 1.5      # 突破周成交量 / 吸筹期均量 >=1.5

    # --- 均线多头 ---
    ma10w_above_ma20w: bool = True       # MA10W > MA20W
    ma10w_rising_weeks: int = 3          # MA10W 连续上升周数
    ma20w_rising_weeks: int = 2          # MA20W 连续上升周数

    # --- 成交额门槛 ---
    min_weekly_amount: float = 3e8       # 周成交额最低 3 亿


def daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """
    日线聚合为周线

    输入需含: trade_date, ts_code, open, high, low, close, vol, amount
    输出: 同名列 + week_end (周五日期)
    """
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ts_code", "trade_date"])

    df["week_end"] = df["trade_date"] - pd.to_timedelta(df["trade_date"].dt.weekday, unit="D") + pd.Timedelta(days=4)

    weekly = df.groupby(["ts_code", "week_end"]).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        vol=("vol", "sum"),
        amount=("amount", "sum"),
    ).reset_index()

    weekly = weekly.rename(columns={"week_end": "trade_date"})
    weekly = weekly.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return weekly


def calculate_weekly_rps(weekly: pd.DataFrame, period: int = 120) -> pd.DataFrame:
    """计算周线 RPS (按周数计算, 120 周 ≈ 2.3 年)"""
    weekly = weekly.copy()
    n = period

    weekly[f"pre_close_{n}"] = weekly.groupby("ts_code")["close"].shift(n)
    weekly[f"roc_{n}"] = (weekly["close"] - weekly[f"pre_close_{n}"]) / weekly[f"pre_close_{n}"]
    weekly[f"W_RPS_{n}"] = weekly.groupby("trade_date")[f"roc_{n}"].rank(pct=True) * 100
    weekly.drop(columns=[f"pre_close_{n}", f"roc_{n}"], inplace=True)

    return weekly


class WeeklyRpsScreener:
    """
    周线 RPS 吸筹突破选股器

    用法:
        screener = WeeklyRpsScreener()
        result = screener.screen(daily_df, target_date='20260305')
    """

    def __init__(self, config: Optional[WeeklyScreenerConfig] = None):
        self.config = config or WeeklyScreenerConfig()

    def screen(
        self,
        daily_df: pd.DataFrame,
        target_date: Optional[str] = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        if verbose:
            print("[周线策略] 日线转周线...")
        weekly = daily_to_weekly(daily_df)

        if verbose:
            print(f"[周线策略] 周线数据: {weekly['ts_code'].nunique()} 只, {len(weekly)} 行")
            print("[周线策略] 计算周线 RPS_120...")
        weekly = calculate_weekly_rps(weekly, period=120)

        for p in [10, 20, 120]:
            col = f"MA_{p}W"
            weekly[col] = weekly.groupby("ts_code")["close"].transform(
                lambda x: x.rolling(p, min_periods=p).mean()
            )

        if target_date is None:
            target_date = weekly["trade_date"].max()
        else:
            target_date = pd.Timestamp(target_date)
            dates = weekly["trade_date"].unique()
            valid = dates[dates <= target_date]
            if len(valid) == 0:
                if verbose:
                    print("[周线策略] 目标日期无数据")
                return pd.DataFrame()
            target_date = valid.max()

        if verbose:
            print(f"[周线策略] 目标周: {target_date.strftime('%Y-%m-%d')}")

        lookback = max(
            self.config.accum_lookback,
            self.config.ma120w_cross_lookback,
            self.config.rps_was_weak_within,
            self.config.ma10w_rising_weeks + 1,
        ) + 5

        stocks = weekly["ts_code"].unique()
        records = []

        for code in stocks:
            sdf = weekly[weekly["ts_code"] == code]
            window = sdf[sdf["trade_date"] <= target_date].tail(lookback)

            if len(window) < lookback:
                continue

            today = window.iloc[-1]
            if pd.isna(today.get("W_RPS_120")) or pd.isna(today.get("MA_120W")):
                continue

            rec = {
                "trade_date": target_date,
                "ts_code": code,
                "close": today["close"],
                "W_RPS_120": today["W_RPS_120"],
                "MA_120W": today["MA_120W"],
            }

            rec["W_COND1"] = self._cond_rps_turn(window)
            rec["W_COND2"] = self._cond_cross_ma120(window)
            rec["W_COND3"] = self._cond_accumulation(window)
            rec["W_COND4"] = self._cond_breakout_volume(window)
            rec["W_COND5"] = self._cond_ma_alignment(window)
            rec["W_COND6"] = self._cond_min_amount(window)

            records.append(rec)

        if not records:
            if verbose:
                print("[周线策略] 无数据")
            return pd.DataFrame()

        signals = pd.DataFrame(records)
        cond_cols = [c for c in signals.columns if c.startswith("W_COND")]
        signals["W_PASS"] = signals[cond_cols].all(axis=1)
        passed = signals[signals["W_PASS"]].copy()

        if verbose:
            total = len(signals)
            for col in cond_cols:
                cnt = signals[col].sum()
                print(f"  {col}: {cnt}/{total} ({cnt/total*100:.1f}%)")
            print(f"  PASS: {len(passed)}/{total}")

        return passed

    # =========================================================================
    # 6 个周线选股条件
    # =========================================================================

    def _cond_rps_turn(self, window: pd.DataFrame) -> bool:
        """W_COND1: 周 RPS_120 翻红 — 当前 > 阈值，且近期曾低于弱势线"""
        cfg = self.config
        today = window.iloc[-1]
        if today["W_RPS_120"] < cfg.rps_threshold:
            return False

        history = window.tail(cfg.rps_was_weak_within + 1).iloc[:-1]
        was_weak = (history["W_RPS_120"] < cfg.rps_weak_level).any()
        return was_weak

    def _cond_cross_ma120(self, window: pd.DataFrame) -> bool:
        """W_COND2: 穿越 120 周线 — 当前在上方，最近 N 周内曾在下方"""
        cfg = self.config
        today = window.iloc[-1]

        if today["close"] < today["MA_120W"] * (1 + cfg.price_above_ma120w_min / 100):
            return False

        recent = window.tail(cfg.ma120w_cross_lookback + 1).iloc[:-1]
        was_below = (recent["close"] < recent["MA_120W"]).any()
        return was_below

    def _cond_accumulation(self, window: pd.DataFrame) -> bool:
        """
        W_COND3: 紧凑吸筹
          - 突破前的 N 周价格振幅 <= 阈值
          - 吸筹期成交量相比更早期缩量
        """
        cfg = self.config
        n = cfg.accum_lookback

        accum_zone = window.iloc[-(n + 1):-1]
        if len(accum_zone) < n:
            return False

        zone_high = accum_zone["high"].max()
        zone_low = accum_zone["low"].min()
        if zone_low <= 0:
            return False
        price_range = (zone_high - zone_low) / zone_low
        if price_range > cfg.accum_range_max:
            return False

        half = n // 2
        later_vol = accum_zone.tail(half)["vol"].mean()
        earlier_vol = accum_zone.head(half)["vol"].mean()
        if earlier_vol > 0 and later_vol / earlier_vol > cfg.accum_vol_shrink:
            return False

        return True

    def _cond_breakout_volume(self, window: pd.DataFrame) -> bool:
        """W_COND4: 放量突破 — 最近周成交量 > 吸筹期均量 * 倍数"""
        cfg = self.config
        today = window.iloc[-1]
        accum_zone = window.iloc[-(cfg.accum_lookback + 1):-1]
        if len(accum_zone) == 0:
            return False

        accum_avg_vol = accum_zone["vol"].mean()
        if accum_avg_vol <= 0:
            return False

        recent_3w_vol = window.tail(3)["vol"].mean()
        return recent_3w_vol >= accum_avg_vol * cfg.breakout_vol_ratio

    def _cond_ma_alignment(self, window: pd.DataFrame) -> bool:
        """W_COND5: 均线多头 — MA10W > MA20W, MA10W/MA20W 连续上升"""
        cfg = self.config
        today = window.iloc[-1]

        if cfg.ma10w_above_ma20w and today["MA_10W"] < today["MA_20W"]:
            return False

        tail = window.tail(cfg.ma10w_rising_weeks + 1)
        if len(tail) < cfg.ma10w_rising_weeks + 1:
            return False
        ma10_vals = tail["MA_10W"].values
        if not all(ma10_vals[i + 1] >= ma10_vals[i] for i in range(len(ma10_vals) - cfg.ma10w_rising_weeks, len(ma10_vals) - 1)):
            return False

        tail20 = window.tail(cfg.ma20w_rising_weeks + 1)
        ma20_vals = tail20["MA_20W"].values
        if not all(ma20_vals[i + 1] >= ma20_vals[i] for i in range(len(ma20_vals) - cfg.ma20w_rising_weeks, len(ma20_vals) - 1)):
            return False

        return True

    def _cond_min_amount(self, window: pd.DataFrame) -> bool:
        """W_COND6: 最近周成交额 >= 门槛"""
        cfg = self.config
        recent = window.tail(3)
        avg_amount = recent["amount"].mean()
        return avg_amount >= cfg.min_weekly_amount

    # =========================================================================
    # 诊断
    # =========================================================================

    def diagnose(self, daily_df: pd.DataFrame, ts_code: str, target_date: Optional[str] = None) -> Dict:
        weekly = daily_to_weekly(daily_df)
        weekly = calculate_weekly_rps(weekly, period=120)
        for p in [10, 20, 120]:
            col = f"MA_{p}W"
            weekly[col] = weekly.groupby("ts_code")["close"].transform(
                lambda x: x.rolling(p, min_periods=p).mean()
            )

        if target_date is None:
            target_date = weekly["trade_date"].max()
        else:
            target_date = pd.Timestamp(target_date)
            dates = weekly["trade_date"].unique()
            valid = dates[dates <= target_date]
            target_date = valid.max() if len(valid) > 0 else target_date

        lookback = max(self.config.accum_lookback, self.config.ma120w_cross_lookback, self.config.rps_was_weak_within) + 5
        sdf = weekly[weekly["ts_code"] == ts_code]
        window = sdf[sdf["trade_date"] <= target_date].tail(lookback)

        if window.empty:
            return {"error": f"no data for {ts_code}"}

        today = window.iloc[-1]
        result = {
            "ts_code": ts_code,
            "trade_date": str(target_date.date()),
            "close": today["close"],
            "W_RPS_120": today.get("W_RPS_120"),
            "MA_120W": today.get("MA_120W"),
            "conditions": {
                "W_COND1_rps_turn": self._cond_rps_turn(window),
                "W_COND2_cross_ma120": self._cond_cross_ma120(window),
                "W_COND3_accumulation": self._cond_accumulation(window),
                "W_COND4_breakout_vol": self._cond_breakout_volume(window),
                "W_COND5_ma_alignment": self._cond_ma_alignment(window),
                "W_COND6_min_amount": self._cond_min_amount(window),
            },
        }
        result["all_pass"] = all(result["conditions"].values())
        return result
