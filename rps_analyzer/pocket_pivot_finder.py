"""
================================================================================
口袋支点识别模块 - 量化识别 Pocket Pivot 买点
================================================================================

口袋支点定义（Mark Minervini）：
1. 上涨日 - 今日收盘价 > 昨日收盘价
2. 放量突破卖压 - 今日成交量 > 过去 10 天所有下跌日的最大成交量
3. 均线突破 - 突破 MA10 或 MA50，且前一日收盘价在均线附近（偏离度 < 4%）
4. 长期趋势 - 股价 > MA200
"""

import pandas as pd
import numpy as np
from typing import List, Tuple


def identify_pocket_pivot(df: pd.DataFrame,
                         vol_window: int = 10,
                         ma_threshold: float = 0.04) -> pd.DataFrame:
    """
    量化识别口袋支点买入信号

    参数
    ----------
    df : pd.DataFrame
        单只股票的日线数据
    vol_window : int
        成交量窗口大小（默认 10 天）
    ma_threshold : float
        均线偏离度阈值（默认 4%）

    返回
    -------
    pd.DataFrame
        原始数据 + pocket_pivot_signal 列 (True/False)
    """
    df = df.copy()

    if len(df) < 250:
        return df

    # 1. 计算关键均线
    df['ma_10'] = df['close'].rolling(window=10).mean()
    df['ma_50'] = df['close'].rolling(window=50).mean()
    df['ma_200'] = df['close'].rolling(window=200).mean()

    # 2. 识别近期的"最大卖压"
    # 判断是否为下跌日：今日收盘价 < 昨日收盘价
    df['is_down_day'] = df['close'] < df['close'].shift(1)

    # 提取下跌日的成交量，上涨日设为 0
    df['down_vol'] = np.where(df['is_down_day'], df['vol'], 0)

    # 计算过去 vol_window 天下跌日的最大成交量（滚动窗口）
    df['max_down_vol_10'] = df['down_vol'].rolling(window=vol_window).max().shift(1)

    # 3. 支点条件判断 (量化规则)

    # 条件 A：今日必须是上涨的 (基于收盘价)
    cond_up = df['close'] > df['close'].shift(1)

    # 条件 B：今日成交量必须绝对大于过去 10 天内任何一个下跌日的成交量
    cond_vol = df['vol'] > df['max_down_vol_10']

    # 条件 C：均线粘合与突破
    # 收盘价在 10 日或 50 日均线附近（例如偏离度在 4% 以内），或者是从下方穿越
    cond_near_ma10 = (
        (abs(df['close'].shift(1) - df['ma_10'].shift(1)) / df['ma_10'].shift(1) < ma_threshold) &
        (df['close'] > df['ma_10'])
    )
    cond_near_ma50 = (
        (abs(df['close'].shift(1) - df['ma_50'].shift(1)) / df['ma_50'].shift(1) < ma_threshold) &
        (df['close'] > df['ma_50'])
    )
    cond_ma = cond_near_ma10 | cond_near_ma50

    # 条件 D：长期趋势过滤（极度建议）。要求股价在 200 日均线之上
    cond_trend = df['close'] > df['ma_200']

    # 4. 综合信号生成
    df['pocket_pivot_signal'] = cond_up & cond_vol & cond_ma & cond_trend

    return df


def find_pocket_pivot_stocks(df: pd.DataFrame,
                             rps_threshold: float = 90.0,
                             min_signals: int = 1) -> pd.DataFrame:
    """
    在高 RPS 股票中寻找口袋支点

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
        包含 pocket_pivot_signal 的数据
    """
    print(f"[口袋支点] 开始筛选高 RPS 股票...")

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

    print(f"[口袋支点] 高 RPS 股票数量: {len(high_rps_stocks)}")

    # 对每只高 RPS 股票进行口袋支点识别
    results = []
    signal_count = 0

    for i, ts_code in enumerate(high_rps_stocks):
        stock_data = df[df['ts_code'] == ts_code].sort_values('trade_date').reset_index(drop=True)

        if len(stock_data) < 250:
            continue

        try:
            stock_data = identify_pocket_pivot(stock_data)
        except Exception as e:
            continue

        if stock_data['pocket_pivot_signal'].any():
            signal_count += len(stock_data[stock_data['pocket_pivot_signal']])
            results.append(stock_data)

        if (i + 1) % 50 == 0:
            print(f"[口袋支点] 已处理 {i + 1}/{len(high_rps_stocks)} 只股票, 发现 {signal_count} 个信号")

    if len(results) == 0:
        print("[口袋支点] 未发现口袋支点信号")
        return pd.DataFrame()

    result_df = pd.concat(results, ignore_index=True)
    print(f"[口袋支点] 完成！发现 {signal_count} 个口袋支点信号")

    return result_df


if __name__ == "__main__":
    print("口袋支点识别测试...")
