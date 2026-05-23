"""
诊断 688662.SH 为什么在早期∩L1 入选但未进入火车轨。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

CODE = "688662.SH"
DATE = "20260417"
OUTPUT_DIR = Path("output")

# 1. 看在哪些清单里
print("=" * 70)
print(f"诊断 {CODE} @ {DATE}")
print("=" * 70)
print("\n[1] 在各清单中的隶属:")
for f in ["tdx_screener_20260417_codes.csv",
          "tdx_screener_20260417_L2_codes.csv",
          "tdx_screener_20260417_early_codes.csv",
          "tdx_screener_20260417_early_L1_codes.csv",
          "tdx_screener_20260417_huoche_codes.csv"]:
    p = OUTPUT_DIR / f
    if not p.exists():
        print(f"  {f}: 文件不存在")
        continue
    codes = pd.read_csv(p, header=None, dtype=str).iloc[:, 0].astype(str).tolist()
    flag = "YES" if CODE in codes else " no"
    print(f"  [{flag}] {f}  (n={len(codes)})")

# 2. 看 early csv 中该票的明细
print("\n[2] 早期启涨明细 (early.csv):")
early = pd.read_csv(OUTPUT_DIR / "tdx_screener_20260417_early.csv")
row = early[early["ts_code"] == CODE]
if row.empty:
    print("  not in early.csv")
else:
    for col in row.columns:
        print(f"  {col}: {row.iloc[0][col]}")

# 3. 跑一次 screen_huoche 单独看 W_SXHCG 各条件
print("\n[3] 周线火车轨条件诊断:")
from rps_analyzer.huoche_screener import (
    HuocheConfig, _add_weekly_indicators, _compute_signals_for_code,
)
from rps_analyzer.weekly_rps_screener import daily_to_weekly, calculate_weekly_rps

df = pd.read_parquet(OUTPUT_DIR / "cache_tdx_with_rps.parquet")
df["trade_date"] = pd.to_datetime(df["trade_date"])
sub = df[df["ts_code"] == CODE].copy()
print(f"  日线行数: {len(sub)}")
if sub.empty:
    print("  no daily data")
    raise SystemExit

weekly = daily_to_weekly(sub)
weekly = calculate_weekly_rps(weekly, period=120)
weekly = _add_weekly_indicators(weekly)
print(f"  周线行数: {len(weekly)}")

target_dt = pd.Timestamp(DATE)
valid = weekly["trade_date"].unique()
valid = valid[valid <= target_dt]
target_w = pd.Timestamp(valid.max())
print(f"  对齐目标周: {target_w.date()}")

cfg = HuocheConfig()
sdf = weekly[weekly["ts_code"] == CODE]
rec = _compute_signals_for_code(sdf, target_w, cfg)
if rec is None:
    print("  rec=None (周线数据不够)")
else:
    for k, v in rec.items():
        print(f"  {k}: {v}")

# 4. 最近 12 周关键周线指标
print("\n[4] 最近 12 周周线 (W_RPS_120 / MA_20W / MA_60W / MA_120W):")
last12 = sdf.tail(12)[["trade_date", "close", "high", "low", "W_RPS_120",
                        "MA_10W", "MA_20W", "MA_60W", "MA_120W"]].copy()
last12["trade_date"] = last12["trade_date"].dt.strftime("%Y-%m-%d")
print(last12.to_string(index=False, float_format="%.2f"))

# 5. W_SXHCG3 子条件分解
print("\n[5] W_SXHCG3 失败拆解:")
import numpy as np
window = sdf[sdf["trade_date"] <= target_w]
tail = window.tail(cfg.drawdown_lookback_w)
highs = tail["high"].values
lows = tail["low"].values
new_high_idx = int(np.argmax(highs))
new_high_price = highs[new_high_idx]
new_low_price = (np.min(lows[new_high_idx:]) if new_high_idx < len(highs) - 1 else lows[-1])
drawdown = (new_high_price - new_low_price) / new_high_price if new_high_price > 0 else 0

today = window.iloc[-1]
full = window.tail(cfg.near_high_period_w)
hhv_close = full["close"].max()
near = today["close"] / hhv_close if hhv_close > 0 else 0

print(f"  回撤窗口 {cfg.drawdown_lookback_w} 周")
print(f"    窗口高点周 idx={new_high_idx}/{len(tail)-1} (从 0 起算)")
print(f"    周线高点价格: {new_high_price:.2f}  (周 {tail.iloc[new_high_idx]['trade_date'].date()})")
print(f"    高点之后最低价: {new_low_price:.2f}")
print(f"    回撤: {drawdown:.2%}  阈值: ≤ {cfg.max_drawdown_pct:.0%}  -> {'PASS' if drawdown<=cfg.max_drawdown_pct else 'FAIL'}")
print(f"  近 {cfg.near_high_period_w} 周新高对比")
print(f"    HHV(close) = {hhv_close:.2f}  当前收盘={today['close']:.2f}  比值={near:.2%}  阈值: > {cfg.near_high_ratio:.0%}  -> {'PASS' if near>cfg.near_high_ratio else 'FAIL'}")

print("\n[6] 最近 20 周 high/low 列表 (找回撤源头):")
print(tail[["trade_date", "high", "low", "close"]].assign(
    trade_date=lambda d: d["trade_date"].dt.strftime("%Y-%m-%d")
).to_string(index=False, float_format="%.2f"))
