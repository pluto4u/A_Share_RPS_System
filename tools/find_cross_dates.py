"""找到参考标的穿越 120 周线的日期"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from rps_analyzer.tdx_reader import load_tdx_market
from rps_analyzer.weekly_rps_screener import daily_to_weekly

df = load_tdx_market(r"D:\tdx\vipdoc", markets=["sh", "sz"], min_days=300, verbose=False)

for code in ["601126.SH", "688048.SH", "002491.SZ"]:
    sdf = df[df["ts_code"] == code].copy()
    weekly = daily_to_weekly(sdf)
    weekly["MA_120W"] = weekly["close"].rolling(120, min_periods=120).mean()

    weekly["above"] = weekly["close"] > weekly["MA_120W"]
    weekly["prev_above"] = weekly["above"].shift(1)
    crosses = weekly[(weekly["above"] == True) & (weekly["prev_above"] == False)].tail(3)

    print(f"\n{code} - cross above MA_120W:")
    for _, r in crosses.iterrows():
        dt = r["trade_date"].strftime("%Y-%m-%d")
        print(f"  {dt}  close={r['close']:.2f}  MA120W={r['MA_120W']:.2f}")
