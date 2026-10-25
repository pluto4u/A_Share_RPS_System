"""
龙回头策略深度打磨：V6与V7专属深网格搜索

基于上一轮的结果（V6稳健、V7收益最高），结合A股龙回头的经典理论：
1. 回撤深度：黄金分割 (38.2%, 50%, 61.8%)，对应从最高点跌幅的 15%-40%
2. 极限缩量：缩量至前高的 20-30%
3. 止损防线：不破核心均线，允许均线下 3-8% 的宽幅震荡（防洗盘）
4. T+1持仓保护：买入后 1-3 天不硬止损

我们要寻找 V6 和 V7 之间的帕累托最优前沿。
"""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import pandas as pd
import numpy as np
from pathlib import Path
import sys
import time
from itertools import product

from project_paths import REPORTS_DIR, market_data_file, report_file

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from rps_analyzer.dragon_pullback_finder import identify_dragon_pullback
from rps_analyzer.dragon_pullback_backtester import run_dragon_pullback_backtest

# 定义深层网格空间
PARAM_GRID = {
    'stop_loss_pct': [0.05, 0.06, 0.07],             # 止损宽容度 3
    'vol_shrink_pct': [0.20, 0.25, 0.30],            # 缩量苛刻度 3
    'drawdown_max': [0.35, 0.40],                    # 回调深度下限 2
    'min_hold_days': [1, 2, 3],                      # T+1 持仓保护期 3
    'trailing_stop_pct': [0.10, 0.15],               # 移动止盈宽容度 2
} # 3x3x2x3x2 = 108 组

# 固定参数 (V6/V7中已经验证有效的设定)
FIXED_PARAMS = {
    'dragon_rise_pct': 0.25,      # 龙门槛放宽到 25% (V7)
    'drawdown_min': 0.12,         # 避免高位微调即买入
    'breakeven_trigger_pct': 0.05 # 保本续命开启 (V6)
}

def get_emotion_scores():
    score_csv = report_file("emotion_validation", "emotion_scores.csv")
    if not score_csv.exists(): return None
    df = pd.read_csv(score_csv)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df[['trade_date', 'emotion_score']].copy()

def load_data():
    data_path = market_data_file("stock_daily.parquet")
    df = pd.read_parquet(data_path)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['trade_date'] >= pd.to_datetime('2022-01-01')].copy()
    top_stocks = df.groupby('ts_code')['amount'].mean().nlargest(500).index
    return df[df['ts_code'].isin(top_stocks)].copy(), top_stocks

def apply_emotion_filter(df_with_signals, emotion_df):
    if emotion_df is None: return df_with_signals
    emotion_map = dict(zip(emotion_df['trade_date'], emotion_df['emotion_score']))
    def emotion_filter(row):
        if not row['dragon_pullback_signal']: return False
        return emotion_map.get(row['trade_date'], 50.0) >= 37.5
    df_with_signals['dragon_pullback_signal'] = df_with_signals.apply(emotion_filter, axis=1)
    return df_with_signals

