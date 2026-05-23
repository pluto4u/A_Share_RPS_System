"""完全按 SOP §2.1 全部 6 条硬阈值过滤持续性榜，确认核心槽候选"""
import pandas as pd

target = pd.Timestamp("2026-04-30")

# 阈值 (来自 docs/strategy_daily_sop.md §2.1)
TH_SUPER = 0.45
TH_L1 = 0.60
TH_LAYERS = 3
TH_VS60 = 0.50
TH_RET20 = 0.50

pers = pd.read_csv("output/persistence_20260401_20260430.csv")
df = pd.read_parquet("output/cache_tdx_with_rps.parquet")
df["trade_date"] = pd.to_datetime(df["trade_date"])

# 持续性硬筛
pers_pass = pers[
    (pers["super_rate"] >= TH_SUPER)
    & (pers["l1_rate"] >= TH_L1)
    & (pers["layers_covered"] >= TH_LAYERS)
].copy()
print(f"持续性榜 515 只 -> 满足 super>=0.45 & l1>=0.60 & layers>=3: {len(pers_pass)}")

codes = pers_pass["ts_code"].tolist()
sub = df[df["ts_code"].isin(codes)].copy().sort_values(["ts_code", "trade_date"])
g = sub.groupby("ts_code", sort=False)
sub["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
sub["MA60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).mean())
sub["ret20"] = g["close"].transform(lambda x: x / x.shift(20) - 1)

today = sub[sub["trade_date"] == target].copy()
today["above_ma20"] = today["close"] > today["MA20"]
today["vs60"] = today["close"] / today["MA60"] - 1
today["pass_ma20"] = today["above_ma20"]
today["pass_vs60"] = today["vs60"] <= TH_VS60
today["pass_ret20"] = today["ret20"] <= TH_RET20
today["pass_form"] = today["pass_ma20"] & today["pass_vs60"] & today["pass_ret20"]

merged = pers_pass.merge(today[["ts_code", "close", "MA20", "MA60", "vs60",
                                 "ret20", "pass_ma20", "pass_vs60", "pass_ret20",
                                 "pass_form"]], on="ts_code", how="left")
merged = merged.sort_values("score", ascending=False)

print(f"\n持续性 OK 的 {len(merged)} 只 4/30 形态检查:\n")
print(f"{'代码':<12}{'close':>8}{'MA20':>9}{'vs60':>8}{'ret20':>8}{'super':>7}{'l1':>5}{'lay':>4}  形态")
for _, r in merged.iterrows():
    state = "★全过" if r["pass_form"] else (
        "✗ close<MA20" if not r["pass_ma20"] else
        "✗ vs60>50%" if not r["pass_vs60"] else
        "✗ ret20>50%" if not r["pass_ret20"] else "?"
    )
    print(f"{r['ts_code']:<12}{r['close']:>8.2f}{r['MA20']:>9.2f}"
          f"{r['vs60']:>7.0%}{r['ret20']:>7.0%}"
          f"{r['super_rate']:>7.0%}{r['l1_rate']:>5.0%}{int(r['layers_covered']):>4}  {state}")

print(f"\n=== 完全过 SOP §2.1 全 6 条阈值的核心槽候选 ===")
core = merged[merged["pass_form"]]
for _, r in core.iterrows():
    print(f"  {r['ts_code']:<12} super={r['super_rate']:.0%} l1={r['l1_rate']:.0%} layers={int(r['layers_covered'])}")
print(f"\n总数: {len(core)} 只")
