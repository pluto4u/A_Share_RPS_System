"""
================================================================================
早期∩L1 多持有期对比 — 找最优 hold_days
================================================================================
固定 5 个 early_date 锚点，对每个锚点算一次 A (早期启涨集合);
然后对 hold_days ∈ {5, 10, 15, 20} 分别计算 fwd_ret 与基准超额。

每个 early_date 的 A 不依赖 hold_days, 一次计算复用 — 比 multi 脚本更高效.

用法:
    py -3 verify_holding_periods.py
    py -3 verify_holding_periods.py --end 20260417 --windows 5 --gap-days 10
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
from verify_early_forward_multi import (
    compute_l1_proxy_fast, forward_returns,
)

OUTPUT_DIR = Path("output")
CACHE_PATH = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def stats(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "mean%": np.nan, "med%": np.nan, "win%": np.nan,
                ">+10%": np.nan, "max_med%": np.nan}
    return {
        "n": n,
        "mean%": round(sub["fwd_ret"].mean() * 100, 2),
        "med%": round(sub["fwd_ret"].median() * 100, 2),
        "win%": round((sub["fwd_ret"] > 0).mean() * 100, 1),
        ">+10%": round((sub["fwd_ret"] > 0.10).mean() * 100, 1),
        "max_med%": round(sub["max_ret"].median() * 100, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="20260417")
    parser.add_argument("--windows", type=int, default=5)
    parser.add_argument("--gap-days", type=int, default=10)
    parser.add_argument("--hold-days", nargs="+", type=int, default=[5, 10, 15, 20])
    args = parser.parse_args()

    df = pd.read_parquet(CACHE_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    all_dates = sorted(df["trade_date"].unique())
    end_dt = pd.Timestamp(args.end)
    end_idx = all_dates.index(end_dt) if end_dt in all_dates else len(all_dates) - 1

    max_hold = max(args.hold_days)
    # early 锚点：从 end_idx - max_hold 倒推 (确保 confirm 不会越界)
    early_idxs = []
    for i in range(args.windows):
        idx = end_idx - max_hold - i * args.gap_days
        if idx >= 100:
            early_idxs.append(idx)
    early_idxs = sorted(set(early_idxs))
    early_dates = [all_dates[i] for i in early_idxs]

    print("=" * 80)
    print(f"多持有期验证: {len(early_dates)} 个早期锚点 × {args.hold_days} 持有期")
    print("=" * 80)
    for ed in early_dates:
        print(f"  early_date: {ed.date()}  ", end="")
        confs = [all_dates[all_dates.index(ed) + h] for h in args.hold_days
                 if all_dates.index(ed) + h < len(all_dates)]
        print("→ confirm:", ", ".join([str(c.date()) for c in confs]))

    rows = []
    for ed in early_dates:
        print(f"\n[{ed.date()}] 跑早期启涨 ...", end=" ", flush=True)
        t0 = time.time()
        A_df = screen_early_launch(df, target_date=ed.strftime("%Y%m%d"),
                                    config=EarlyLaunchConfig(), verbose=False)
        A = set(A_df["ts_code"]) if not A_df.empty else set()
        print(f"|A|={len(A)} ({time.time()-t0:.1f}s)")

        ed_idx = all_dates.index(ed)
        for h in args.hold_days:
            ci = ed_idx + h
            if ci >= len(all_dates):
                continue
            confirm_dt = all_dates[ci]

            L1 = compute_l1_proxy_fast(df, confirm_dt)
            fwd_market = forward_returns(df, df["ts_code"].unique(), ed, confirm_dt)
            fwd_A = fwd_market[fwd_market["ts_code"].isin(A)]
            fwd_AL1 = fwd_market[fwd_market["ts_code"].isin(A & L1)]
            fwd_market_only = fwd_market

            for label, sub in [
                ("[基准] 全市场", fwd_market_only),
                ("全 A", fwd_A),
                ("∩ L1", fwd_AL1),
            ]:
                rows.append({
                    "early": ed.strftime("%Y%m%d"),
                    "hold": h,
                    "confirm": confirm_dt.strftime("%Y%m%d"),
                    "label": label,
                    **stats(sub),
                })
            print(f"  hold={h:2d}  A={len(fwd_A):3d}  AL1={len(fwd_AL1):3d}", end=" | ")
        print()

    summary = pd.DataFrame(rows)

    # ========================================================================
    # 按 (hold, label) 聚合 — 这是核心对比
    # ========================================================================
    print("\n" + "=" * 80)
    print("按持有期聚合 (跨所有 early 锚点平均)")
    print("=" * 80)
    agg = summary.groupby(["hold", "label"]).agg(
        windows=("n", lambda s: (s > 0).sum()),
        total_n=("n", "sum"),
        avg_mean=("mean%", "mean"),
        avg_med=("med%", "mean"),
        avg_win=("win%", "mean"),
        avg_gt10=(">+10%", "mean"),
        avg_max_med=("max_med%", "mean"),
    ).round(2).reset_index()
    print(agg.to_string(index=False))

    # ========================================================================
    # ∩ L1 vs 基准 超额 — 按持有期
    # ========================================================================
    print("\n" + "=" * 80)
    print("[★] ∩ L1 vs 基准 超额 — 按持有期")
    print("=" * 80)
    bench = agg[agg["label"] == "[基准] 全市场"].set_index("hold")[["avg_med", "avg_win"]]
    al1 = agg[agg["label"] == "∩ L1"].set_index("hold")
    cmp = al1[["total_n", "avg_med", "avg_win", "avg_gt10", "avg_max_med"]].copy()
    cmp["med_alpha"] = (cmp["avg_med"] - bench["avg_med"]).round(2)
    cmp["win_alpha"] = (cmp["avg_win"] - bench["avg_win"]).round(1)
    print(cmp.to_string())

    # ========================================================================
    # 按 hold × early 明细 (显示稳健性)
    # ========================================================================
    print("\n" + "=" * 80)
    print("∩ L1 各 (early, hold) 明细 (找跨持有期一致性)")
    print("=" * 80)
    al1_detail = summary[summary["label"] == "∩ L1"][
        ["early", "hold", "confirm", "n", "med%", "win%", ">+10%"]
    ].sort_values(["early", "hold"])
    print(al1_detail.to_string(index=False))

    # ========================================================================
    # 持仓决策启发: 最大回撤路径 / 利润提取窗口
    # ========================================================================
    print("\n" + "=" * 80)
    print("利润提取观察: max_ret 中位数 vs 期末 ret 中位数 (按持有期)")
    print("=" * 80)
    print("  说明: max_med 显著高于 med 表示持有过程中曾出现更高点位 -> 可考虑提前止盈")
    for h in args.hold_days:
        sub = summary[(summary["label"] == "∩ L1") & (summary["hold"] == h)]
        if sub.empty:
            continue
        avg_med = sub["med%"].mean()
        avg_max = sub["max_med%"].mean()
        gap = avg_max - avg_med
        print(f"  hold={h:2d}  期末 med={avg_med:6.2f}%  期内峰值 med={avg_max:6.2f}%  差额={gap:+.2f}pp")

    out = OUTPUT_DIR / f"holding_periods_{args.end}.csv"
    summary.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  明细: {out}")


if __name__ == "__main__":
    main()