def main():
    print("=" * 80)
    print("  深度打磨：V6与V7专属深网格搜索 (参数空间 108 组)")
    print("=" * 80)
    df_filtered, top_stocks = load_data()
    emotion_df = get_emotion_scores()
    
    keys = list(PARAM_GRID.keys())
    grid_values = list(product(*PARAM_GRID.values()))
    total = len(grid_values)
    
    # 预计算每只股票的数据，避免重复过滤
    stock_cache = {}
    for code in top_stocks:
        stock_data = df_filtered[df_filtered['ts_code'] == code].sort_values('trade_date').reset_index(drop=True)
        if len(stock_data) >= 60:
            stock_cache[code] = stock_data
    print(f"载入数据完毕，{len(stock_cache)} 只有效股票，即将搜索 {total} 组参数...\n")
    
    # 信号缓存：相同 finder 参数只扫描一次
    signal_cache = {}
    
    all_results = []
    start_t = time.time()
    
    for i, vals in enumerate(grid_values):
        params = dict(zip(keys, vals))
        
        if i % 12 == 0:
            elapsed_so_far = time.time() - start_t
            eta = (elapsed_so_far / max(i, 1)) * (total - i)
            print(f"进度: {i}/{total} | 已用 {elapsed_so_far:.0f}s | 预计剩余 {eta:.0f}s")
        
        # 信号扫描只取决于 finder 参数，缓存它
        finder_key = (params['stop_loss_pct'], params['vol_shrink_pct'], params['drawdown_max'])
        
        if finder_key not in signal_cache:
            finder_params = {
                'stop_loss_pct': params['stop_loss_pct'],
                'vol_shrink_pct': params['vol_shrink_pct'],
                'drawdown_max': params['drawdown_max'],
                'dragon_rise_pct': FIXED_PARAMS['dragon_rise_pct'],
                'drawdown_min': FIXED_PARAMS['drawdown_min']
            }
            results_list = []
            for code, stock_data in stock_cache.items():
                try:
                    res = identify_dragon_pullback(stock_data, ma_support=20, max_risk_pct=0.08, **finder_params)
                    if res['dragon_pullback_signal'].any():
                        results_list.append(res)
                except Exception:
                    pass
            
            if results_list:
                df_signals = pd.concat(results_list, ignore_index=True)
                df_signals = apply_emotion_filter(df_signals.copy(), emotion_df)
                signal_cache[finder_key] = df_signals
            else:
                signal_cache[finder_key] = None
        
        df_with_signals = signal_cache[finder_key]
        if df_with_signals is None:
            continue
        
        final_signals = int(df_with_signals['dragon_pullback_signal'].sum())
        if final_signals == 0:
            continue
        
        bt_params = {
            'trailing_stop_pct': params['trailing_stop_pct'],
            'min_hold_days': params['min_hold_days'],
            'breakeven_trigger_pct': FIXED_PARAMS['breakeven_trigger_pct']
        }
        
        try:
            nav_df, trades_df = run_dragon_pullback_backtest(
                df_with_signals.copy(), initial_capital=1000000, max_pos_pct=0.30, verbose=False, **bt_params
            )
        except Exception:
            continue
        
        if trades_df.empty:
            continue
        
        final_nav = nav_df['nav'].iloc[-1]
        ret = (final_nav / 1000000) - 1
        nav_series = nav_df['nav']
        drawdown = (nav_series - nav_series.cummax()) / nav_series.cummax()
        max_dd = drawdown[drawdown > -0.99].min() * 100 if len(drawdown[drawdown > -0.99]) > 0 else 0
        
        sells = trades_df[trades_df['action'] == 'SELL']
        wins = sells[sells['pnl'] > 0]
        losses = sells[sells['pnl'] <= 0]
        
        all_results.append({
            'params_str': f"S:{params['stop_loss_pct']}_V:{params['vol_shrink_pct']}_D:{params['drawdown_max']}_H:{params['min_hold_days']}_T:{params['trailing_stop_pct']}",
            'total_return': ret * 100,
            'max_drawdown': max_dd,
            'win_rate': (len(wins) / len(sells) * 100) if len(sells) > 0 else 0,
            'pnl_ratio': abs(wins['pnl'].mean() / losses['pnl'].mean()) if len(wins) > 0 and len(losses) > 0 else 0,
            'n_trades': len(sells),
            'signals': final_signals,
            **params
        })
            
    elapsed = time.time() - start_t
    print(f"\n搜索完毕，总耗时 {elapsed/60:.1f} 分钟。有效结果 {len(all_results)} 组。")
    
    df_res = pd.DataFrame(all_results)
    df_res = df_res.sort_values('total_return', ascending=False)
    
    report_dir = REPORTS_DIR / "dragon_optimization"
    report_dir.mkdir(parents=True, exist_ok=True)
    df_res.to_csv(report_dir / "deep_optimization_results.csv", index=False)
    
    print("\n" + "=" * 80)
    print("  Top 10 终极黄金参数组合 (按总收益排序)")
    print("=" * 80)
    
    header = f"{'参数简写':<35} {'收益':>8} {'回撤':>8} {'胜率':>6} {'盈亏比':>6} {'交易数'}"
    print(header)
    print("-" * len(header))
    
    for _, r in df_res.head(10).iterrows():
        print(f"{r['params_str']:<35} {r['total_return']:>+7.1f}% {r['max_drawdown']:>+7.1f}% "
              f"{r['win_rate']:>5.1f}% {r['pnl_ratio']:>6.2f} {r['n_trades']:>5}")

    print(f"\n详细搜索结果已保存至: {report_dir / 'deep_optimization_results.csv'}")
    print("=" * 80)

if __name__ == '__main__':
    main()
