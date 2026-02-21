"""
================================================================================
杯柄形态识别模块 - 量化识别 Mark Minervini 杯柄形态
================================================================================
"""

import pandas as pd
import numpy as np
from typing import List, Tuple


def find_local_extremes(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    识别局部极值点（用于寻找杯底和杯顶）

    参数
    ----------
    df : pd.DataFrame
        单只股票的日线数据
    window : int
        窗口大小

    返回
    -------
    pd.DataFrame
        原始数据 + is_low, is_high 标记
    """
    df = df.copy()

    # 识别局部低点
    df['is_low'] = (
        df['close'] == df['close'].rolling(window, center=True).min()
    )

    # 识别局部高点
    df['is_high'] = (
        df['close'] == df['close'].rolling(window, center=True).max()
    )

    return df


def identify_cup_handle(df: pd.DataFrame,
                       cup_depth_range: Tuple[float, float] = (0.15, 0.30),
                       handle_depth_range: Tuple[float, float] = (0.08, 0.12),
                       min_cup_days: int = 30,
                       max_cup_days: int = 200,
                       min_handle_days: int = 5,
                       max_handle_days: int = 30) -> pd.DataFrame:
    """
    量化识别杯柄形态

    参数
    ----------
    df : pd.DataFrame
        单只股票的日线数据（按日期排序）
    cup_depth_range : tuple
        杯底深度范围 (最小跌幅, 最大跌幅)，默认 (0.15, 0.30)
    handle_depth_range : tuple
        柄部深度范围 (最小跌幅, 最大跌幅)，默认 (0.08, 0.12)
    min_cup_days, max_cup_days : int
        杯部持续天数范围
    min_handle_days, max_handle_days : int
        柄部持续天数范围

    返回
    -------
    pd.DataFrame
        原始数据 + cup_handle_signal 列 (True/False)
        以及 cup_start_idx, cup_bottom_idx, cup_right_idx, handle_end_idx 等标记
    """
    df = df.copy().reset_index(drop=True)

    # 初始化信号列
    df['cup_handle_signal'] = False
    df['cup_signal_stage'] = None
    df['cup_info'] = None

    if len(df) < max_cup_days + max_handle_days + 20:
        return df

    # 识别局部极值点
    df = find_local_extremes(df, window=5)

    # 寻找杯底（局部低点）
    cup_bottoms = df[df['is_low']].index.tolist()

    for bottom_idx in cup_bottoms:
        # 检查是否有足够的历史数据
        if bottom_idx < min_cup_days + 10:
            continue

        # 杯底前 10-200 天内寻找杯顶（局部高点）
        cup_top_start = max(0, bottom_idx - max_cup_days)
        cup_top_end = max(0, bottom_idx - min_cup_days)

        cup_top_area = df.loc[cup_top_start:bottom_idx]
        cup_top_candidates = cup_top_area[cup_top_area['is_high']]

        if len(cup_top_candidates) == 0:
            continue

        # 选择杯底前的最高点作为杯顶
        cup_top_row = cup_top_candidates.loc[cup_top_candidates['close'].idxmax()]
        cup_top_idx = cup_top_row.name

        # 1. 检查杯底深度
        cup_top_price = cup_top_row['close']
        cup_bottom_price = df.loc[bottom_idx, 'close']
        cup_depth = (cup_top_price - cup_bottom_price) / cup_top_price

        if not (cup_depth_range[0] <= cup_depth <= cup_depth_range[1]):
            continue

        # 2. 检查杯右高度（杯底后回升）
        handle_start = bottom_idx + 1
        handle_end = min(len(df) - 1, bottom_idx + max_cup_days)

        if handle_end <= handle_start:
            continue

        cup_right_area = df.loc[handle_start:handle_end]

        # 寻找杯右的高点
        cup_right_high = cup_right_area['close'].max()
        cup_right_high_idx = cup_right_area['close'].idxmax()

        # 检查杯右高度是否达到杯顶的 85-100%
        cup_right_retrace = (cup_right_high - cup_bottom_price) / (cup_top_price - cup_bottom_price)

        if not (0.85 <= cup_right_retrace <= 1.0):
            continue

        # 3. 寻找柄部（杯右后的回调）
        if cup_right_high_idx >= len(df) - min_handle_days:
            continue

        handle_area_end = min(len(df) - 1, cup_right_high_idx + max_handle_days)
        handle_area = df.loc[cup_right_high_idx:handle_area_end]

        if len(handle_area) < min_handle_days:
            continue

        # 柄部低点
        handle_low_price = handle_area['close'].min()
        handle_low_idx = handle_area['close'].idxmin()

        # 检查柄部深度
        handle_depth = (cup_right_high - handle_low_price) / cup_right_high

        if not (handle_depth_range[0] <= handle_depth <= handle_depth_range[1]):
            continue

        # 4. 检查突破点（柄部后突破柄部高点或杯顶）
        if handle_low_idx >= len(df) - 1:
            continue

        breakout_area = df.loc[handle_low_idx:min(len(df)-1, handle_low_idx + 10)]

        # 检查是否有突破
        breakout_signal = False
        breakout_price = 0

        # 突破杯顶或杯右高点
        for idx, row in breakout_area.iterrows():
            if row['close'] > cup_top_price * 1.02:  # 突破杯顶 2%
                breakout_signal = True
                breakout_price = row['close']
                break

        if not breakout_signal:
            continue

        # 检查成交量配合
        # 杯底缩量，柄部缩量，突破放量
        if 'vol' in df.columns:
            cup_bottom_vol = df.loc[bottom_idx, 'vol']
            cup_right_high_vol = df.loc[cup_right_high_idx, 'vol']
            handle_low_vol = df.loc[handle_low_idx, 'vol']
            breakout_vol = df.loc[idx, 'vol']

            # 杯底成交量应该小于杯顶
            # 杯右成交量可以大于杯底
            # 柄部成交量应该缩量
            # 突破应该放量（至少大于柄部低点成交量的 1.5 倍）
            if breakout_vol < handle_low_vol * 1.3:
                continue

        # 5. 所有条件满足，标记信号
        df.loc[idx, 'cup_handle_signal'] = True
        df.loc[idx, 'cup_signal_stage'] = 'breakout'

        cup_info = {
            'cup_top_idx': cup_top_idx,
            'cup_bottom_idx': bottom_idx,
            'cup_right_idx': cup_right_high_idx,
            'handle_low_idx': handle_low_idx,
            'breakout_idx': idx,
            'cup_depth': round(cup_depth * 100, 2),
            'cup_right_retrace': round(cup_right_retrace * 100, 2),
            'handle_depth': round(handle_depth * 100, 2),
            'cup_top_price': round(cup_top_price, 2),
            'cup_bottom_price': round(cup_bottom_price, 2),
            'cup_right_price': round(cup_right_high, 2),
            'handle_low_price': round(handle_low_price, 2),
            'breakout_price': round(breakout_price, 2)
        }
        df.loc[idx, 'cup_info'] = str(cup_info)

        break  # 只标记最近的一个信号

    return df


def find_cup_handle_stocks(df: pd.DataFrame,
                          rps_threshold: float = 90.0,
                          min_signals: int = 1) -> pd.DataFrame:
    """
    在高 RPS 股票中寻找杯柄形态

    参数
    ----------
    df : pd.DataFrame
        包含 RPS 列的全市场日线数据
    rps_threshold : float
        RPS 阈值
    min_signals : int
        最少需要出现的信号次数

    返回
    -------
    pd.DataFrame
        包含 cup_handle_signal 的数据
    """
    print(f"[Cup-Handle] 开始筛选高 RPS 股票...")

    # 筛选高 RPS 股票
    if 'RPS_250' not in df.columns or 'RPS_120' not in df.columns:
        raise ValueError("数据中缺少 RPS 列")

    # 获取最新一天的高 RPS 股票列表
    latest_date = df['trade_date'].max()
    high_rps_stocks = df[
        (df['trade_date'] == latest_date) &
        (df['RPS_250'] > rps_threshold) &
        (df['RPS_120'] > rps_threshold)
    ]['ts_code'].unique()

    print(f"[Cup-Handle] 高 RPS 股票数量: {len(high_rps_stocks)}")

    # 对每只高 RPS 股票进行杯柄形态识别
    results = []
    signal_count = 0

    for i, ts_code in enumerate(high_rps_stocks):
        stock_data = df[df['ts_code'] == ts_code].sort_values('trade_date').reset_index(drop=True)

        if len(stock_data) < 200:
            continue

        try:
            stock_data = identify_cup_handle(stock_data)
        except Exception as e:
            continue

        if stock_data['cup_handle_signal'].any():
            signal_count += len(stock_data[stock_data['cup_handle_signal']])
            results.append(stock_data)

        if (i + 1) % 50 == 0:
            print(f"[Cup-Handle] 已处理 {i + 1}/{len(high_rps_stocks)} 只股票, 发现 {signal_count} 个信号")

    if len(results) == 0:
        print("[Cup-Handle] 未发现杯柄信号")
        return pd.DataFrame()

    result_df = pd.concat(results, ignore_index=True)
    print(f"[Cup-Handle] 完成！发现 {signal_count} 个杯柄信号")

    return result_df


if __name__ == "__main__":
    print("杯柄形态识别测试...")
