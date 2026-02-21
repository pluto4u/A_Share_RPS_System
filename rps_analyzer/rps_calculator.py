"""
================================================================================
RPS 计算模块 - 向量化计算全市场相对价格强度
================================================================================
"""

import pandas as pd
import numpy as np
from typing import List


def calculate_rps(df: pd.DataFrame, periods: List[int] = [50, 120, 250]) -> pd.DataFrame:
    """
    向量化计算全市场 RPS (相对价格强度)

    参数
    ----------
    df : pd.DataFrame
        包含 ['trade_date', 'ts_code', 'close', 'vol', 'amount', 'turnover'] 的股票日线数据
    periods : list
        计算周期，默认 [50, 120, 250]

    返回
    -------
    pd.DataFrame
        原始数据 + RPS_n 列
    """
    df = df.copy()

    # 确保数据按日期和代码排序
    df = df.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)

    print(f"[RPS] 开始计算，数据量: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    print(f"[RPS] 计算周期: {periods}")

    for n in periods:
        print(f"[RPS] 计算 RPS_{n}...")

        # 计算每只股票 N 日前的收盘价
        df[f'pre_close_{n}'] = df.groupby('ts_code')['close'].shift(n)

        # 计算 N 日收益率
        df[f'roc_{n}'] = (df['close'] - df[f'pre_close_{n}']) / df[f'pre_close_{n}']

        # 横截面排名：按交易日分组，计算收益率的百分位排名
        # rank(pct=True) 会直接返回 0 到 1 之间的百分比，乘以 100 即为标准 RPS
        df[f'RPS_{n}'] = df.groupby('trade_date')[f'roc_{n}'].rank(pct=True) * 100

        # 清理中间变量
        df.drop(columns=[f'pre_close_{n}', f'roc_{n}'], inplace=True)

    print(f"[RPS] 计算完成")
    return df


def filter_top_rps(df: pd.DataFrame, rps_threshold: float = 90.0) -> pd.DataFrame:
    """
    过滤出 RPS 最高的股票

    参数
    ----------
    df : pd.DataFrame
        包含 RPS 列的数据
    rps_threshold : float
        RPS 阈值，默认 90（即前 10%）

    返回
    -------
    pd.DataFrame
        只包含 RPS_250 > 90 且 RPS_120 > 90 的数据
    """
    if 'RPS_250' not in df.columns or 'RPS_120' not in df.columns:
        raise ValueError("数据中缺少 RPS_250 或 RPS_120 列")

    filtered = df[
        (df['RPS_250'] > rps_threshold) &
        (df['RPS_120'] > rps_threshold)
    ].copy()

    stock_count = filtered['ts_code'].nunique()
    print(f"[RPS] 筛选后: {len(filtered)} 行, {stock_count} 只股票")

    return filtered


if __name__ == "__main__":
    # 测试
    print("RPS 计算器测试...")
