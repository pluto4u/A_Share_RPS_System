"""
诊断持续性榜入选票的形态对比（4 月窗口默认）。

用法:
    py -3 .\\tools\\diagnose_picks.py
    py -3 .\\tools\\diagnose_picks.py --codes 002636.SZ,300308.SZ,001270.SZ --start 20260401 --end 20260430
    py -3 .\\tools\\diagnose_picks.py --refresh   # 重读最新 TDX 数据并更新缓存

依赖 output\\cache_tdx_with_rps.parquet (持续性扫描 / huoche_sweep 生成)。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CACHE = Path("output/cache_tdx_with_rps.parquet")

DEFAULT_CODES = ["002636.SZ", "300308.SZ", "001270.SZ"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--codes", default=",".join(DEFAULT_CODES))
    p.add_argument("--start", default="20260401")
    p.add_argument("--end", default="20260430")
    p.add_argument("--lookback", type=int, default=60, help="窗口前 N 个交易日做基线对比")
    p.add_argument("--refresh", action="store_true", help="重读 TDX 最新数据 + 重算 RPS, 更新缓存")
    p.add_argument("--tdx-dir", default=None, help="TDX 数据目录 (refresh 模式时使用)")
    p.add_argument("--min-days", type=int, default=250)
    return p.parse_args()


def refresh_cache(args) -> pd.DataFrame:
    from rps_analyzer.tdx_reader import load_tdx_market
    from rps_analyzer.rps_calculator import calculate_rps

    print("[refresh] 重新加载 TDX 全市场 ...")
    t0 = time.time()
    if args.tdx_dir:
        df = load_tdx_market(args.tdx_dir, min_days=args.min_days)
    else:
        df = load_tdx_market(min_days=args.min_days)
    print(f"  读取 {time.time()-t0:.1f}s, {len(df):,} 行")

    print("[refresh] 计算 RPS_120 / RPS_250 ...")
    t0 = time.time()
    df = calculate_rps(df, periods=[120, 250], verbose=False)
    print(f"  耗时 {time.time()-t0:.1f}s")

    CACHE.parent.mkdir(exist_ok=True)
    df.to_parquet(CACHE, index=False)
    print(f"[refresh] 已写入 {CACHE}")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def load_cache(args) -> pd.DataFrame:
    if args.refresh or not CACHE.exists():
        return refresh_cache(args)
    df = pd.read_parquet(CACHE)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def add_indicators(sub: pd.DataFrame) -> pd.DataFrame:
    """按单只票计算 MA / 涨幅 / 回撤等。"""
    sub = sub.sort_values("trade_date").copy()
    sub["MA20"] = sub["close"].rolling(20, min_periods=20).mean()
    sub["MA60"] = sub["close"].rolling(60, min_periods=60).mean()
    sub["MA120"] = sub["close"].rolling(120, min_periods=120).mean()
    sub["vs_ma60"] = sub["close"] / sub["MA60"] - 1
    sub["ret20"] = sub["close"] / sub["close"].shift(20) - 1
    sub["ret60"] = sub["close"] / sub["close"].shift(60) - 1
    sub["ret120"] = sub["close"] / sub["close"].shift(120) - 1
    return sub


def diagnose_one(df_all: pd.DataFrame, code: str, start: pd.Timestamp,
                 end: pd.Timestamp, lookback: int) -> None:
    sub = df_all[df_all["ts_code"] == code]
    if sub.empty:
        print(f"\n[{code}] 缓存中无数据，跳过")
        return

    sub = add_indicators(sub)
    win = sub[(sub["trade_date"] >= start) & (sub["trade_date"] <= end)].copy()
    if win.empty:
        print(f"\n[{code}] 窗口 {start.date()}~{end.date()} 内无数据")
        return

    last = sub.iloc[-1]
    first_w = win.iloc[0]
    last_w = win.iloc[-1]

    # 基线参考: 窗口开始前 lookback 天
    base_idx = sub.index[sub["trade_date"] < start]
    base = sub.loc[base_idx].tail(lookback) if len(base_idx) else pd.DataFrame()

    print("\n" + "=" * 78)
    print(f"诊断 {code}  窗口 {first_w['trade_date'].date()} -> {last_w['trade_date'].date()}  ({len(win)} 个交易日)")
    print("=" * 78)

    # —— A. 价格走势
    px_chg = last_w["close"] / first_w["close"] - 1
    win_high = win["high"].max()
    win_low = win["low"].min()
    drawdown = win_low / win_high - 1
    print(f"\n[A] 4 月价格走势")
    print(f"    起始收盘 {first_w['close']:.2f} → 末日收盘 {last_w['close']:.2f}  涨幅 {px_chg:+.2%}")
    print(f"    窗口区间 [{win_low:.2f}, {win_high:.2f}]   最大回撤 {drawdown:.2%}")

    # —— B. RPS 走势
    rps120_min = win["RPS_120"].min()
    rps120_max = win["RPS_120"].max()
    rps120_avg = win["RPS_120"].mean()
    rps250_min = win["RPS_250"].min()
    rps250_max = win["RPS_250"].max()
    rps250_avg = win["RPS_250"].mean()

    super_days = ((win["RPS_120"] >= 98) & (win["RPS_250"] >= 98)).sum()
    super_position_ok = (win["vs_ma60"] <= 0.60) & (win["ret20"] <= 0.60)
    super_pass_days = ((win["RPS_120"] >= 98) & (win["RPS_250"] >= 98) & super_position_ok).sum()

    print(f"\n[B] RPS 强度")
    print(f"    RPS_120  min={rps120_min:.0f}  avg={rps120_avg:.0f}  max={rps120_max:.0f}")
    print(f"    RPS_250  min={rps250_min:.0f}  avg={rps250_avg:.0f}  max={rps250_max:.0f}")
    print(f"    双 98 满足天数:        {super_days}/{len(win)}")
    print(f"    双 98 + 位置约束通过:  {super_pass_days}/{len(win)}   (= Super 持续性)")

    # —— C. 位置 / 段涨幅
    over_extended = (win["vs_ma60"] > 0.60).sum()
    over_ret20 = (win["ret20"] > 0.60).sum()
    print(f"\n[C] 是否过度延伸")
    print(f"    vs MA60   min={win['vs_ma60'].min():.0%}  avg={win['vs_ma60'].mean():.0%}  max={win['vs_ma60'].max():.0%}   超 60% 天数: {over_extended}")
    print(f"    20 日涨幅 min={win['ret20'].min():.0%}  avg={win['ret20'].mean():.0%}  max={win['ret20'].max():.0%}   超 60% 天数: {over_ret20}")

    # —— D. 末日体检（最新一天的形态）
    print(f"\n[D] {last['trade_date'].date()} 体检 (最新缓存日)")
    print(f"    收盘 {last['close']:.2f}   MA20 {last['MA20']:.2f}   MA60 {last['MA60']:.2f}   MA120 {last['MA120']:.2f}")
    print(f"    vs MA60 {last['vs_ma60']:+.1%}   20日涨幅 {last['ret20']:+.1%}   60日涨幅 {last['ret60']:+.1%}   120日涨幅 {last['ret120']:+.1%}")
    print(f"    RPS_120 {last['RPS_120']:.0f}   RPS_250 {last['RPS_250']:.0f}")
    if "amount" in last.index:
        print(f"    成交额 {last['amount']/1e8:.2f} 亿")

    # —— E. 4 月窗口前 60 天对比 (识别"启动期")
    if not base.empty:
        base_rps120 = base["RPS_120"].mean()
        base_close_first = base["close"].iloc[0]
        base_close_last = base["close"].iloc[-1]
        base_chg = base_close_last / base_close_first - 1
        print(f"\n[E] 启动前基线 (4月之前 {len(base)} 个交易日)")
        print(f"    收盘 {base_close_first:.2f} → {base_close_last:.2f}  涨幅 {base_chg:+.2%}")
        print(f"    RPS_120 平均 {base_rps120:.0f}  vs 4月平均 {rps120_avg:.0f}   △ {rps120_avg - base_rps120:+.0f}")

    # —— F. 节奏判定
    judge = []
    if super_pass_days >= len(win) * 0.6:
        judge.append("[OK ] Super 持续 >= 60% (核心持仓资格)")
    elif super_days >= len(win) * 0.6 and super_pass_days < len(win) * 0.4:
        judge.append("[WRN] 双 98 满足但位置已过度延伸 -> 不再适合追入")
    else:
        judge.append("[ -- ] Super 持续性不足")

    if not base.empty:
        if (rps120_avg - base_rps120) >= 15:
            judge.append("[OK ] RPS 显著加速 (启动期特征)")
        elif (rps120_avg - base_rps120) >= 5:
            judge.append("[ -- ] RPS 温和走强")
        else:
            judge.append("[ -- ] RPS 已在高位 (非新启动)")

    if last["close"] < last["MA60"]:
        judge.append("[BAD] 收盘已跌破 MA60 (SOP 硬卖出条件)")
    elif last["close"] < last["MA20"]:
        judge.append("[WRN] 收盘已跌破 MA20 (减半仓预警)")
    else:
        judge.append("[OK ] 仍在 MA20 之上")

    if last["vs_ma60"] > 0.60:
        judge.append("[BAD] 末日 vs MA60 > 60% (位置过高，不进 Super)")

    print(f"\n[F] 节奏判定")
    for j in judge:
        print(f"    {j}")


def main() -> None:
    args = parse_args()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    print(f"加载缓存 {CACHE} ...")
    df = load_cache(args)
    print(f"缓存行数 {len(df):,}   日期范围 {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
    if df['trade_date'].max() < end:
        print(f"[警告] 缓存最新日 {df['trade_date'].max().date()} 早于窗口末日 {end.date()},")
        print(f"       窗口数据不完整。如需完整 4 月窗口，请加 --refresh 重读 TDX 最新数据。")

    for code in codes:
        diagnose_one(df, code, start, end, args.lookback)

    print("\n" + "=" * 78)
    print("诊断结束。SOP 决策建议:")
    print("  - 全部判定为 [OK] 且 Super 持续 >= 60% -> 候选核心槽")
    print("  - 出现 [WRN] -> 仅候选机动槽 / 观察")
    print("  - 出现 [BAD] -> 不入选, 已持仓需考虑卖出")
    print("=" * 78)


if __name__ == "__main__":
    main()
