"""quick close lookup"""
import sys
import pandas as pd

df = pd.read_parquet("output/cache_tdx_with_rps.parquet")
for code in sys.argv[1:]:
    sub = df[df["ts_code"] == code].sort_values("trade_date")
    if sub.empty:
        print(f"{code}: not found")
        continue
    row = sub.iloc[-1]
    print(f"{code}  date={row['trade_date'].date()}  close={row['close']:.2f}  RPS_120={row['RPS_120']:.0f}  RPS_250={row['RPS_250']:.0f}")
