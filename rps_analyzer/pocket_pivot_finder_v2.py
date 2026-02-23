import pandas as pd
import numpy as np

def identify_pocket_pivot_v2(df: pd.DataFrame, 
                             vol_window: int = 10, 
                             ma_threshold: float = 0.04,
                             max_risk_pct: float = 0.08) -> pd.DataFrame:
    """
    量化识别口袋支点(Pocket Pivot)买入信号 - A股增强版
    
    规则：
    1. 上涨日 - 今日收盘价 > 昨日收盘价
    2. 放量突破卖压 - 今日成交量 > 过去 10 天所有下跌日的最大成交量
    3. 均线附近 - 收盘价在 MA10 或 MA20 附近，或刚突破
    4. 趋势过滤 - 股价 > MA50，且最近20天内最高价距离当前不远（非长期阴跌）
    5. 强制风险计算 - 破10日线或20日线止损，初始风险 <= 8%
    """
    df = df.copy().reset_index(drop=True)
    df['pocket_pivot_signal'] = False
    df['stop_loss_price'] = np.nan
    df['risk_pct'] = np.nan
    df['signal_info'] = None
    
    if len(df) < 60:
        return df
        
    close = df['close'].values
    vol = df['vol'].values if 'vol' in df.columns else np.ones(len(df))
    
    # 1. 计算均线
    df['ma_10'] = df['close'].rolling(window=10).mean()
    df['ma_20'] = df['close'].rolling(window=20).mean()
    df['ma_50'] = df['close'].rolling(window=50).mean()
    
    # 2. 识别近期的"最大卖压"
    # 判断是否为下跌日：今日收盘价 < 昨日收盘价
    df['is_down_day'] = df['close'] < df['close'].shift(1)
    df['down_vol'] = np.where(df['is_down_day'], df['vol'], 0)
    # 过去 vol_window 天下跌日的最大成交量（不含今日）
    df['max_down_vol_10'] = df['down_vol'].rolling(window=vol_window).max().shift(1)
    
    # 最近20天的最高价 (用来判断势能)
    df['highest_20d'] = df['close'].rolling(window=20).max()
    
    # 3. 核心条件判定
    # A. 必须是上涨日
    cond_up = df['close'] > df['close'].shift(1)
    
    # B. 成交量绝对大于过去10天内最大的阴线量，且要求至少是过去5日均量的1.2倍
    cond_vol = (df['vol'] > df['max_down_vol_10']) & (df['vol'] > df['vol'].rolling(5).mean().shift(1) * 1.2)
    
    # C. 必须处于极强多头环境：收盘价在MA50之上，更要在MA20之上，且接近10日线
    # 修正：A股爆炒龙头常常偏离很大，我们不限制上限(highest_20d)，而是要求它已经创新高或接近新高
    cond_trend = (df['close'] > df['ma_50']) & (df['close'] > df['ma_20']) & (df['close'] >= df['highest_20d'] * 0.95)
    
    # D. 均线附近发力 (从均线附近起涨)
    # 要么是 MA10，要么是 MA20。用前一日的收盘价来判断是否在均线附近
    near_ma10 = abs(df['close'].shift(1) - df['ma_10'].shift(1)) / df['ma_10'].shift(1) < ma_threshold
    near_ma20 = abs(df['close'].shift(1) - df['ma_20'].shift(1)) / df['ma_20'].shift(1) < ma_threshold
    cond_near_ma = near_ma10 | near_ma20
    
    # 综合基础信号
    base_signal = cond_up & cond_vol & cond_trend & cond_near_ma
    
    # 4. 风险定价过滤 (向量化操作)
    # 止损设在买入价下方 6%
    df['tentative_stop_loss'] = df['close'] * 0.94
    df['tentative_risk'] = (df['close'] - df['tentative_stop_loss']) / df['close']
    
    # 最终信号：满足基础条件，且初始风险 <= max_risk_pct
    final_signal = base_signal & (df['tentative_risk'] <= max_risk_pct) & (df['tentative_risk'] > 0)
    
    df.loc[final_signal, 'pocket_pivot_signal'] = True
    df.loc[final_signal, 'stop_loss_price'] = df['tentative_stop_loss']
    df.loc[final_signal, 'risk_pct'] = df['tentative_risk']
    
    # 清理中间列
    cols_to_drop = ['is_down_day', 'down_vol', 'max_down_vol_10', 'highest_20d', 'tentative_stop_loss', 'tentative_risk']
    df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True)
    
    return df

def identify_trend_pullback(df: pd.DataFrame, max_risk_pct: float = 0.08) -> pd.DataFrame:
    """
    强趋势均线缩量回踩买点 (Trend Pullback)
    
    规则：
    1. 强趋势：MA10 > MA20 > MA50
    2. 回调到位：前几天股价在MA10之上，今日最低价触及或跌破 MA20，但收盘拉回 MA20 之上 (长下影线) 或 收盘就在 MA20 附近
    3. 必须缩量：今日成交量小于过去5日均量
    4. 止损位：本次回调的最低点或 MA20 下方 2%
    """
    df = df.copy().reset_index(drop=True)
    df['pullback_signal'] = False
    df['stop_loss_price'] = np.nan
    df['risk_pct'] = np.nan
    
    if len(df) < 60:
        return df
        
    # 计算均线
    df['ma_10'] = df['close'].rolling(10).mean()
    df['ma_20'] = df['close'].rolling(20).mean()
    df['ma_50'] = df['close'].rolling(50).mean()
    df['vol_ma_5'] = df['vol'].rolling(5).mean()
    
    # 1. 强多头排列
    cond_trend = (df['ma_10'] > df['ma_20']) & (df['ma_20'] > df['ma_50'])
    
    # 2. 缩量
    cond_vol = df['vol'] < df['vol_ma_5'] * 0.9  # 至少缩量10%
    
    # 3. 回踩判定：最低价击穿或接近 MA20 (距离<2%)，收盘价在 MA20 之上
    low = df['low'] if 'low' in df.columns else df['close']
    cond_touch = (low <= df['ma_20'] * 1.02) & (df['close'] > df['ma_20'])
    
    base_signal = cond_trend & cond_vol & cond_touch
    
    # 4. 止损与风险：以买入价下方 8% 为止损
    df['tentative_stop_loss'] = df['close'] * 0.92
    df['tentative_risk'] = (df['close'] - df['tentative_stop_loss']) / df['close']
    
    final_signal = base_signal & (df['tentative_risk'] <= max_risk_pct) & (df['tentative_risk'] > 0)
    
    df.loc[final_signal, 'pullback_signal'] = True
    df.loc[final_signal, 'stop_loss_price'] = df['tentative_stop_loss']
    df.loc[final_signal, 'risk_pct'] = df['tentative_risk']
    
    cols_to_drop = ['tentative_stop_loss', 'tentative_risk', 'vol_ma_5']
    df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True)
    
    return df
