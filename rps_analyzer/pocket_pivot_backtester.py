import pandas as pd
import numpy as np

def run_pocket_pivot_backtest(df: pd.DataFrame, initial_capital=1000000, max_pos_pct=0.2, trailing_stop_pct=0.10):
    """
    口袋支点与强趋势回踩的历史回测引擎（向量化与事件驱动结合）
    """
    print("开始 Pocket Pivot / Pullback 向量化回测...")
    
    # 提取所有存在的买点信号
    if 'pocket_pivot_signal' not in df.columns:
        df['pocket_pivot_signal'] = False
    if 'pullback_signal' not in df.columns:
        df['pullback_signal'] = False
        
    df['combined_signal'] = df['pocket_pivot_signal'] | df['pullback_signal']
    
    dates = sorted(df['trade_date'].unique())
    df = df.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
    
    capital = initial_capital
    positions = {} # ts_code -> {'shares': 0, 'entry_price': 0, 'stop_loss': 0, 'highest_price': 0, 'signal_type': ''}
    
    nav_history = []
    trade_log = []
    
    for i, date in enumerate(dates):
        daily_data = df[df['trade_date'] == date]
        if daily_data.empty:
            continue
            
        current_value = capital
        to_sell = []
        
        # 1. 持仓管理与退出判定
        for code, pos in positions.items():
            stock_row = daily_data[daily_data['ts_code'] == code]
            if stock_row.empty:
                current_value += pos['shares'] * pos['entry_price']
                continue
                
            close = float(stock_row['close'].iloc[0])
            low = float(stock_row['low'].iloc[0]) if 'low' in stock_row.columns else close
            open_price = float(stock_row['open'].iloc[0]) if 'open' in stock_row.columns else close
            
            # 更新最高价用于移动止盈
            if close > pos['highest_price']:
                pos['highest_price'] = close
                # 移动止盈线
                trailing_stop = pos['highest_price'] * (1 - trailing_stop_pct)
                if trailing_stop > pos['stop_loss']:
                    pos['stop_loss'] = trailing_stop
            
            # 止损/止盈检查 (跌破即出)
            if low <= pos['stop_loss']:
                sell_price = min(open_price, pos['stop_loss'])
                to_sell.append({'code': code, 'price': sell_price, 'reason': 'STOP_LOSS'})
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
            
        # 2. 检查新买入信号
        signals = daily_data[daily_data['combined_signal'] == True]
        
        # 为了更贴近实战，如果在同一个交易日有多个信号，我们按过去成交量排序买入最活跃的
        if len(signals) > 0:
            if 'vol' in signals.columns:
                signals = signals.sort_values('vol', ascending=False)
                
            for _, signal_row in signals.iterrows():
                code = signal_row['ts_code']
                if code in positions:
                    continue # 已持有
                    
                target_alloc = current_value * max_pos_pct
                actual_alloc = min(target_alloc, capital)
                
                if actual_alloc < 10000:
                    continue # 现金不足
                    
                buy_price = float(signal_row['close'])
                stop_loss = float(signal_row['stop_loss_price'])
                signal_type = 'POCKET_PIVOT' if signal_row['pocket_pivot_signal'] else 'PULLBACK'
                
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
                    'signal_type': signal_type
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

if __name__ == '__main__':
    pass