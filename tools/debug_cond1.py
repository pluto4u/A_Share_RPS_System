"""Debug COND1 for 601126"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.weekly_rps_screener import daily_to_weekly, calculate_weekly_rps, WeeklyScreenerConfig

df = load_tdx_market(verbose=False)
weekly = daily_to_weekly(df)
weekly = calculate_weekly_rps(weekly, period=120)

sdf = weekly[weekly["ts_code"] == "601126.SH"].copy()
target = pd.Timestamp("2025-09-26")
cfg = WeeklyScreenerConfig()
lookback = max(cfg.accum_lookback, cfg.ma120w_cross_lookback, cfg.rps_was_weak_within, cfg.ma10w_rising_weeks + 1) + 5
window = sdf[sdf["trade_date"] <= target].tail(lookback)

print(f"lookback={lookback}, window rows={len(window)}")
print(f"window: {window.iloc[0]['trade_date'].date()} ~ {window.iloc[-1]['trade_date'].date()}")

today_rps = window.iloc[-1]["W_RPS_120"]
print(f"today W_RPS_120={today_rps:.1f}, threshold={cfg.rps_threshold}")

history = window.tail(cfg.rps_was_weak_within + 1).iloc[:-1]
print(f"history: {history.iloc[0]['trade_date'].date()} ~ {history.iloc[-1]['trade_date'].date()} ({len(history)} rows)")

rps_min = history["W_RPS_120"].min()
rps_max = history["W_RPS_120"].max()
below = (history["W_RPS_120"] < cfg.rps_weak_level).sum()
print(f"RPS range in history: {rps_min:.1f} ~ {rps_max:.1f}")
print(f"weeks below {cfg.rps_weak_level}: {below}")
print(f"COND1 should PASS: {today_rps >= cfg.rps_threshold and below > 0}")
