"""
次新股诊断 (上市 < 250 天, RPS 系统不适用)

读 TDX 原始 .day 文件, 输出简单走势 + 涨幅 + 量能。

用法:
    py -3 .\\tools\\diagnose_newcomer.py 301666.SZ 688808.SH
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rps_analyzer.tdx_reader import read_day_file

TDX_DIR = Path(r"D:\tdx\vipdoc")


def code_to_filepath(ts_code: str) -> Path:
    code, market = ts_code.split(".")
    market = market.lower()
    return TDX_DIR / market / "lday" / f"{market}{code}.day"


def diagnose(ts_code: str) -> None:
    path = code_to_filepath(ts_code)
    print("\n" + "=" * 78)
    print(f"次新股诊断 {ts_code}  ({path})")
    print("=" * 78)

    if not path.exists():
        print(f"  TDX 文件不存在: {path}")
        return

    df = read_day_file(str(path))
    if df is None or df.empty:
        print(f"  无数据")
        return

    df = df.sort_values("trade_date").reset_index(drop=True)
    n = len(df)
    print(f"\n[A] 上市天数: {n} 个交易日 (远小于 RPS 所需 120 天)")
    print(f"    上市首日 {df.iloc[0]['trade_date'].date()}  收盘 {df.iloc[0]['close']:.2f}")
    print(f"    最新日期 {df.iloc[-1]['trade_date'].date()}  收盘 {df.iloc[-1]['close']:.2f}")

    # 价格走势
    first_close = df.iloc[0]["close"]
    last_close = df.iloc[-1]["close"]
    chg = last_close / first_close - 1
    high_all = df["high"].max()
    low_all = df["low"].min()
    high_idx = df["high"].idxmax()
    high_dt = df.iloc[high_idx]["trade_date"].date()

    print(f"\n[B] 上市以来表现")
    print(f"    总涨幅: {chg:+.2%}")
    print(f"    历史区间: [{low_all:.2f}, {high_all:.2f}]")
    print(f"    最高点出现在: {high_dt}")
    print(f"    自最高点回撤: {(last_close/high_all - 1):+.2%}")

    if n >= 3:
        recent_3 = df.tail(3)
        recent_chg = recent_3.iloc[-1]["close"] / recent_3.iloc[0]["close"] - 1
        print(f"\n[C] 近 3 日: 收盘 {recent_3.iloc[0]['close']:.2f} -> {recent_3.iloc[-1]['close']:.2f}  涨幅 {recent_chg:+.2%}")

    # 量能
    print(f"\n[D] 全部交易日明细")
    df_show = df.copy()
    df_show["trade_date"] = df_show["trade_date"].dt.strftime("%Y-%m-%d")
    df_show["amt_yi"] = (df_show["amount"] / 1e8).round(2)
    df_show["chg_pct"] = (df_show["close"].pct_change() * 100).round(2)
    cols = ["trade_date", "open", "high", "low", "close", "amt_yi", "chg_pct"]
    df_show.columns = [c if c != "amt_yi" else "amt(亿)" for c in df_show.columns]
    df_show.columns = [c if c != "chg_pct" else "chg%" for c in df_show.columns]
    print(df_show[["trade_date", "open", "high", "low", "close", "amt(亿)", "chg%"]].to_string(index=False, float_format="%.2f"))

    # 风格判定
    print(f"\n[E] 风格判定")
    if n < 30:
        print(f"    [警告] 上市 {n} 天，量化系统不适用")
        print(f"    -> 适合用 '次新股 + 首板/连板' 等策略框架")
        if chg > 1.0:
            print(f"    -> 累计涨幅 {chg:+.0%}，已被资金爆炒，追入风险大")
        elif chg < -0.3:
            print(f"    -> 累计跌幅 {chg:+.0%}，可能在破发等启动信号")
    else:
        print(f"    上市 {n} 天，可作为短线观察 (需手动看量价)")


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: py -3 .\\tools\\diagnose_newcomer.py 301666.SZ 688808.SH")
        sys.exit(1)
    for code in sys.argv[1:]:
        diagnose(code)


if __name__ == "__main__":
    main()
