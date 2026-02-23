import pandas as pd
import sys
import logging

# Setup basic logging
logging.basicConfig(level=logging.WARNING)

sys.path.append('.')
from emotion_validation.etf_rotation.backtest_rotation import run_backtest

def main():
    results = []
    
    for freq in ['M', '5D', '3D']:
        print(f'Running backtest for freq={freq}...')
        res = run_backtest(start_date='2024-01-01', top_n=2, freq=freq)
        metrics = res['metrics']
        
        # Extract D strategy metrics
        d_metric = next(m for m in metrics if m['label'].startswith('D'))
        
        results.append({
            'Frequency': freq,
            'Annual_Return(%)': d_metric['annual_ret'],
            'Max_Drawdown(%)': d_metric['max_dd'],
            'Sharpe': d_metric['sharpe'],
            'Win_Rate(%)': d_metric['win_rate'],
            'Trades': d_metric['n_trades']
        })
        
    df = pd.DataFrame(results)
    print('\n=== ETF Rotation Frequency Comparison (Strategy D: S5 + ETF Rotation) ===')
    print('Cost: 0.0001 (1bp) one way (2bp round trip)')
    print(df.to_string(index=False))

if __name__ == '__main__':
    main()
