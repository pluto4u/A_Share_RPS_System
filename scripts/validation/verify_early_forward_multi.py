"""
================================================================================
早期启涨 跨多窗口 前瞻验证
================================================================================
对 N 个 (early, confirm) 窗口分别跑早期启涨 + 远期收益 + 与市场对比。
跨不同市场环境验证 "早期 ∩ L1 = 高胜率" 结论是否稳健。

L1 代理 (避免每个窗口重跑 6 分钟全市场 L1 筛选):
    RPS_120 + RPS_250 >= 180 且 close > MA20  (= TDX SXHCG1 + 部分 SXHCG2)

输出:
    跨窗口对比表 (mean / median / win_rate / >+10% / 超额)
    各窗口明细 csv

用法:
    py -3 verify_early_forward_multi.py
    py -3 verify_early_forward_multi.py --hold-days 10 --windows 5
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import time
import pandas as pd
import numpy as np
from pathlib import Path

from rps_analyzer.early_launch_screener import (
    screen_early_launch, EarlyLaunchConfig,
)

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def compute_l1_proxy(df: pd.DataFrame, target_dt: pd.Timestamp,
                      rps_sum_min: float = 180.0) -> set:
    """L1 代理: target_dt 当日 RPS_120+RPS_250 >= 180 且 close > MA20"""
    sub = df[df["trade_date"] == target_dt].copy()
    if sub.empty:
        return set()
    g = df[df["trade_date"] <= target_dt].groupby("ts_code")
    ma20 = g["close"].apply(lambda x: x.tail(20).mean() if len(x) >= 20 else np.nan)
    sub = sub.set_index("ts_code")
    sub["MA20"] = ma20
    cond = (
        (sub["RPS_120"] + sub["RPS_250"] >= rps_sum_min)
        & (sub["close"] > sub["MA20"])
        & sub["RPS_120"].notna() & sub["RPS_250"].notna()
    )
    return set(sub[cond].index)


def compute_l1_proxy_fast(df: pd.DataFrame, target_dt: pd.Timestamp,
                           rps_sum_min: float = 180.0) -> set:
    """更快版本: 只对当日有数据且 RPS 通过的票算 MA20"""
    today = df[df["trade_date"] == target_dt]
    if today.empty:
        return set()
    cand = today[(today["RPS_120"] + today["RPS_250"] >= rps_sum_min)
                 & today["RPS_120"].notna() & today["RPS_250"].notna()]
    if cand.empty:
        return set()
    cand_codes = cand["ts_code"].unique()
    sub = df[df["ts_code"].isin(cand_codes) & (df["trade_date"] <= target_dt)]
    ma20 = (
        sub.groupby("ts_code")["close"]
        .apply(lambda x: x.tail(20).mean() if len(x) >= 20 else np.nan)
    )
    cand = cand.set_index("ts_code")
    cand["MA20"] = ma20
    return set(cand[cand["close"] > cand["MA20"]].index)


def forward_returns(df: pd.DataFrame, codes, start_dt, end_dt) -> pd.DataFrame:
    sub = df[df["ts_code"].isin(codes) & df["trade_date"].between(start_dt, end_dt)].copy()
    rows = []
    for code, sdf in sub.groupby("ts_code"):
        sdf = sdf.sort_values("trade_date")
        if len(sdf) < 2:
            continue
        s_close = sdf.iloc[0]["close"]
        e_close = sdf.iloc[-1]["close"]
        rows.append({
            "ts_code": code,
            "fwd_ret": e_close / s_close - 1,
            "max_ret": sdf["high"].max() / s_close - 1,
        })
    return pd.DataFrame(rows)


def stats_block(sub: pd.DataFrame, label: str) -> dict:
    n = len(sub)
    if n == 0:
        return {"label": label, "n": 0}
    return {
        "label": label,
        "n": n,
        "mean%": round(sub["fwd_ret"].mean() * 100, 2),
        "med%": round(sub["fwd_ret"].median() * 100, 2),
        "win%": round((sub["fwd_ret"] > 0).mean() * 100, 1),
        ">+10%": round((sub["fwd_ret"] > 0.10).mean() * 100, 1),
        "max_med%": round(sub["max_ret"].median() * 100, 2),
    }


def run_one_window(df: pd.DataFrame, early_dt, confirm_dt) -> dict:
    early_str = early_dt.strftime("%Y%m%d")
    confirm_str = confirm_dt.strftime("%Y%m%d")
    print(f"\n窗口 {early_dt.date()} → {confirm_dt.date()}  (持有 {(confirm_dt-early_dt).days} 自然日)")
    print("-" * 78)

    t0 = time.time()
    A_df = screen_early_launch(df, target_date=early_str,
                                config=EarlyLaunchConfig(), verbose=False)
    A = set(A_df["ts_code"]) if not A_df.empty else set()
    print(f"  早期启涨 A: {len(A):4d} 只  (扫描耗时 {time.time()-t0:.1f}s)")

    t1 = time.time()
    L1_proxy = compute_l1_proxy_fast(df, confirm_dt)
    print(f"  L1 代理 (confirm 日) : {len(L1_proxy):4d} 只  ({time.time()-t1:.1f}s)")

    fwd_market = forward_returns(df, df["ts_code"].unique(), early_dt, confirm_dt)
    fwd_A = fwd_market[fwd_market["ts_code"].isin(A)]
    fwd_AL1 = fwd_market[fwd_market["ts_code"].isin(A & L1_proxy)]
    fwd_A_only = fwd_market[fwd_market["ts_code"].isin(A - L1_proxy)]

    rows = [
        {"window": f"{early_str}→{confirm_str}", **stats_block(fwd_market, "[基准] 全市场")},
        {"window": f"{early_str}→{confirm_str}", **stats_block(fwd_A, "全 A")},
        {"window": f"{early_str}→{confirm_str}", **stats_block(fwd_AL1, "  ∩ L1代理")},
        {"window": f"{early_str}→{confirm_str}", **stats_block(fwd_A_only, "  - L1代理")},
    ]
    print("  收益分组:")
    for r in rows:
        if r.get("n", 0) == 0:
            print(f"    {r['label']:<18}  n=0")
            continue
        print(f"    {r['label']:<18}  n={r['n']:4d}  mean={r['mean%']:6.2f}%  "
              f"med={r['med%']:6.2f}%  win={r['win%']:5.1f}%  >+10%={r['>+10%']:5.1f}%")

    return {"window_rows": rows, "A": A, "L1": L1_proxy, "fwd": fwd_market}


def find_trading_dates(df: pd.DataFrame, anchors: list) -> list:
    """对每个锚定日期，找 ≤ 它的最近交易日"""
    all_dates = np.array(sorted(df["trade_date"].unique()))
    out = []
    for a in anchors:
        a_ts = pd.Timestamp(a)
        idx = np.searchsorted(all_dates, a_ts, side="right") - 1
        if 0 <= idx < len(all_dates):
            out.append(pd.Timestamp(all_dates[idx]))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-days", type=int, default=10,
                        help="持有交易日 (early -> confirm 间隔)")
    parser.add_argument("--end", default="20260417", help="最新窗口的 confirm 日")
    parser.add_argument("--windows", type=int, default=5, help="窗口数")
    parser.add_argument("--gap-days", type=int, default=10, help="窗口间隔 (交易日)")
    args = parser.parse_args()

    df = load_data()
    all_dates = sorted(df["trade_date"].unique())
    end_dt = pd.Timestamp(args.end)
    end_idx = all_dates.index(end_dt) if end_dt in all_dates else len(all_dates) - 1

    confirm_idxs = []
    for i in range(args.windows):
        idx = end_idx - i * args.gap_days
        if idx >= args.hold_days:
            confirm_idxs.append(idx)
    confirm_idxs = sorted(set(confirm_idxs))

    pairs = []
    for ci in confirm_idxs:
        confirm_dt = all_dates[ci]
        early_dt = all_dates[ci - args.hold_days]
        pairs.append((early_dt, confirm_dt))

    print("=" * 78)
    print(f"跨窗口验证 ({len(pairs)} 个窗口, 持有 {args.hold_days} 个交易日)")
    print("=" * 78)
    for e, c in pairs:
        print(f"  {e.date()} → {c.date()}")

    all_rows = []
    per_window = []
    for early_dt, confirm_dt in pairs:
        res = run_one_window(df, early_dt, confirm_dt)
        all_rows.extend(res["window_rows"])
        per_window.append(res)

    # ========================================================================
    # 跨窗口聚合
    # ========================================================================
    print("\n" + "=" * 78)
    print("跨窗口对比 (含基准超额)")
    print("=" * 78)
    summary = pd.DataFrame(all_rows)
    print(summary.to_string(index=False))

    # 按 label 聚合
    print("\n" + "=" * 78)
    print("按分组聚合 (跨所有窗口)")
    print("=" * 78)
    agg_rows = []
    for label in ["[基准] 全市场", "全 A", "  ∩ L1代理", "  - L1代理"]:
        sub = summary[summary["label"] == label]
        if sub.empty or sub["n"].sum() == 0:
            continue
        agg_rows.append({
            "label": label,
            "windows": (sub["n"] > 0).sum(),
            "total_n": sub["n"].sum(),
            "avg_mean%": round(sub["mean%"].mean(), 2),
            "avg_med%": round(sub["med%"].mean(), 2),
            "avg_win%": round(sub["win%"].mean(), 1),
            "avg_>10%": round(sub[">+10%"].mean(), 1),
        })
    agg = pd.DataFrame(agg_rows)
    print(agg.to_string(index=False))

    # 超额: A∩L1 vs 基准
    print("\n" + "=" * 78)
    print("[★关键] 早期∩L1 vs 全市场基准 — 各窗口超额")
    print("=" * 78)
    bench = summary[summary["label"] == "[基准] 全市场"][["window", "med%", "win%"]].rename(
        columns={"med%": "bench_med%", "win%": "bench_win%"})
    al1 = summary[summary["label"] == "  ∩ L1代理"][["window", "n", "med%", "win%"]].rename(
        columns={"med%": "al1_med%", "win%": "al1_win%", "n": "AL1_n"})
    cmp = al1.merge(bench, on="window")
    cmp["med_alpha"] = (cmp["al1_med%"] - cmp["bench_med%"]).round(2)
    cmp["win_alpha"] = (cmp["al1_win%"] - cmp["bench_win%"]).round(1)
    print(cmp.to_string(index=False))
    print(f"\n  med_alpha 平均: {cmp['med_alpha'].mean():+.2f}pp")
    print(f"  win_alpha 平均: {cmp['win_alpha'].mean():+.1f}pp")
    print(f"  win_alpha > 0 的窗口数: {(cmp['win_alpha'] > 0).sum()}/{len(cmp)}")

    out = OUTPUT_DIR / f"early_forward_multi_{args.end}_h{args.hold_days}_w{args.windows}.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  明细: {out}")


if __name__ == "__main__":
    main()
