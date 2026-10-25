"""
每日龙回头低吸扫描器 — 黄金参数版

在情绪允许的前提下 (S5 >= 37.5)，扫描 RPS 龙头池中
今日出现"极致缩量冰点"低吸买点的个股，并附带完整的
止损价、风险百分比、持仓建议。

股票池与 ETF 轮动完全独立，由 config 的 universe.source 决定 (rps | amount)。
"""
import pandas as pd
import numpy as np
from pathlib import Path
from config.dragon_strategy import DRAGON_STRATEGY
from rps_analyzer.dragon_pullback_finder import identify_dragon_pullback
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.universe import get_universe_pool
from project_paths import market_data_file


def get_dragon_pullback_candidates(emotion_score: float, limit: int = 5) -> tuple[list, dict]:
    """
    获取当日龙回头低吸候选标的。

    Returns:
        (candidates: list of dict, meta: {pool_mode, pool_size})
        meta 用于 today_action 展示，与 ETF 无关
    """
    min_score = DRAGON_STRATEGY["emotion"]["min_score_for_stock_entries"]
    if emotion_score < min_score:
        return [], {"pool_mode": "", "pool_size": 0}
        
    data_path = market_data_file("stock_daily.parquet")
    if not data_path.exists():
        return [], {"pool_mode": "", "pool_size": 0}
        
    df = pd.read_parquet(data_path)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    latest_date = df['trade_date'].max()
    
    universe_cfg = DRAGON_STRATEGY["universe"]
    min_bars = universe_cfg["min_history_bars"]
    source = universe_cfg.get("source", "amount")

    # 股票池 (与 ETF 轮动独立)
    if source == "rps":
        periods = universe_cfg.get("rps_periods", [120, 250])
        df = calculate_rps(df, periods=periods, verbose=False)
    top_stocks, pool_mode = get_universe_pool(df, pd.Timestamp(latest_date), DRAGON_STRATEGY)
    meta = {"pool_mode": pool_mode.upper(), "pool_size": len(top_stocks)}

    # 至少需要 min_bars 天历史
    start_date = latest_date - pd.Timedelta(days=max(300, min_bars * 2))
    df_recent = df[df['trade_date'] >= start_date].copy()
    df_filtered = df_recent[df_recent['ts_code'].isin(top_stocks)].copy()
    
    candidates = []
    
    for code in top_stocks:
        stock_data = df_filtered[df_filtered['ts_code'] == code].sort_values('trade_date').reset_index(drop=True)
        if len(stock_data) < min_bars:
            continue
            
        try:
            finder = DRAGON_STRATEGY["finder"]
            res = identify_dragon_pullback(
                stock_data,
                ma_support=finder["ma_support"],
                max_risk_pct=finder["max_risk_pct"],
                stop_loss_pct=finder["stop_loss_pct"],
                vol_shrink_pct=finder["vol_shrink_pct"],
                dragon_rise_pct=finder["dragon_rise_pct"],
                drawdown_min=finder["drawdown_min"],
                drawdown_max=finder["drawdown_max"],
            )
            last_row = res.iloc[-1]
            
            if last_row.get('dragon_pullback_signal', False):
                vol_ma_5 = stock_data['vol'].rolling(5).mean().iloc[-2] if len(stock_data) > 1 else 1
                vol_ratio = last_row['vol'] / vol_ma_5 if vol_ma_5 > 0 else 1.0
                
                # 判断是 10日线 还是 20日线 (浮点比较用容差)
                support_type = "MA20"
                a = last_row.get('active_support', np.nan)
                m10 = last_row.get('ma_10', np.nan)
                if not np.isnan(a) and not np.isnan(m10) and abs(a - m10) < 1e-6:
                    support_type = "MA10"
                
                candidates.append({
                    'symbol': code,
                    'type': f'龙回头({support_type})',
                    'price': last_row['close'],
                    'stop_loss': last_row['stop_loss_price'],
                    'risk_pct': last_row['risk_pct'] * 100,
                    'vol_ratio': vol_ratio,
                    'max_pos_pct': int(DRAGON_STRATEGY["backtest"]["max_pos_pct"] * 100),
                    'min_hold_days': DRAGON_STRATEGY["backtest"]["min_hold_days"],
                    'trailing_stop_pct': int(DRAGON_STRATEGY["backtest"]["trailing_stop_pct"] * 100),
                    'breakeven_trigger_pct': int(DRAGON_STRATEGY["backtest"]["breakeven_trigger_pct"] * 100),
                })
                
        except Exception:
            pass
    
    candidates.sort(key=lambda x: x['risk_pct'])
    return candidates[:limit], meta
