"""临时: 从 0508 全量扫描的源数据重算 MA20 验证 close>MA20.

用法: py -3 .\tools\_check_form_0508.py [code1 code2 ...]
默认验证 5/11 候选 + 持仓.
"""
import sys
import os
import pandas as pd

from rps_analyzer.tdx_reader import load_tdx_market

DEFAULT_CODES = [
    "603929.SH", "301338.SZ", "001270.SZ", "002353.SZ", "301362.SZ",
    "600487.SH", "688498.SH",
    "002636.SZ", "301377.SZ", "600105.SH", "300136.SZ", "603601.SH",
]

target = pd.Timestamp("2026-05-08")
codes = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_CODES

print("[load] 从 D:\\tdx\\vipdoc 直接读全市场...")
df = load_tdx_market(r"D:\tdx\vipdoc", min_days=200)
df["trade_date"] = pd.to_datetime(df["trade_date"])

print(f"\n{'code':<13}{'close':>9}{'MA20':>10}{'MA60':>10}{'vs20%':>8}{'vs60%':>8}{'ret20%':>8}  SOP-6")
for code in codes:
    sub = df[df["ts_code"] == code].sort_values("trade_date").copy()
    if sub.empty:
        print(f"{code:<13} 无数据"); continue
    sub["MA20"] = sub["close"].rolling(20, min_periods=20).mean()
    sub["MA60"] = sub["close"].rolling(60, min_periods=60).mean()
    sub["ret20"] = sub["close"] / sub["close"].shift(20) - 1
    today = sub[sub["trade_date"] == target]
    if today.empty:
        last = sub.iloc[-1]
        print(f"{code:<13} 5/8无行情, 最新={last['trade_date'].date()} close={last['close']:.2f}")
        continue
    r = today.iloc[-1]
    p1 = "OK" if r["close"] > r["MA20"] else "F:close<MA20"
    p2 = "OK" if r["close"]/r["MA60"]-1 <= 0.50 else "F:vs60>50"
    p3 = "OK" if r["ret20"] <= 0.50 else "F:ret20>50"
    flag = "[ALL]" if (p1 == p2 == p3 == "OK") else "[X]"
    print(f"{code:<13}{r['close']:>9.2f}{r['MA20']:>10.2f}{r['MA60']:>10.2f}"
          f"{r['close']/r['MA20']-1:>7.1%}{r['close']/r['MA60']-1:>7.1%}{r['ret20']:>7.1%}"
          f"  {flag} {p1} | {p2} | {p3}")
