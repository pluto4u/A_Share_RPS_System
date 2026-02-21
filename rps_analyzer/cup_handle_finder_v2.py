"""
================================================================================
杯柄形态识别模块 v2 - 放宽条件版本
================================================================================
"""

import pandas as pd
import numpy as np
from typing import List, Tuple


def find_local_extremes(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    识别局部极值点
    """
    df = df.copy()
    df['is_low'] = (
        df['close'] == df['close'].rolling(window, center=True).min()
    )
    df['is_high'] = (
        df['close'] == df['close'].rolling(window, center=True).max()
    )
    return df


def identify_cup_handle_v2(df: pd.DataFrame,
                          cup_depth_range: Tuple[float, float] = (0.10, 0.40),  # 放宽
                          handle_depth_range: Tuple[float, float] = (0.05, 0.20),  # 放宽
                          min_cup_days: int = 20,  # 放宽
                          max_cup_days: int = 200,
                          min_handle_days: int = 3,  # 放宽
                          max_handle_days: int = 30) -> pd.DataFrame:
    """
    杯柄形态识别 v2 (放宽条件)
    """
    df = df.copy().reset_index(drop=True)

    df['cup_handle_signal'] = False
    df['cup_signal_stage'] = None
    df['cup_info'] = None

    if len(df) < max_cup_days + max_handle_days + 20:
        return df

    df = find_local_extremes(df, window=5)
    cup_bottoms = df[df['is_low']].index.tolist()

    for bottom_idx in cup_bottoms:
        if bottom_idx < min_cup_days + 10:
            continue

        cup_top_start = max(0, bottom_idx - max_cup_days)
        cup_top_end = max(0, bottom_idx - min_cup_days)
        cup_top_area = df.loc[cup_top_start:bottom_idx]
        cup_top_candidates = cup_top_area[cup_top_area['is_high']]

        if len(cup_top_candidates) == 0:
            continue

        cup_top_row = cup_top_candidates.loc[cup_top_candidates['close'].idxmax()]
        cup_top_idx = cup_top_row.name

        # 杯底深度检查
        cup_top_price = cup_top_row['close']
        cup_bottom_price = df.loc[bottom_idx, 'close']
        cup_depth = (cup_top_price - cup_bottom_price) / cup_top_price

        if not (cup_depth_range[0] <= cup_depth <= cup_depth_range[1]):
            continue

        # 杯右高度检查
        handle_start = bottom_idx + 1
        handle_end = min(len(df) - 1, bottom_idx + max_cup_days)

        if handle_end <= handle_start:
            continue

        cup_right_area = df.loc[handle_start:handle_end]
        cup_right_high = cup_right_area['close'].max()
        cup_right_high_idx = cup_right_area['close'].idxmax()

        cup_right_retrace = (cup_right_high - cup_bottom_price) / (cup_top_price - cup_bottom_price)

        if not (0.80 <= cup_right_retrace <= 1.05):  # 放宽
            continue

        # 柄部检查
        if cup_right_high_idx >= len(df) - min_handle_days:
            continue

        handle_area_end = min(len(df) - 1, cup_right_high_idx + max_handle_days)
        handle_area = df.loc[cup_right_high_idx:handle_area_end]

        if len(handle_area) < min_handle_days:
            continue

        handle_low_price = handle_area['close'].min()
        handle_low_idx = handle_area['close'].idxmin()

        handle_depth = (cup_right_high - handle_low_price) / cup_right_high

        if not (handle_depth_range[0] <= handle_depth <= handle_depth_range[1]):
            continue

        # 突破检查
        if handle_low_idx >= len(df) - 1:
            continue

        breakout_area = df.loc[handle_low_idx:min(len(df)-1, handle_low_idx + 10)]

        breakout_signal = False
        breakout_price = 0

        for idx, row in breakout_area.iterrows():
            if row['close'] > cup_top_price * 1.01:  # 降低突破阈值
                breakout_signal = True
                breakout_price = row['close']
                break

        if not breakout_signal:
            continue

        # 标记信号
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

        break

    return df


def find_cup_handle_stocks_v2(df: pd.DataFrame,
                              rps_threshold: float = 90.0,
                              min_signals: int = 1) -> pd.DataFrame:
    """
    杯柄形态识别 v2
    """
    print(f"[Cup-Handle v2] 开始筛选高 RPS 股票...")

    if 'RPS_250' not in df.columns or 'RPS_120' not in df.columns:
        raise ValueError("数据中缺少 RPS 列")

    latest_date = df['trade_date'].max()
    high_rps_stocks = df[
        (df['trade_date'] == latest_date) &
        (df['RPS_250'] > rps_threshold) &
        (df['RPS_120'] > rps_threshold)
    ]['ts_code'].unique()

    print(f"[Cup-Handle v2] 高 RPS 股票数量: {len(high_rps_stocks)}")

    results = []
    signal_count = 0

    for i, ts_code in enumerate(high_rps_stocks):
        stock_data = df[df['ts_code'] == ts_code].sort_values('trade_date').reset_index(drop=True)

        if len(stock_data) < 150:
            continue

        try:
            stock_data = identify_cup_handle_v2(stock_data)
        except Exception as e:
            continue

        if stock_data['cup_handle_signal'].any():
            signal_count += len(stock_data[stock_data['cup_handle_signal']])
            results.append(stock_data)

        if (i + 1) % 50 == 0:
            print(f"[Cup-Handle v2] 已处理 {i + 1}/{len(high_rps_stocks)} 只股票, 发现 {signal_count} 个信号")

    if len(results) == 0:
        print("[Cup-Handle v2] 未发现杯柄信号")
        return pd.DataFrame()

    result_df = pd.concat(results, ignore_index=True)
    print(f"[Cup-Handle v2] 完成！发现 {signal_count} 个杯柄信号")

    return result_df


if __name__ == "__main__":
    print("杯柄形态识别 v2 测试...")
