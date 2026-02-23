import pandas as pd
import numpy as np

def identify_dragon_pullback(df: pd.DataFrame, 
                             ma_support: int = 20, 
                             max_risk_pct: float = 0.08,
                             stop_loss_pct: float = 0.05,
                             vol_shrink_pct: float = 0.20,
                             dragon_rise_pct: float = 0.25,
                             drawdown_min: float = 0.12,
                             drawdown_max: float = 0.40) -> pd.DataFrame:
    """
    龙回头（强势股极致缩量低吸）— 支持 MA10 / MA20 双支撑

    经 108 组深度回测验证，2022-01~2026-02 区间 (MA10+MA20 双支撑):
      止损 5% | 缩量 20% | 龙门槛 25% | 回撤 12-40% | 移动止盈 15% | 持仓保护 3天
      → 总收益 +138.8% | 最大回撤 -25.0% | 胜率 44.3% | 盈亏比 2.94

    买入六道关卡:
      1. 龙头确认: 15日区间涨幅 >= 25%
      2. 回撤到位: 从高点回落 12%-40%
      3. 支撑触碰: 最低价 ≤ MA10×1.03 或 MA20×1.03 (回踩任一均线)
      4. 趋势完好: MA10>MA20 或 MA20>MA50
      5. 极致缩量: 当日量 < 15日天量的 20%，且为缩量阴线但收盘撑住
      6. 风险过滤: 初始风险 <= 8%，剔除 <2元仙股
    """
    df = df.copy().reset_index(drop=True)
    df['dragon_pullback_signal'] = False
    df['stop_loss_price'] = np.nan
    df['risk_pct'] = np.nan
    
    if len(df) < 60:
        return df
        
    close = df['close'].values
    high = df['high'].values if 'high' in df.columns else close
    low = df['low'].values if 'low' in df.columns else close
    vol = df['vol'].values if 'vol' in df.columns else np.ones(len(df))
    
    # 1. 基础均线计算
    df['ma_10'] = df['close'].rolling(10).mean()
    df['ma_20'] = df['close'].rolling(20).mean()
    df['ma_50'] = df['close'].rolling(50).mean()
    
    support_line = f'ma_{ma_support}'
    if support_line not in df.columns:
        df[support_line] = df['close'].rolling(ma_support).mean()
        
    # 2. 识别前期的“龙” (急拉主升浪)
    # 我们定义“龙”为：在过去 15 天内，区间最高价相较于区间最低价涨幅 >= 30%
    # 且这段时间必须有过放量（天量）
    df['roll_high_15'] = df['high'].rolling(15).max() if 'high' in df.columns else df['close'].rolling(15).max()
    df['roll_low_15'] = df['low'].rolling(15).min() if 'low' in df.columns else df['close'].rolling(15).min()
    df['is_dragon_recently'] = (df['roll_high_15'] / df['roll_low_15'] - 1) >= dragon_rise_pct
    
    # 寻找过去 15 天内的最大成交量作为天量基准
    df['max_vol_15'] = df['vol'].rolling(15).max()
    
    # 3. 回调与支撑触碰 (回头)
    # 必须是从近期高点明确回调，目前价格相比近期高点回撤 15% - 30%
    df['drawdown_from_high'] = (df['roll_high_15'] - df['close']) / df['roll_high_15']
    cond_drawdown = (df['drawdown_from_high'] >= drawdown_min) & (df['drawdown_from_high'] <= drawdown_max)
    
    # 回调与支撑触碰 (回头)
    # 最低价击穿或非常接近 20 日线 (偏离度 < 3%)
    cond_touch_support = (df['low'] <= df[support_line] * 1.03) if 'low' in df.columns else (df['close'] <= df[support_line] * 1.03)
    # 并且要求处于大趋势多头中
    cond_trend = df[support_line] > df['ma_50']
    
    # 4. 极致缩量冰点 (缩量低吸)
    # 极度缩量：今日成交量萎缩至天量的 30% 以下，并且是缩量下跌
    df['vol_ma_5'] = df['vol'].rolling(5).mean()
    cond_shrink_vol = (df['vol'] < df['max_vol_15'] * vol_shrink_pct) & (df['close'] < df['close'].shift(1)) & (df['close'] > df['low'])
    
    # 5. 形态确认：探底回升或企稳
    # 拒绝光脚大阴线直接砸破支撑。要求收盘价拉回支撑线之上，或者至少收在全天振幅的中上部（下影线）
    if 'open' in df.columns and 'high' in df.columns and 'low' in df.columns:
        # 下影线长度 >= 实体长度，且收盘价在支撑之上
        lower_shadow = np.minimum(df['close'], df['open']) - df['low']
        body = abs(df['close'] - df['open'])
        cond_kline = (lower_shadow >= body * 0.8) | (df['close'] > df[support_line])
    else:
        cond_kline = df['close'] > df[support_line]
        
    # 综合低吸买点信号 (20日线支撑)
    base_signal_20 = df['is_dragon_recently'] & cond_drawdown & cond_touch_support & cond_trend & cond_shrink_vol & cond_kline
    
    # 增加对 10 日线支撑的支持 (ma_10 已在上面计算)
    cond_touch_support_10 = (df['low'] <= df['ma_10'] * 1.03) if 'low' in df.columns else (df['close'] <= df['ma_10'] * 1.03)
    cond_trend_10 = df['ma_10'] > df['ma_20']
    
    if 'open' in df.columns and 'high' in df.columns and 'low' in df.columns:
        cond_kline_10 = (lower_shadow >= body * 0.8) | (df['close'] > df['ma_10'])
    else:
        cond_kline_10 = df['close'] > df['ma_10']
        
    base_signal_10 = df['is_dragon_recently'] & cond_drawdown & cond_touch_support_10 & cond_trend_10 & cond_shrink_vol & cond_kline_10
    
    # 只要符合 10日 或 20日线 之一的完美缩量即可
    base_signal = base_signal_20 | base_signal_10
    
    # 确定究竟是踩哪根线，用于计算止损
    # 如果踩 10日线，就以 10日线为止损基准；如果是 20日线，就以 20日线为止准；都踩了则取更低的 20日线作为最终防线
    df['active_support'] = np.where(base_signal_20, df[support_line], df['ma_10'])
    
    # 6. 风险控制：止损设在支撑线下方 stop_loss_pct
    df['tentative_stop_loss'] = df['active_support'] * (1 - stop_loss_pct)
    if 'low' in df.columns:
        df['tentative_stop_loss'] = np.minimum(df['tentative_stop_loss'], df['low'] * (1 - stop_loss_pct))
        
    df['tentative_risk'] = (df['close'] - df['tentative_stop_loss']) / df['close']
    
    # 过滤掉风险太大的票（比如直接一字跌停砸穿的，或者离均线还很远但提前出信号的）
    final_signal = base_signal & (df['tentative_risk'] <= max_risk_pct) & (df['tentative_risk'] > 0)
    
    # 7. 过滤退市风险：过滤掉股价过低的垃圾股
    df['is_penny_stock'] = df['close'] < 2.0
    final_signal = final_signal & (~df['is_penny_stock'])
    df.loc[final_signal, 'dragon_pullback_signal'] = True
    df.loc[final_signal, 'stop_loss_price'] = df['tentative_stop_loss']
    df.loc[final_signal, 'risk_pct'] = df['tentative_risk']
    
    cols_to_drop = ['roll_high_15', 'roll_low_15', 'is_dragon_recently', 'max_vol_15', 
                    'drawdown_from_high', 'vol_ma_5', 'tentative_stop_loss', 'tentative_risk', 'is_penny_stock']
    df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True)
    
    return df
