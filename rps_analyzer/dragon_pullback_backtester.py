import pandas as pd
import numpy as np

def run_dragon_pullback_backtest(df: pd.DataFrame, initial_capital=1000000, max_pos_pct=0.3, 
                                 trailing_stop_pct=0.10, min_hold_days=0, 
                                 fixed_target_pct=None, breakeven_trigger_pct=None,
                                 verbose=True):
    """
    龙回头（极致缩量低吸）的历史回测引擎（向量化与事件驱动结合）
    单票仓位上限 30%，也就是满仓最多 3-4 只核心龙头。
    
    Args:
        min_hold_days: 最小持仓天数，买入后 N 天内不触发止损（适配 T+1）
        fixed_target_pct: 固定止盈目标（如 0.08 = 8%），设置后不再使用移动止盈
        breakeven_trigger_pct: 保本止损触发线（如 0.05 = 盈利 5% 后将止损移至成本价）
    """
    if verbose:
        print("开始 Dragon Pullback 猥琐低吸向量化回测...")
    
    if 'dragon_pullback_signal' not in df.columns:
        df['dragon_pullback_signal'] = False
        
    df['combined_signal'] = df['dragon_pullback_signal']
    
    dates = sorted(df['trade_date'].unique())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    df = df.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
    
    capital = initial_capital
    positions = {}
    
    nav_history = []
    trade_log = []
    
    for i, date in enumerate(dates):
        daily_data = df[df['trade_date'] == date]
        if daily_data.empty:
            continue
            
        current_value = capital
        to_sell = []
        
        for code, pos in positions.items():
            stock_row = daily_data[daily_data['ts_code'] == code]
            if stock_row.empty:
                current_value += pos['shares'] * pos['entry_price']
                continue
                
            close = float(stock_row['close'].iloc[0])
            low = float(stock_row['low'].iloc[0]) if 'low' in stock_row.columns else close
            open_price = float(stock_row['open'].iloc[0]) if 'open' in stock_row.columns else close
            
            hold_days = i - pos['entry_idx']
            gain_pct = (close - pos['entry_price']) / pos['entry_price']
            
            # 固定止盈：达到目标直接出场
            if fixed_target_pct and gain_pct >= fixed_target_pct:
                to_sell.append({'code': code, 'price': close, 'reason': 'FIXED_TARGET'})
                continue
            
            # 保本止损：盈利达到阈值后，止损上移至成本价
            if breakeven_trigger_pct and gain_pct >= breakeven_trigger_pct:
                breakeven_price = pos['entry_price'] * 1.002  # 成本价含手续费
                if breakeven_price > pos['stop_loss']:
                    pos['stop_loss'] = breakeven_price
            
            # 移动止盈（仅在未使用固定止盈时）
            if not fixed_target_pct:
                if close > pos['highest_price']:
                    pos['highest_price'] = close
                    trailing_stop = pos['highest_price'] * (1 - trailing_stop_pct)
                    if trailing_stop > pos['stop_loss']:
                        pos['stop_loss'] = trailing_stop
            
            # 最小持仓保护期：T+1 适配
            if hold_days < min_hold_days:
                current_value += pos['shares'] * close
                continue
            
            if low <= pos['stop_loss']:
                sell_price = min(open_price, pos['stop_loss'])
                to_sell.append({'code': code, 'price': sell_price, 'reason': 'STOP_LOSS_OR_PROFIT'})
            else:
                current_value += pos['shares'] * close
                
        # 执行卖出
        for sale in to_sell:
            code = sale['code']
            pos = positions[code]
            proceeds = pos['shares'] * sale['price']
            
            # 滑点和手续费 (双边共千分之二)
            proceeds *= (1 - 0.002) 
            
            capital += proceeds
            current_value += proceeds
            trade_log.append({
                'date': date,
                'code': code,
                'signal_type': pos['signal_type'],
                'action': 'SELL',
                'price': sale['price'],
                'shares': pos['shares'],
                'reason': sale['reason'],
                'pnl': (sale['price'] - pos['entry_price']) / pos['entry_price'] * 100
            })
            del positions[code]
            
        # 2. 检查新买入信号 (只有在大盘情绪达标时才低吸)
        # 这里的大盘情绪过滤已在外层传入的数据里做了预处理。
        signals = daily_data[daily_data['combined_signal'] == True]
        
        # 如果在同一个交易日有多个低吸信号，按近期的极度缩量程度（缩量越严重越好）和 RSI 强度排序
        if len(signals) > 0:
            if 'vol' in signals.columns:
                signals = signals.sort_values('vol', ascending=True) # 越缩量越靠前
                
            for _, signal_row in signals.iterrows():
                code = signal_row['ts_code']
                if code in positions:
                    continue # 已持有
                    
                # 核心：单票最大仓位上限 30%
                target_alloc = current_value * max_pos_pct
                actual_alloc = min(target_alloc, capital)
                
                # 如果可用现金不够建哪怕一手，或者低于总资金的5%，就不开新仓了
                if actual_alloc < current_value * 0.05:
                    continue 
                    
                buy_price = float(signal_row['close'])
                stop_loss = float(signal_row['stop_loss_price'])
                signal_type = 'DRAGON_PULLBACK'
                
                cost_adjusted_price = buy_price * (1 + 0.002)
                shares = int(actual_alloc / cost_adjusted_price / 100) * 100
                
                if shares == 0:
                    continue
                    
                cost = shares * cost_adjusted_price
                capital -= cost
                current_value -= cost
                current_value += shares * buy_price
                
                positions[code] = {
                    'shares': shares,
                    'entry_price': buy_price,
                    'stop_loss': stop_loss,
                    'highest_price': buy_price,
                    'signal_type': signal_type,
                    'entry_idx': i,
                }
                
                trade_log.append({
                    'date': date,
                    'code': code,
                    'signal_type': signal_type,
                    'action': 'BUY',
                    'price': buy_price,
                    'shares': shares,
                    'stop_loss': stop_loss
                })
            
        nav_history.append({'date': date, 'nav': current_value})
        
    return pd.DataFrame(nav_history), pd.DataFrame(trade_log)
