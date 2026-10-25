"""
龙回头策略多变体网格搜索优化器

参考已知高盈利模型设计变体：
  V0 (Baseline)   : 原始参数 — 止损2%, 缩量30%, 移动止盈10%
  V1 (宽止损)      : 止损5%, 其余同 V0 — 减少A股日内震荡假止损
  V2 (地量冰点)    : 止损5%, 缩量20% — 更极端的冰点，信号更少但更高质量
  V3 (快进快出)    : 止损5%, 固定止盈8% — 类 Connors RSI2 均值回归思路
  V4 (保本续命)    : 止损5%, 盈利5%后止损上移至成本 — 保护本金让利润奔跑
  V5 (T+1保护)    : 止损5%, 买入后3天内不止损 — 适配A股T+1
  V6 (综合最优)    : 止损5%, 缩量20%, 持仓保护2天, 保本5%, 移动止盈15%
  V7 (超宽+宽幅)   : 止损7%, 龙门槛25%, 回撤10-40% — 更宽容的信号条件
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import time

from project_paths import REPORTS_DIR, market_data_file, report_file

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from rps_analyzer.dragon_pullback_finder import identify_dragon_pullback
from rps_analyzer.dragon_pullback_backtester import run_dragon_pullback_backtest


VARIANTS = {
    'V0_基线(2%止损)': {
        'finder': dict(stop_loss_pct=0.02, vol_shrink_pct=0.30),
        'backtest': dict(trailing_stop_pct=0.10),
    },
    'V1_宽止损(5%)': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.30),
        'backtest': dict(trailing_stop_pct=0.10),
    },
    'V2_地量冰点': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.20),
        'backtest': dict(trailing_stop_pct=0.10),
    },
    'V3_快进快出(8%止盈)': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.30),
        'backtest': dict(trailing_stop_pct=0.10, fixed_target_pct=0.08),
    },
    'V4_保本续命': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.30),
        'backtest': dict(trailing_stop_pct=0.12, breakeven_trigger_pct=0.05),
    },
    'V5_T+1保护(3日)': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.30),
        'backtest': dict(trailing_stop_pct=0.10, min_hold_days=3),
    },
    'V6_综合最优': {
        'finder': dict(stop_loss_pct=0.05, vol_shrink_pct=0.20),
        'backtest': dict(trailing_stop_pct=0.15, min_hold_days=2, breakeven_trigger_pct=0.05),
    },
    'V7_超宽+放宽条件': {
        'finder': dict(stop_loss_pct=0.07, vol_shrink_pct=0.30, dragon_rise_pct=0.25, drawdown_min=0.10, drawdown_max=0.40),
        'backtest': dict(trailing_stop_pct=0.15, min_hold_days=2),
    },
}


def get_emotion_scores():
    score_csv = report_file("emotion_validation", "emotion_scores.csv")
    if not score_csv.exists():
        return None
    df = pd.read_csv(score_csv)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df[['trade_date', 'emotion_score']].copy()


def load_data():
    data_path = market_data_file("stock_daily.parquet")
    if not data_path.exists():
        print(f"数据文件不存在: {data_path}")
        sys.exit(1)
    df = pd.read_parquet(data_path)
    start_date = pd.to_datetime('2022-01-01')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['trade_date'] >= start_date].copy()
    top_stocks = df.groupby('ts_code')['amount'].mean().nlargest(500).index
    df_filtered = df[df['ts_code'].isin(top_stocks)].copy()
    return df_filtered, top_stocks


def apply_emotion_filter(df_with_signals, emotion_df):
    if emotion_df is None:
        return df_with_signals
    emotion_map = dict(zip(emotion_df['trade_date'], emotion_df['emotion_score']))
    
    def emotion_filter(row):
        if not row['dragon_pullback_signal']:
            return False
        score = emotion_map.get(row['trade_date'], 50.0)
        return score >= 37.5
    
    df_with_signals['dragon_pullback_signal'] = df_with_signals.apply(emotion_filter, axis=1)
    return df_with_signals


def run_variant(name, config, df_filtered, top_stocks, emotion_df):
    finder_params = config['finder']
    bt_params = config['backtest']
    
    results = []
    signal_count = 0
    
    for code in top_stocks:
        stock_data = df_filtered[df_filtered['ts_code'] == code].sort_values('trade_date').reset_index(drop=True)
        if len(stock_data) < 60:
            continue
        try:
            res = identify_dragon_pullback(stock_data, ma_support=20, max_risk_pct=0.08, **finder_params)
            if res['dragon_pullback_signal'].any():
                signal_count += res['dragon_pullback_signal'].sum()
                results.append(res)
        except Exception:
            pass
    
    if not results:
        return None
    
    df_with_signals = pd.concat(results, ignore_index=True)
    raw_signals = signal_count
    
    df_with_signals = apply_emotion_filter(df_with_signals, emotion_df)
    final_signals = int(df_with_signals['dragon_pullback_signal'].sum())
    
    if final_signals == 0:
        return None
    
    nav_df, trades_df = run_dragon_pullback_backtest(
        df_with_signals, initial_capital=1000000, max_pos_pct=0.30, 
        verbose=False, **bt_params
    )
    
    if trades_df.empty:
        return None
    
    final_nav = nav_df['nav'].iloc[-1]
    ret = (final_nav / 1000000) - 1
    
    days = (nav_df['date'].max() - nav_df['date'].min()).days
    annual_ret = ((final_nav / 1000000) ** (365 / max(days, 1)) - 1)
    
    nav_series = nav_df['nav']
    peak = nav_series.cummax()
    drawdown = (nav_series - peak) / peak
    drawdown = drawdown[drawdown > -0.99]
    max_dd = drawdown.min() * 100 if len(drawdown) > 0 else 0
    
    sells = trades_df[trades_df['action'] == 'SELL']
    n_trades = len(sells)
    
    if n_trades > 0:
        wins = sells[sells['pnl'] > 0]
        losses = sells[sells['pnl'] <= 0]
        win_rate = len(wins) / n_trades * 100
        avg_win = wins['pnl'].mean() if len(wins) > 0 else 0
        avg_loss = losses['pnl'].mean() if len(losses) > 0 else 0
        pnl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    else:
        win_rate = avg_win = avg_loss = pnl_ratio = 0
    
    return {
        'name': name,
        'raw_signals': raw_signals,
        'final_signals': final_signals,
        'total_return': ret * 100,
        'annual_return': annual_ret * 100,
        'max_drawdown': max_dd,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'pnl_ratio': pnl_ratio,
        'final_nav': final_nav,
    }


def main():
    print("=" * 80)
    print("  龙回头策略 — 多变体网格搜索优化器")
    print("  参考模型: Connors RSI2 / Turtle Trading / Minervini SEPA / A股低吸实战")
    print("=" * 80)
    
    print("\n加载数据...")
    df_filtered, top_stocks = load_data()
    emotion_df = get_emotion_scores()
    print(f"载入 {df_filtered['ts_code'].nunique()} 只龙头, {len(df_filtered)} 行数据\n")
    
    all_results = []
    
    for name, config in VARIANTS.items():
        t0 = time.time()
        print(f"  运行 {name} ...", end='', flush=True)
        result = run_variant(name, config, df_filtered, top_stocks, emotion_df)
        elapsed = time.time() - t0
        
        if result:
            all_results.append(result)
            print(f" 完成 ({elapsed:.1f}s) | 收益={result['total_return']:+.1f}% | 胜率={result['win_rate']:.1f}% | 盈亏比={result['pnl_ratio']:.2f}")
        else:
            print(f" 无信号 ({elapsed:.1f}s)")
    
    if not all_results:
        print("\n所有变体均无信号。")
        return
    
    print("\n" + "=" * 80)
    print("  优化结果对比排行榜 (按总收益排序)")
    print("=" * 80)
    
    header = f"{'变体名称':<22} {'总收益':>8} {'年化':>7} {'最大回撤':>8} {'胜率':>6} {'盈亏比':>6} {'均盈':>7} {'均亏':>7} {'交易数':>5} {'信号数':>5}"
    print(header)
    print("-" * len(header) + "---" * 5)
    
    all_results.sort(key=lambda x: x['total_return'], reverse=True)
    
    for r in all_results:
        print(f"{r['name']:<22} {r['total_return']:>+7.1f}% {r['annual_return']:>+6.1f}% {r['max_drawdown']:>+7.1f}% "
              f"{r['win_rate']:>5.1f}% {r['pnl_ratio']:>6.2f} {r['avg_win']:>+6.1f}% {r['avg_loss']:>+6.1f}% "
              f"{r['n_trades']:>5} {r['final_signals']:>5}")
    
    best = all_results[0]
    worst = all_results[-1]
    
    print(f"\n最优变体: {best['name']}")
    print(f"  总收益 {best['total_return']:+.2f}% | 年化 {best['annual_return']:+.2f}% | 最大回撤 {best['max_drawdown']:.2f}%")
    print(f"  胜率 {best['win_rate']:.1f}% | 盈亏比 {best['pnl_ratio']:.2f} | 交易 {best['n_trades']} 笔")
    
    improvement = best['total_return'] - worst['total_return']
    print(f"\n最优 vs 最差改善幅度: {improvement:+.1f}%")
    
    report_dir = REPORTS_DIR / "dragon_optimization"
    report_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_results).to_csv(report_dir / "optimization_results.csv", index=False, encoding='utf-8-sig')
    print(f"\n详细结果已保存至: {report_dir}")
    print("=" * 80)


if __name__ == '__main__':
    main()
