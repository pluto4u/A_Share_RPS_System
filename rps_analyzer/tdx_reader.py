"""
================================================================================
通达信本地 .day 文件读取器
================================================================================
读取 C:/new_tdx/vipdoc/{sh,sz}/lday/ 下的二进制日线文件，输出标准 DataFrame。

.day 文件格式 (每条记录 32 字节):
  date    : uint32  (YYYYMMDD)
  open    : uint32  (价格 * 100)
  high    : uint32  (价格 * 100)
  low     : uint32  (价格 * 100)
  close   : uint32  (价格 * 100)
  amount  : float32 (成交额，元)
  vol     : uint32  (成交量，股)
  reserved: uint32
"""

import struct
import os
import pandas as pd
import numpy as np
from typing import Optional, List
from pathlib import Path

RECORD_FORMAT = "<IIIIIfII"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)  # 32

A_SHARE_PREFIXES_SH = ("600", "601", "603", "605", "688", "689")
A_SHARE_PREFIXES_SZ = ("000", "001", "002", "003", "300", "301")
DEFAULT_TDX_DIR = r"C:\new_tdx\vipdoc\sh\lday"


def normalize_tdx_dir(tdx_dir: str) -> str:
    """
    统一 TDX 数据目录。

    支持传入 vipdoc 根目录，也支持传入具体 lday 目录，例如:
    C:\\new_tdx\\vipdoc\\sh\\lday -> C:\\new_tdx\\vipdoc
    """
    path = Path(tdx_dir)
    parts = [part.lower() for part in path.parts]
    if len(parts) >= 2 and parts[-1] == "lday" and parts[-2] in ("sh", "sz"):
        return str(path.parents[1])
    return str(path)


def read_day_file(filepath: str) -> Optional[pd.DataFrame]:
    """读取单个 .day 文件，返回 DataFrame 或 None"""
    try:
        size = os.path.getsize(filepath)
        if size < RECORD_SIZE:
            return None

        n_records = size // RECORD_SIZE
        with open(filepath, "rb") as f:
            raw = f.read(n_records * RECORD_SIZE)

        records = np.frombuffer(raw, dtype=np.dtype([
            ("date", "<u4"), ("open", "<u4"), ("high", "<u4"),
            ("low", "<u4"), ("close", "<u4"),
            ("amount", "<f4"), ("vol", "<u4"), ("reserved", "<u4"),
        ]))

        df = pd.DataFrame({
            "trade_date": pd.to_datetime(records["date"].astype(str), format="%Y%m%d", errors="coerce"),
            "open": records["open"] / 100.0,
            "high": records["high"] / 100.0,
            "low": records["low"] / 100.0,
            "close": records["close"] / 100.0,
            "amount": records["amount"].astype(np.float64),
            "vol": records["vol"].astype(np.float64),
        })

        df = df.dropna(subset=["trade_date"])
        return df if len(df) > 0 else None

    except Exception:
        return None


def _is_a_share(filename: str, market: str) -> bool:
    """判断是否为 A 股（排除指数、B股、基金等）"""
    code = filename[2:8]  # e.g. "sh600519.day" -> "600519"
    if market == "sh":
        return code[:3] in A_SHARE_PREFIXES_SH
    elif market == "sz":
        return code[:3] in A_SHARE_PREFIXES_SZ
    return False


def _to_ts_code(filename: str, market: str) -> str:
    """文件名转标准代码: sh600519.day -> 600519.SH"""
    code = filename[2:8]
    return f"{code}.{market.upper()}"


def load_tdx_market(
    tdx_dir: str = DEFAULT_TDX_DIR,
    markets: List[str] = None,
    min_days: int = 250,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    批量读取通达信本地日线数据

    参数
    ----------
    tdx_dir : str
        通达信 vipdoc 目录路径，或 sh/sz lday 目录路径
    markets : list
        要读取的市场，默认 ["sh", "sz"]
    min_days : int
        最少交易日数，少于此的（次新股）跳过
    verbose : bool
        打印进度

    返回
    -------
    pd.DataFrame
        含 trade_date, ts_code, open, high, low, close, amount, vol 列
    """
    tdx_dir = normalize_tdx_dir(tdx_dir)

    if markets is None:
        markets = ["sh", "sz"]

    all_dfs = []
    total_files = 0
    loaded = 0
    skipped_short = 0

    for market in markets:
        lday_dir = os.path.join(tdx_dir, market, "lday")
        if not os.path.exists(lday_dir):
            if verbose:
                print(f"[TDX] 目录不存在: {lday_dir}")
            continue

        files = [f for f in os.listdir(lday_dir) if f.endswith(".day") and _is_a_share(f, market)]
        total_files += len(files)

        if verbose:
            print(f"[TDX] {market.upper()} A股文件: {len(files)} 个")

        for i, fname in enumerate(files):
            fpath = os.path.join(lday_dir, fname)
            df = read_day_file(fpath)

            if df is None or len(df) < min_days:
                skipped_short += 1
                continue

            df["ts_code"] = _to_ts_code(fname, market)
            all_dfs.append(df)
            loaded += 1

            if verbose and (i + 1) % 500 == 0:
                print(f"  [{market.upper()}] 已读取 {i+1}/{len(files)}...")

    if not all_dfs:
        raise RuntimeError(f"未读取到任何数据，请检查路径: {tdx_dir}")

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    if verbose:
        latest = result["trade_date"].max()
        print(f"[TDX] 读取完成: {loaded} 只股票, {len(result)} 行")
        print(f"[TDX] 跳过次新: {skipped_short} 只 (< {min_days} 天)")
        print(f"[TDX] 最新日期: {latest.strftime('%Y-%m-%d')}")

    return result


if __name__ == "__main__":
    import argparse, time

    parser = argparse.ArgumentParser(description="通达信本地数据读取器")
    parser.add_argument("--dir", default=DEFAULT_TDX_DIR, help="vipdoc 路径，或 sh/sz lday 路径")
    parser.add_argument("--save", default=None, help="保存为 parquet/csv")
    parser.add_argument("--min-days", type=int, default=250, help="最少交易天数")
    args = parser.parse_args()

    t0 = time.time()
    df = load_tdx_market(args.dir, min_days=args.min_days)
    elapsed = time.time() - t0
    print(f"[TDX] 耗时: {elapsed:.1f}s")

    print(f"\n数据摘要:")
    print(f"  股票数: {df['ts_code'].nunique()}")
    print(f"  日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"  总行数: {len(df)}")

    if args.save:
        if args.save.endswith(".parquet"):
            df.to_parquet(args.save, index=False)
        else:
            df.to_csv(args.save, index=False)
        print(f"\n已保存到 {args.save}")
