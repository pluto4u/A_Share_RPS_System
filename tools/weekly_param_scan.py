"""扫描参考标的在不同时间点的周线条件状态，帮助找到最佳信号窗口和调参方向"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.weekly_rps_screener import (
    daily_to_weekly, calculate_weekly_rps, WeeklyRpsScreener, WeeklyScreenerConfig,
)

print("Loading TDX data...")
df = load_tdx_market(r"D:\tdx\vipdoc", markets=["sh", "sz"], min_days=300, verbose=False)

print("Building weekly data...")
weekly = daily_to_weekly(df)
weekly = calculate_weekly_rps(weekly, period=120)
for p in [10, 20, 120]:
    col = f"MA_{p}W"
    weekly[col] = weekly.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(p, min_periods=p).mean()
    )

targets = {
    "601126.SH": ("2025-04-01", "2025-12-31"),
    "688048.SH": ("2025-03-01", "2025-12-31"),
    "002491.SZ": ("2025-01-01", "2025-12-31"),
}

for code, (start, end) in targets.items():
    sdf = weekly[weekly["ts_code"] == code].copy()
    sdf = sdf[(sdf["trade_date"] >= start) & (sdf["trade_date"] <= end)]

    print(f"\n{'='*80}")
    print(f"{code}  ({start} ~ {end})")
    print(f"{'date':>12} {'close':>8} {'W_RPS':>7} {'MA120W':>8} {'above':>6} {'MA10W':>8} {'MA20W':>8} {'10>20':>6}")
    print("-" * 80)

    for _, r in sdf.iterrows():
        dt = r["trade_date"].strftime("%Y-%m-%d")
        close = r["close"]
        rps = r.get("W_RPS_120", 0) or 0
        ma120 = r.get("MA_120W", 0) or 0
        ma10 = r.get("MA_10W", 0) or 0
        ma20 = r.get("MA_20W", 0) or 0
        above = "Y" if close > ma120 else ""
        align = "Y" if ma10 > ma20 else ""
        marker = " <--" if rps > 70 and above == "Y" else ""
        print(f"{dt:>12} {close:>8.2f} {rps:>7.1f} {ma120:>8.2f} {above:>6} {ma10:>8.2f} {ma20:>8.2f} {align:>6}{marker}")
