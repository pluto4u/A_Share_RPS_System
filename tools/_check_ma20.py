"""检查 4/30 当日 close vs MA20 / MA60 / ret20"""
import sys
import pandas as pd

df = pd.read_parquet("output/cache_tdx_with_rps.parquet")
df["trade_date"] = pd.to_datetime(df["trade_date"])

target = pd.Timestamp("2026-04-30")
codes = sys.argv[1:] if len(sys.argv) > 1 else [
    "002636.SZ", "688195.SH", "301377.SZ", "600105.SH", "000688.SZ",
    "688498.SH", "300136.SZ", "603601.SH", "603268.SH",
    "001270.SZ", "002565.SZ", "000657.SZ"
]

print(f"{'code':<13}{'close':>9}{'MA20':>10}{'MA60':>10}{'vs MA20':>10}{'vs MA60':>10}{'ret20':>9} 状态")
for code in codes:
    sub = df[df["ts_code"] == code].sort_values("trade_date").copy()
    if sub.empty:
        print(f"{code:<13} 无数据")
        continue
    sub["MA20"] = sub["close"].rolling(20, min_periods=20).mean()
    sub["MA60"] = sub["close"].rolling(60, min_periods=60).mean()
    sub["ret20"] = sub["close"] / sub["close"].shift(20) - 1

    today = sub[sub["trade_date"] == target]
    if today.empty:
        print(f"{code:<13} 4/30 无行情")
        continue
    r = today.iloc[-1]
    pass_ma20 = "[OK]" if r["close"] > r["MA20"] else "[FAIL] close<MA20"
    pass_vs60 = "[OK]" if r["close"]/r["MA60"]-1 <= 0.50 else "[FAIL] vs60>50%"
    pass_r20 = "[OK]" if r["ret20"] <= 0.50 else "[FAIL] ret20>50%"
    print(f"{code:<13}{r['close']:>9.2f}{r['MA20']:>10.2f}{r['MA60']:>10.2f}"
          f"{r['close']/r['MA20']-1:>9.1%}{r['close']/r['MA60']-1:>9.1%}{r['ret20']:>9.1%}"
          f"  {pass_ma20} | {pass_vs60} | {pass_r20}")
