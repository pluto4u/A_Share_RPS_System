"""
================================================================================
静默筑底 · 低位再启动 → 前瞻验证 (Forward Returns)
================================================================================
核心实验:
    在历史某个观察日 date 跑静默筑底扫描 -> 得到 可买入(BUY) / 观察(WATCH) 两档
    统计每档在 +10 / +20 / +40 个交易日后的收益分布, 并与全市场同期基准对比

    回答 3 个问题:
    1. 可买入档相对全市场是否有正的超额收益 (策略是否成立)
    2. 观察档是否值得埋伏 (左侧档位的代价有多大)
    3. 哪些明细指标与远期收益相关 (地量比 / 箱体振幅 / RPS 抬升 / 放量比)

用法:
    py -3 verify_quiet_base.py --date 20260601
    py -3 verify_quiet_base.py --date 20260601 --windows 5,10,20,40
    py -3 verify_quiet_base.py --date 20260601 --cache output/cache_tdx_with_rps.parquet
    py -3 verify_quiet_base.py --dates 20260302,20260401,20260506   # 多期汇总

注意: 需要 date 之后至少 max(windows) 个交易日的数据, 否则该窗口会显示 n 不足。
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

from rps_analyzer.quiet_base_screener import QuietBaseConfig, screen_quiet_base
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market


OUTPUT_DIR = Path("output")
DEFAULT_CACHE = OUTPUT_DIR / "cache_tdx_with_rps.parquet"


def load_data(cache: Optional[str], tdx_dir: str, min_days: int) -> pd.DataFrame:
    cache_path = Path(cache) if cache else DEFAULT_CACHE
    if cache_path.exists():
        print(f"  从缓存读取: {cache_path}")
        df = (
            pd.read_parquet(cache_path)
            if cache_path.suffix == ".parquet"
            else pd.read_csv(cache_path)
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if "RPS_120" not in df.columns:
            df = calculate_rps(df, periods=[50, 120, 250], verbose=True)
        return df

    print(f"  缓存不存在({cache_path})，改读通达信本地日线 ...")
    df = load_tdx_market(tdx_dir, min_days=min_days)
    return calculate_rps(df, periods=[50, 120, 250], verbose=True)


def forward_returns(df: pd.DataFrame, codes: Sequence[str],
                    start_dt: pd.Timestamp, bars: int) -> pd.DataFrame:
    """
    计算 codes 中每只票从 start_dt 起 bars 个交易日的收益。
    fwd_ret = 第 bars 根 K 线收盘 / start_dt 收盘 - 1
    max_ret / min_ret = 期间最高 / 最低 相对 start_dt 收盘 (用于看盈亏比与回撤)
    """
    sub = df[df["ts_code"].isin(set(codes)) & (df["trade_date"] >= start_dt)]
    rows = []
    for code, sdf in sub.groupby("ts_code", sort=False):
        sdf = sdf.sort_values("trade_date")
        if len(sdf) < 2:
            continue
        s_close = float(sdf.iloc[0]["close"])
        if s_close <= 0:
            continue
        seg = sdf.iloc[: bars + 1]
        rows.append({
            "ts_code": code,
            "bars_avail": len(seg) - 1,
            "start_close": s_close,
            "end_close": float(seg.iloc[-1]["close"]),
            "fwd_ret": float(seg.iloc[-1]["close"]) / s_close - 1,
            "max_ret": float(seg["high"].max()) / s_close - 1,
            "min_ret": float(seg["low"].min()) / s_close - 1,
        })
    return pd.DataFrame(rows)


def _stats_line(sub: pd.DataFrame, label: str) -> None:
    if sub.empty:
        print(f"  {label:<26}  n=  0")
        return
    mean = sub["fwd_ret"].mean() * 100
    med = sub["fwd_ret"].median() * 100
    win = (sub["fwd_ret"] > 0).mean() * 100
    win10 = (sub["fwd_ret"] > 0.10).mean() * 100
    max_med = sub["max_ret"].median() * 100
    min_med = sub["min_ret"].median() * 100
    print(
        f"  {label:<26}  n={len(sub):4d}  mean={mean:6.2f}%  med={med:6.2f}%  "
        f"win={win:5.1f}%  >+10%={win10:5.1f}%  最高中位={max_med:6.2f}%  最低中位={min_med:6.2f}%"
    )


def _correlations(picks: pd.DataFrame, fwd: pd.DataFrame) -> None:
    merged = picks.merge(fwd[["ts_code", "fwd_ret"]], on="ts_code", how="inner")
    if len(merged) < 10:
        print("  样本不足, 跳过相关性")
        return
    cols = ["vol_dry_x", "box_range_pct", "close_cv", "drawdown_pct",
            "rps_chg20", "vol_expand_x", "base_score"]
    print("  明细指标 vs 远期收益 的 Spearman 相关系数:")
    for col in cols:
        if col not in merged.columns:
            continue
        series = pd.to_numeric(merged[col], errors="coerce")
        if series.notna().sum() < 10 or series.nunique() < 3:
            continue
        rho = series.corr(merged["fwd_ret"], method="spearman")
        print(f"    {col:<16} {rho:+.3f}")


def verify_one_date(df: pd.DataFrame, date: str, windows: List[int],
                    cfg: QuietBaseConfig, show_corr: bool = True) -> pd.DataFrame:
    """跑一期验证，返回该期的入选明细（带各窗口 fwd_ret 列）"""
    print("=" * 88)
    print(f"观察日: {date}")
    print("=" * 88)

    picks = screen_quiet_base(df, target_date=date, config=cfg, verbose=True)
    if picks.empty:
        print("  该日无入选，跳过\n")
        return pd.DataFrame()

    start_dt = pd.Timestamp(picks.iloc[0]["trade_date"])
    buy_codes = picks.loc[picks["BUY"], "ts_code"].tolist()
    watch_codes = picks.loc[picks["WATCH"], "ts_code"].tolist()
    market_codes = df["ts_code"].unique().tolist()

    out = picks.copy()
    for bars in windows:
        print(f"\n[+{bars} 交易日] {start_dt.date()} 收盘 → 之后第 {bars} 根 K 线收盘")
        fwd_all = forward_returns(df, picks["ts_code"].tolist(), start_dt, bars)
        if fwd_all.empty:
            print("  无前瞻数据（观察日太靠近数据末端）")
            continue
        short = fwd_all[fwd_all["bars_avail"] < bars]
        if not short.empty:
            print(f"  注意: {len(short)} 只票前瞻数据不足 {bars} 根，已按实际可得长度计算")

        _stats_line(fwd_all[fwd_all["ts_code"].isin(buy_codes)], "可买入 BUY")
        _stats_line(fwd_all[fwd_all["ts_code"].isin(watch_codes)], "观察 WATCH")
        bench = forward_returns(df, market_codes, start_dt, bars)
        _stats_line(bench, "[基准] 全市场")

        if not bench.empty:
            buy_fwd = fwd_all[fwd_all["ts_code"].isin(buy_codes)]
            if not buy_fwd.empty:
                excess = (buy_fwd["fwd_ret"].mean() - bench["fwd_ret"].mean()) * 100
                print(f"  → 可买入档超额收益(均值): {excess:+.2f}%")

        out = out.merge(
            fwd_all[["ts_code", "fwd_ret", "max_ret", "min_ret"]].rename(columns={
                "fwd_ret": f"fwd_ret_{bars}",
                "max_ret": f"max_ret_{bars}",
                "min_ret": f"min_ret_{bars}",
            }),
            on="ts_code", how="left",
        )

    if show_corr and windows:
        main_bars = windows[-1]
        print(f"\n[相关性 @ +{main_bars} 日]")
        fwd_main = forward_returns(df, picks["ts_code"].tolist(), start_dt, main_bars)
        if not fwd_main.empty:
            _correlations(picks, fwd_main)

    top_col = f"fwd_ret_{windows[-1]}" if f"fwd_ret_{windows[-1]}" in out.columns else None
    if top_col:
        print(f"\n[+{windows[-1]} 日收益 Top 15]")
        top = out.sort_values(top_col, ascending=False).head(15).copy()
        show = top[["ts_code", "SIGNAL", "vol_dry_x", "box_range_pct",
                    "rps_chg20", "base_score", top_col]].copy()
        show[top_col] = (show[top_col] * 100).round(1)
        print(show.to_string(index=False))

        print(f"\n[+{windows[-1]} 日收益 Bottom 10]")
        bot = out.sort_values(top_col).head(10).copy()
        show = bot[["ts_code", "SIGNAL", "vol_dry_x", "box_range_pct",
                    "rps_chg20", "base_score", top_col]].copy()
        show[top_col] = (show[top_col] * 100).round(1)
        print(show.to_string(index=False))

    out["observe_date"] = date
    print()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="静默筑底 · 低位再启动 前瞻验证")
    parser.add_argument("--date", default=None, help="观察日 YYYYMMDD")
    parser.add_argument("--dates", default=None, help="多个观察日，逗号分隔（多期汇总）")
    parser.add_argument("--windows", default="10,20,40", help="前瞻窗口（交易日），逗号分隔")
    parser.add_argument("--cache", default=None, help="带 RPS 的日线 parquet/csv")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 路径")
    parser.add_argument("--min-days", type=int, default=350, help="最少日线根数")
    parser.add_argument("--vol-dry-max", type=float, default=0.85, help="地量比上限")
    parser.add_argument("--box-range-max", type=float, default=0.22, help="箱体振幅上限")
    parser.add_argument("--rps-rise-min", type=float, default=10.0, help="RPS 抬升下限")
    args = parser.parse_args()

    if not args.date and not args.dates:
        parser.error("需要 --date 或 --dates")

    dates = (
        [d.strip() for d in args.dates.split(",") if d.strip()]
        if args.dates else [args.date]
    )
    windows = sorted({int(w) for w in args.windows.split(",") if w.strip()})

    OUTPUT_DIR.mkdir(exist_ok=True)
    cfg = QuietBaseConfig(
        vol_dry_max=args.vol_dry_max,
        box_range_max=args.box_range_max,
        rps_rise_min=args.rps_rise_min,
    )

    print("=" * 88)
    print("静默筑底 · 低位再启动 —— 前瞻验证")
    print(f"观察日: {dates}   前瞻窗口: {windows} 交易日")
    print("=" * 88)
    df = load_data(args.cache, args.tdx_dir, args.min_days)
    print(f"  数据范围: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}, "
          f"{df['ts_code'].nunique()} 只\n")

    frames = []
    for date in dates:
        out = verify_one_date(df, date, windows, cfg)
        if not out.empty:
            frames.append(out)
    if not frames:
        print("所有观察日均无入选，结束")
        return

    allp = pd.concat(frames, ignore_index=True)

    if len(dates) > 1:
        print("=" * 88)
        print("多期汇总")
        print("=" * 88)
        for bars in windows:
            col = f"fwd_ret_{bars}"
            if col not in allp.columns:
                continue
            print(f"\n[+{bars} 交易日]")
            for tier, mask in (("可买入 BUY", allp["BUY"]), ("观察 WATCH", allp["WATCH"])):
                sub = allp[mask & allp[col].notna()]
                if sub.empty:
                    print(f"  {tier:<26}  n=  0")
                    continue
                print(
                    f"  {tier:<26}  n={len(sub):4d}  "
                    f"mean={sub[col].mean() * 100:6.2f}%  med={sub[col].median() * 100:6.2f}%  "
                    f"win={(sub[col] > 0).mean() * 100:5.1f}%"
                )

    tag = dates[0] if len(dates) == 1 else f"{dates[0]}_x{len(dates)}"
    out_path = OUTPUT_DIR / f"quiet_base_forward_{tag}.csv"
    allp.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n  明细: {out_path}")


if __name__ == "__main__":
    main()
