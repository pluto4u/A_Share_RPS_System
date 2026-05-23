"""
================================================================================
火车轨入选股票 量价节奏剖析 — 找"提前识别"信号
================================================================================
对 huoche.csv 中入选的股票，回看 60-120 个交易日的量价行为，提取：
  - 启涨日 (close 站上 MA20 之后开始持续向上的第一天)
  - 启涨距今 (越大说明信号越滞后)
  - 启涨时 vs 入选时累计涨幅
  - 启涨日相对 60 日均量的放量倍数
  - 启涨前 N 日的盘整/收敛特征 (波动收敛 + 缩量)
  - MA20 / MA60 多头排列的形成日

目的: 找出"早期识别"特征 — 是否能在启涨第 3-5 天就发现，而非等火车轨亮灯。
"""

import argparse
import pandas as pd
import numpy as np
from pathlib import Path

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def load_data() -> pd.DataFrame:
    if not CACHE_PATH.exists():
        raise FileNotFoundError(f"缓存不存在: {CACHE_PATH} — 先跑 run_huoche_sweep.py")
    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def add_indicators(sdf: pd.DataFrame) -> pd.DataFrame:
    sdf = sdf.sort_values("trade_date").reset_index(drop=True).copy()
    sdf["MA5"] = sdf["close"].rolling(5).mean()
    sdf["MA10"] = sdf["close"].rolling(10).mean()
    sdf["MA20"] = sdf["close"].rolling(20).mean()
    sdf["MA60"] = sdf["close"].rolling(60).mean()
    sdf["MA120"] = sdf["close"].rolling(120).mean()
    sdf["VOL_MA5"] = sdf["vol"].rolling(5).mean()
    sdf["VOL_MA60"] = sdf["vol"].rolling(60).mean()
    sdf["pct"] = sdf["close"].pct_change()
    sdf["amplitude"] = (sdf["high"] - sdf["low"]) / sdf["close"].shift(1)
    return sdf


def find_launch_day(sdf: pd.DataFrame, end_idx: int, lookback: int = 120) -> int:
    """
    启涨日定义：在 end_idx 之前最近一段连续 (close > MA20) 区间的起点。
    若 end_idx 当前已不在该区间内，则取最近一段满足条件的最后区间起点。
    返回索引；找不到返回 -1。
    """
    start = max(0, end_idx - lookback)
    seg = sdf.iloc[start:end_idx + 1]
    above = (seg["close"] > seg["MA20"]).values
    if not above.any():
        return -1
    # 找最后一段 True 区间的起点
    last_run_start = None
    in_run = False
    for i, v in enumerate(above):
        if v and not in_run:
            last_run_start = i
            in_run = True
        elif not v:
            in_run = False
    if last_run_start is None:
        return -1
    return start + last_run_start


def find_breakout_day(sdf: pd.DataFrame, end_idx: int, lookback: int = 60) -> int:
    """
    放量突破日定义：当日 vol >= 1.5*VOL_MA60 且 close 涨幅 >= 4% 且 收 > MA20。
    取窗口内最近一次。
    """
    start = max(60, end_idx - lookback)
    seg = sdf.iloc[start:end_idx + 1]
    cond = (
        (seg["vol"] >= 1.5 * seg["VOL_MA60"])
        & (seg["pct"] >= 0.04)
        & (seg["close"] > seg["MA20"])
    )
    idxs = seg.index[cond]
    if len(idxs) == 0:
        return -1
    return int(idxs[-1])


