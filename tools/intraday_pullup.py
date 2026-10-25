"""
================================================================================
T0 砸后拉形态扫描（日线代理）
================================================================================
TDX 本地数据是日线 OHLC，没有分钟数据，无法精确切出"下午拉升"。
本工具用日线 K 线形态作为「先砸后拉」的代理：

  dip_pct        = (max(prev_close, open) - low) / max(prev_close, open)
                   当日相对前收/开盘 砸到 low 的最大跌幅
  rebound_pct    = (close - low) / low
                   最低点到收盘的反弹幅度（即"拉升幅度"）
  close_pos      = (close - low) / (high - low)
                   收盘在当日区间位置，越接近 1.0 越强
  day_change_pct = (close - prev_close) / prev_close
  amount_yi      = amount / 1e8   成交额（亿元）

筛选门槛（默认）:
  dip_pct       >= 3%     (确认"砸")
  rebound_pct   >= 4%     (确认"拉")
  close_pos     >= 0.6    (尾盘相对强势)
  amount_yi     >= 2.0    (避免小票噪音)

输出排序: score = rebound_pct * close_pos
并标记交集: IN_L1 / IN_L2 / IN_SUPER / IN_HUOCHE / IN_EARLY_L1

用法:
    py -3 tools/intraday_pullup.py --date 20260526
    py -3 tools/intraday_pullup.py --date 20260526 --dip 2 --rebound 3 --topn 30
================================================================================
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market  # noqa: E402

OUTPUT_DIR = ROOT / "output"


def _load_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except OSError:
        return set()


def main() -> None:
    parser = argparse.ArgumentParser(description="T0 砸后拉形态扫描（日线代理）")
    parser.add_argument("--date", required=True, help="目标日期 YYYYMMDD")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--min-days", type=int, default=60)
    parser.add_argument("--dip", type=float, default=3.0, help="dip_pct 下限 (%)")
    parser.add_argument("--rebound", type=float, default=4.0, help="rebound_pct 下限 (%)")
    parser.add_argument("--close-pos", type=float, default=0.6, help="收盘位 下限 [0,1]")
    parser.add_argument("--amount", type=float, default=2.0, help="成交额下限 (亿元)")
    parser.add_argument("--topn", type=int, default=40, help="控制台打印 Top N")
    args = parser.parse_args()

    date_str = args.date
    target_dt = pd.to_datetime(date_str, format="%Y%m%d")

    print(f"[砸后拉] 读取 TDX: {args.tdx_dir}")
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days, verbose=True)
    df = df.sort_values(["ts_code", "trade_date"])

    today = df[df["trade_date"] == target_dt].copy()
    if today.empty:
        raise SystemExit(f"[砸后拉] {date_str} 无数据，最新日: {df['trade_date'].max().date()}")

    df["prev_close"] = df.groupby("ts_code")["close"].shift(1)
    today = today.merge(
        df[df["trade_date"] == target_dt][["ts_code", "prev_close"]],
        on="ts_code", how="left",
    )

    today = today[today["high"] > today["low"]].copy()
    today = today[today["low"] > 0].copy()
    today = today[today["prev_close"].notna()].copy()

    ref = today[["open", "prev_close"]].max(axis=1)
    today["dip_pct"] = (ref - today["low"]) / ref * 100.0
    today["rebound_pct"] = (today["close"] - today["low"]) / today["low"] * 100.0
    today["close_pos"] = (today["close"] - today["low"]) / (today["high"] - today["low"])
    today["day_change_pct"] = (today["close"] - today["prev_close"]) / today["prev_close"] * 100.0
    today["amount_yi"] = today["amount"] / 1e8
    today["score"] = today["rebound_pct"] * today["close_pos"]

    pool = today[
        (today["dip_pct"] >= args.dip)
        & (today["rebound_pct"] >= args.rebound)
        & (today["close_pos"] >= args.close_pos)
        & (today["amount_yi"] >= args.amount)
    ].copy()

    layers = {
        "IN_L1":       _load_codes(OUTPUT_DIR / f"tdx_screener_{date_str}_codes.csv"),
        "IN_L2":       _load_codes(OUTPUT_DIR / f"tdx_screener_{date_str}_L2_codes.csv"),
        "IN_SUPER":    _load_codes(OUTPUT_DIR / f"tdx_screener_{date_str}_super_codes.csv"),
        "IN_HUOCHE":   _load_codes(OUTPUT_DIR / f"tdx_screener_{date_str}_huoche_codes.csv"),
        "IN_EARLY_L1": _load_codes(OUTPUT_DIR / f"tdx_screener_{date_str}_early_L1_codes.csv"),
    }
    for col, codes in layers.items():
        pool[col] = pool["ts_code"].isin(codes)
    pool["layers_hit"] = pool[list(layers.keys())].sum(axis=1)

    pool = pool.sort_values("score", ascending=False).reset_index(drop=True)

    out_cols = [
        "ts_code", "close", "open", "high", "low", "prev_close",
        "dip_pct", "rebound_pct", "close_pos", "day_change_pct",
        "amount_yi", "score", "layers_hit",
        "IN_L1", "IN_L2", "IN_SUPER", "IN_HUOCHE", "IN_EARLY_L1",
    ]
    out = pool[out_cols].copy()
    out_path = OUTPUT_DIR / f"intraday_pullup_{date_str}.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.4f")

    print()
    print("=" * 90)
    print(f"[砸后拉] 目标日 {date_str}  全市场命中 {len(pool)} 只 (阈值 dip>={args.dip}% rebound>={args.rebound}% close_pos>={args.close_pos} amount>={args.amount}亿)")
    print("=" * 90)

    cols_show = ["ts_code", "close", "dip_pct", "rebound_pct", "close_pos",
                 "day_change_pct", "amount_yi", "score",
                 "IN_L1", "IN_L2", "IN_SUPER", "IN_HUOCHE", "IN_EARLY_L1"]

    print("\n[Top {n}] 全市场".format(n=args.topn))
    print(out[cols_show].head(args.topn).to_string(
        index=False,
        formatters={
            "close": "{:>8.2f}".format,
            "dip_pct": "{:>6.2f}".format,
            "rebound_pct": "{:>6.2f}".format,
            "close_pos": "{:>6.2f}".format,
            "day_change_pct": "{:>6.2f}".format,
            "amount_yi": "{:>7.2f}".format,
            "score": "{:>6.2f}".format,
        },
    ))

    in_l1 = out[out["IN_L1"]]
    in_super = out[out["IN_SUPER"]]
    in_huoche = out[out["IN_HUOCHE"]]

    if not in_l1.empty:
        print(f"\n[L1 内] {len(in_l1)} 只")
        print(in_l1[cols_show].head(args.topn).to_string(
            index=False,
            formatters={
                "close": "{:>8.2f}".format,
                "dip_pct": "{:>6.2f}".format,
                "rebound_pct": "{:>6.2f}".format,
                "close_pos": "{:>6.2f}".format,
                "day_change_pct": "{:>6.2f}".format,
                "amount_yi": "{:>7.2f}".format,
                "score": "{:>6.2f}".format,
            },
        ))

    if not in_super.empty:
        print(f"\n[Super 内] {len(in_super)} 只")
        print(in_super[cols_show].to_string(
            index=False,
            formatters={
                "close": "{:>8.2f}".format,
                "dip_pct": "{:>6.2f}".format,
                "rebound_pct": "{:>6.2f}".format,
                "close_pos": "{:>6.2f}".format,
                "day_change_pct": "{:>6.2f}".format,
                "amount_yi": "{:>7.2f}".format,
                "score": "{:>6.2f}".format,
            },
        ))

    if not in_huoche.empty:
        print(f"\n[Huoche 火车轨 内] {len(in_huoche)} 只")
        print(in_huoche[cols_show].to_string(
            index=False,
            formatters={
                "close": "{:>8.2f}".format,
                "dip_pct": "{:>6.2f}".format,
                "rebound_pct": "{:>6.2f}".format,
                "close_pos": "{:>6.2f}".format,
                "day_change_pct": "{:>6.2f}".format,
                "amount_yi": "{:>7.2f}".format,
                "score": "{:>6.2f}".format,
            },
        ))

    print(f"\n[砸后拉] 完整结果: {out_path}")


if __name__ == "__main__":
    main()
