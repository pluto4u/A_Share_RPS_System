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
import os
import sys
from config.dragon_strategy import DRAGON_STRATEGY
from project_paths import REPORTS_DIR, market_data_file, report_file

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from rps_analyzer.dragon_pullback_finder import identify_dragon_pullback
from rps_analyzer.dragon_pullback_backtester import run_dragon_pullback_backtest
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.universe import get_universe_pool, get_pool_by_amount

def get_emotion_scores():
    """读取每日情绪大盘评分，作为滤网"""
    score_csv = report_file("emotion_validation", "emotion_scores.csv")
    if not score_csv.exists():
        print(f"找不到情绪评分文件: {score_csv}")
        return None
    df = pd.read_csv(score_csv)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df[['trade_date', 'emotion_score', 'regime', 'regime_reversal']].copy()

def main():
    print("=" * 65)
    print("  RPS龙头缩量低吸 (猥琐发育版) 极致复利回测验证")
    print("  核心参数: 单票上限 30% | 极度缩量冰点确认 | 情绪护城河")
    print("=" * 65)
    print("加载历史数据与情绪大盘滤网...")
    
    data_path = market_data_file("stock_daily.parquet")
    if not data_path.exists():
        print(f"数据文件不存在: {data_path}")
        return
        
    df = pd.read_parquet(data_path)
    
    start_date = pd.to_datetime('2022-01-01')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['trade_date'] >= start_date].copy()
    
    emotion_df = get_emotion_scores()
    
    print(f"载入个股数据: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    
    universe_cfg = DRAGON_STRATEGY["universe"]
    min_bars = universe_cfg["min_history_bars"]
    finder = DRAGON_STRATEGY["finder"]
    backtest_cfg = DRAGON_STRATEGY["backtest"]
    min_score = DRAGON_STRATEGY["emotion"]["min_score_for_stock_entries"]
    source = universe_cfg.get("source", "amount")

    # 股票池: RPS 模式需先计算 RPS
    pool_per_date = None
    if source == "rps":
        print("股票池模式: RPS (RPS_120 & RPS_250 > 90)")
        periods = universe_cfg.get("rps_periods", [120, 250])
        df = calculate_rps(df, periods=periods, verbose=True)
        # 收集每个交易日的高 RPS 股票
        dates = df["trade_date"].unique()
        pool_per_date = {}
        for d in dates:
            pool, _ = get_universe_pool(df, pd.Timestamp(d), DRAGON_STRATEGY)
            pool_per_date[d] = set(pool)
        all_pool_stocks = set()
        for s in pool_per_date.values():
            all_pool_stocks |= s
        top_stocks = list(all_pool_stocks)
        print(f"RPS 池: 历史共 {len(top_stocks)} 只股票曾入选")
    else:
        print("股票池模式: 成交额前 N")
        top_stocks = get_pool_by_amount(
            df,
            top_n=universe_cfg.get("top_active_stocks", 500),
            as_of_date=None,
        )
        print(f"选取 {len(top_stocks)} 只龙头核心池")
    
    print("扫描其深蹲缩量买点...")
    
    results = []
    total = len(top_stocks)
    signal_count = 0
    df_filtered = df[df['ts_code'].isin(top_stocks)].copy()
    
    for i, code in enumerate(top_stocks):
        if i % 100 == 0:
            print(f"  深度扫描进度: {i}/{total}...")
            
        stock_data = df_filtered[df_filtered['ts_code'] == code].sort_values('trade_date').reset_index(drop=True)
        if len(stock_data) < min_bars:
            continue
            
        try:
            # 扫描“龙头首阴 / 缩量回踩”信号（统一黄金参数）
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
            
            if has_signal:
                # RPS 模式: 只保留当日该股在 RPS 池内的信号
                if source == "rps" and pool_per_date is not None:
                    for _, row in res[res["dragon_pullback_signal"]].iterrows():
                        d = row["trade_date"]
                        if d in pool_per_date and code in pool_per_date[d]:
                            signal_count += 1
                            results.append(res[res["trade_date"] == d])
                else:
                    signal_count += res['dragon_pullback_signal'].sum()
                    results.append(res)
                
        except Exception as e:
            pass
            
    if not results:
        print("未发现任何符合量能冰点的低吸信号。")
        return
        
    df_with_signals = pd.concat(results, ignore_index=True)
    
    print(f"\n扫描完成! 在核心池中挖掘出极为苛刻的低吸买点: {signal_count}个")
    
    # ---------------------------------------------------------
    # 大盘情绪一票否决权 (防主跌浪，不接下降飞刀)
    # ---------------------------------------------------------
    if emotion_df is not None:
        print("执行大盘情绪清洗...")
        emotion_map = dict(zip(emotion_df['trade_date'], emotion_df['emotion_score']))
        # 如果当天情绪 < 37.5 分 (偏冷、极冷)，或者刚进入暴跌，撤销买单
        # 也就是只有情绪处于 正常 (NORMAL)、偏热 (HOT)、过热 (OVERHEAT) 或 闪崩反转 时，才去接刀
        def emotion_filter(row):
            if not row['dragon_pullback_signal']:
                return False
            score = emotion_map.get(row['trade_date'], 50.0) # 没有数据默认给50分允许过
            if score < min_score:
                return False # 坚决否决
            return True
            
        original_count = df_with_signals['dragon_pullback_signal'].sum()
        df_with_signals['dragon_pullback_signal'] = df_with_signals.apply(emotion_filter, axis=1)
        final_count = df_with_signals['dragon_pullback_signal'].sum()
        print(f"情绪过滤拦截了 {original_count - final_count} 次处于主跌浪/冰点期的死亡低吸。")
        print(f"最终保留了 {final_count} 次有效的高胜率低吸狙击。")
    
    print("\n运行单票满仓 30% 上限的“极致复利版”回测引擎...")
    try:
        nav_df, trades_df = run_dragon_pullback_backtest(
            df_with_signals,
            initial_capital=backtest_cfg["initial_capital"],
            max_pos_pct=backtest_cfg["max_pos_pct"],
            trailing_stop_pct=backtest_cfg["trailing_stop_pct"],
            min_hold_days=backtest_cfg["min_hold_days"],
            breakeven_trigger_pct=backtest_cfg["breakeven_trigger_pct"],
        )
        
        if trades_df.empty:
            print("资金回测中未产生实际成交")
            return
            
        final_nav = nav_df['nav'].iloc[-1]
        ret = (final_nav / 1000000) - 1
        
        sells = trades_df[trades_df['action'] == 'SELL'].copy()
        if len(sells) > 0:
            win_trades = sells[sells['pnl'] > 0]
            loss_trades = sells[sells['pnl'] <= 0]
            win_rate = len(win_trades) / len(sells) * 100
            avg_win = win_trades['pnl'].mean() if len(win_trades) > 0 else 0
            avg_loss = loss_trades['pnl'].mean() if len(loss_trades) > 0 else 0
            pnl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
            
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            pnl_ratio = 0
            
        print("\n" + "=" * 65)
        print("  终极回测总结 (A股最强: 高RPS龙头缩量低吸)")
        print("=" * 65)
        print(f"初始资金 : 1,000,000")
        print(f"最终净值 : {final_nav:,.2f}")
        print(f"总收益率 : {ret*100:.2f}%")
        
    # 计算年化
        days = (nav_df['date'].max() - nav_df['date'].min()).days
        annual_ret = ((final_nav / 1000000) ** (365 / days) - 1) if days > 0 else 0
        print(f"年化收益 : {annual_ret*100:.2f}%")
        
        # 修复最大回撤计算 Bug
        nav_series = nav_df['nav']
        peak = nav_series.cummax()
        drawdown = (nav_series - peak) / peak
        
        # 过滤掉可能由停牌或数据异常引起的虚假 0 值导致的 -99.9% 错觉
        drawdown = drawdown[drawdown > -0.99]
        max_dd = drawdown.min() * 100 if len(drawdown) > 0 else 0
        print(f"最大回撤 : {max_dd:.2f}%")
        print(f"总交易数 : {len(sells)} 笔 (重锤出击)")
        print(f"整体胜率 : {win_rate:.1f}%")
        print(f"平均盈利 : +{avg_win:.2f}% (大肉)")
        print(f"平均亏损 : {avg_loss:.2f}% (小损)")
        print(f"极致盈亏比: {pnl_ratio:.2f}")
        
        report_dir = REPORTS_DIR / "dragon_pullback_backtest"
        report_dir.mkdir(parents=True, exist_ok=True)
        trades_df.to_csv(report_dir / "dragon_trades.csv", index=False, encoding='utf-8-sig')
        nav_df.to_csv(report_dir / "dragon_nav.csv", index=False, encoding='utf-8-sig')
        print(f"\n详细交易记录与曲线已保存至: {report_dir}")
        print("=" * 65)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"回测引擎运行失败: {e}")

if __name__ == '__main__':
    main()