def analyze_one(sdf: pd.DataFrame, code: str, target_dt: pd.Timestamp) -> dict:
    sdf = add_indicators(sdf)
    sdf = sdf[sdf["trade_date"] <= target_dt].reset_index(drop=True)
    if len(sdf) < 130:
        return {"ts_code": code, "error": "不足 130 日"}

    end_idx = len(sdf) - 1
    today = sdf.iloc[end_idx]

    # 启涨日
    launch_idx = find_launch_day(sdf, end_idx, lookback=120)
    breakout_idx = find_breakout_day(sdf, end_idx, lookback=120)

    if launch_idx >= 0:
        launch = sdf.iloc[launch_idx]
        launch_days_ago = end_idx - launch_idx
        launch_ret = today["close"] / launch["close"] - 1
        launch_vol_x = launch["vol"] / launch["VOL_MA60"] if launch["VOL_MA60"] > 0 else np.nan
        launch_date = launch["trade_date"].strftime("%m-%d")
        launch_pct = launch["pct"]
    else:
        launch_days_ago = -1
        launch_ret = np.nan
        launch_vol_x = np.nan
        launch_date = "N/A"
        launch_pct = np.nan

    if breakout_idx >= 0:
        bo = sdf.iloc[breakout_idx]
        bo_days_ago = end_idx - breakout_idx
        bo_ret = today["close"] / bo["close"] - 1
        bo_date = bo["trade_date"].strftime("%m-%d")
        bo_vol_x = bo["vol"] / bo["VOL_MA60"]
    else:
        bo_days_ago = -1
        bo_ret = np.nan
        bo_date = "N/A"
        bo_vol_x = np.nan

    # 启涨前 20 日 收敛/缩量 特征
    if launch_idx >= 20:
        pre = sdf.iloc[launch_idx - 20:launch_idx]
        pre_amp = pre["amplitude"].mean()
        pre_vol_ratio = pre["vol"].mean() / sdf.iloc[max(0, launch_idx - 60):launch_idx]["vol"].mean()
        pre_range = (pre["high"].max() / pre["low"].min() - 1)
    else:
        pre_amp = pre_vol_ratio = pre_range = np.nan

    # 多头排列形成日 (MA5>MA10>MA20>MA60 持续 N 日)
    bullish_idx = -1
    for i in range(end_idx, max(60, end_idx - 120), -1):
        row = sdf.iloc[i]
        if (row["MA5"] > row["MA10"] > row["MA20"] > row["MA60"]):
            bullish_idx = i
        else:
            if bullish_idx > 0:
                break
    bullish_days_ago = end_idx - bullish_idx if bullish_idx > 0 else -1

    # 近 5/10/20 日表现
    def ret(n):
        if end_idx >= n:
            return today["close"] / sdf.iloc[end_idx - n]["close"] - 1
        return np.nan

    return {
        "ts_code": code,
        "close": today["close"],
        "ret_5d": ret(5),
        "ret_10d": ret(10),
        "ret_20d": ret(20),
        "ret_60d": ret(60),
        "launch_date": launch_date,
        "launch_days_ago": launch_days_ago,
        "launch_ret": launch_ret,
        "launch_vol_x": launch_vol_x,
        "launch_pct": launch_pct,
        "breakout_date": bo_date,
        "breakout_days_ago": bo_days_ago,
        "breakout_ret": bo_ret,
        "breakout_vol_x": bo_vol_x,
        "pre_amp": pre_amp,
        "pre_vol_ratio": pre_vol_ratio,
        "pre_range": pre_range,
        "bullish_days_ago": bullish_days_ago,
        "vs_MA20_pct": (today["close"] / today["MA20"] - 1) if today["MA20"] > 0 else np.nan,
        "vs_MA60_pct": (today["close"] / today["MA60"] - 1) if today["MA60"] > 0 else np.nan,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(OUTPUT_DIR / "tdx_screener_20260417_huoche.csv"))
    parser.add_argument("--date", default="20260417")
    args = parser.parse_args()

    target_dt = pd.Timestamp(args.date)
    picks = pd.read_csv(args.csv)
    codes = picks["ts_code"].tolist()
    print(f"分析 {len(codes)} 只入选股票，目标日 {args.date}\n")

    df = load_data()
    rows = []
    for code in codes:
        sdf = df[df["ts_code"] == code]
        if sdf.empty:
            print(f"  [skip] {code} 无数据")
            continue
        rec = analyze_one(sdf, code, target_dt)
        rows.append(rec)

    out = pd.DataFrame(rows)
    if "error" in out.columns:
        out = out[out["error"].isna()] if out["error"].notna().any() else out.drop(columns=["error"], errors="ignore")
    out = out.merge(picks[["ts_code", "HUOCHE_FIRST_W"]], on="ts_code", how="left")

    # ---------------- 显示 ----------------
    show = out[[
        "ts_code", "HUOCHE_FIRST_W", "close",
        "ret_20d", "ret_60d",
        "launch_date", "launch_days_ago", "launch_ret", "launch_vol_x",
        "breakout_date", "breakout_days_ago", "breakout_vol_x",
        "vs_MA20_pct", "vs_MA60_pct",
        "pre_vol_ratio", "pre_range",
    ]].copy()
    for c in ["ret_20d", "ret_60d", "launch_ret", "vs_MA20_pct", "vs_MA60_pct", "pre_range"]:
        show[c] = (show[c] * 100).round(1)
    for c in ["launch_vol_x", "breakout_vol_x", "pre_vol_ratio"]:
        show[c] = show[c].round(2)
    show = show.sort_values("launch_days_ago", ascending=False)

    print("=" * 120)
    print("个股量价节奏 (按启涨距今天数 降序)")
    print("=" * 120)
    print(show.to_string(index=False))

    # ---------------- 汇总统计 ----------------
    print("\n" + "=" * 120)
    print("分布统计")
    print("=" * 120)
    desc = out[[
        "ret_20d", "ret_60d",
        "launch_days_ago", "launch_ret", "launch_vol_x",
        "breakout_days_ago", "breakout_vol_x",
        "vs_MA20_pct", "vs_MA60_pct",
        "pre_vol_ratio", "pre_range", "bullish_days_ago",
    ]].describe(percentiles=[0.25, 0.5, 0.75]).T
    print(desc.round(3).to_string())

    # ---------------- 关键发现 ----------------
    print("\n" + "=" * 120)
    print("关键发现 (用于 提前识别 特征工程)")
    print("=" * 120)
    p50_launch = out["launch_days_ago"].median()
    p50_bo = out["breakout_days_ago"].median()
    p50_launch_ret = out["launch_ret"].median() * 100
    p50_bo_vol = out["breakout_vol_x"].median()
    p50_vs60 = out["vs_MA60_pct"].median() * 100
    p50_pre_range = out["pre_range"].median() * 100
    print(f"  - 启涨距今中位数: {p50_launch:.0f} 个交易日")
    print(f"  - 放量突破距今中位数: {p50_bo:.0f} 个交易日")
    print(f"  - 启涨至今累计涨幅中位数: {p50_launch_ret:.1f}%")
    print(f"  - 突破日放量倍数中位数: {p50_bo_vol:.2f}x")
    print(f"  - 当前价 vs MA60 偏离中位数: {p50_vs60:.1f}%")
    print(f"  - 启涨前 20 日震荡区间中位数: {p50_pre_range:.1f}%")

    out_path = OUTPUT_DIR / f"huoche_picks_anatomy_{args.date}.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n  明细已保存: {out_path}")


if __name__ == "__main__":
    main()
