import pandas as pd
import numpy as np
from pathlib import Path
import os
import sys

from project_paths import REPORTS_DIR, market_data_file

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from rps_analyzer.pocket_pivot_finder_v2 import identify_pocket_pivot_v2, identify_trend_pullback
from rps_analyzer.pocket_pivot_backtester import run_pocket_pivot_backtest

def main():
    print("=" * 60)
    print("  RPS+口袋支点/均线回踩 极致复利系统 - 历史回测验证")
    print("=" * 60)
    print("加载历史数据...")
    
    data_path = market_data_file("stock_daily.parquet")
    if not data_path.exists():
        print(f"数据文件不存在: {data_path}")
        return
        
    df = pd.read_parquet(data_path)
    
    start_date = pd.to_datetime('2022-01-01')
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df[df['trade_date'] >= start_date].copy()
    
    print(f"载入数据: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    
    # 我们抽取 500 只市值和成交极度活跃的股票进行全景扫描，模拟 RPS 强势股池
    top_stocks = df.groupby('ts_code')['amount'].mean().nlargest(500).index
    df_filtered = df[df['ts_code'].isin(top_stocks)].copy()
    
    print(f"选取 {len(top_stocks)} 只核心活跃票进行口袋支点和回踩扫描...")
    
    results = []
    total = len(top_stocks)
    signal_count_pp = 0
    signal_count_pb = 0
    
    for i, code in enumerate(top_stocks):
        if i % 100 == 0:
            print(f"  扫描进度: {i}/{total}...")
        stock_data = df_filtered[df_filtered['ts_code'] == code].sort_values('trade_date').reset_index(drop=True)
        if len(stock_data) < 60:
            continue
            
        try:
            # 1. 扫描口袋支点
            pp_res = identify_pocket_pivot_v2(stock_data, max_risk_pct=0.08)
            # 2. 扫描均线回踩
            pb_res = identify_trend_pullback(stock_data, max_risk_pct=0.08)
            
            # 合并两个信号 (如果同一天同时出现，以口袋支点优先)
            combined = stock_data.copy()
            combined['pocket_pivot_signal'] = pp_res['pocket_pivot_signal'] if 'pocket_pivot_signal' in pp_res else False
            combined['pullback_signal'] = pb_res['pullback_signal'] if 'pullback_signal' in pb_res else False
            
            # 处理 Stop loss 的合并
            combined['stop_loss_price'] = np.nan
            if 'stop_loss_price' in pp_res.columns:
                combined.loc[combined['pocket_pivot_signal'], 'stop_loss_price'] = pp_res.loc[combined['pocket_pivot_signal'], 'stop_loss_price']
            if 'stop_loss_price' in pb_res.columns:
                mask_pb = combined['pullback_signal'] & ~combined['pocket_pivot_signal']
                combined.loc[mask_pb, 'stop_loss_price'] = pb_res.loc[mask_pb, 'stop_loss_price']
                
            has_pp = combined['pocket_pivot_signal'].any()
            has_pb = combined['pullback_signal'].any()
            
            if has_pp or has_pb:
                signal_count_pp += combined['pocket_pivot_signal'].sum()
                signal_count_pb += combined['pullback_signal'].sum()
                results.append(combined)
                
        except Exception as e:
            pass
            
    if not results:
        print("未发现任何有效突破信号。")
        return
        
    df_with_signals = pd.concat(results, ignore_index=True)
    print(f"\n扫描完成! 共产生信号: 口袋支点({signal_count_pp}个), 均线回踩({signal_count_pb}个)")
    print(f"过滤掉 8% 初始风险敞口之外的劣质信号")
    
    print("\n执行带有仓位管理、ATR移动止盈、硬止损的资金曲线回测...")
    try:
    # 回测时使用单票最大15%仓位 (满仓最多6-7只股票)
        nav_df, trades_df = run_pocket_pivot_backtest(df_with_signals, initial_capital=1000000, max_pos_pct=0.15)
        
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
            
            # 计算每种策略胜率
            sells_pp = sells[sells['signal_type'] == 'POCKET_PIVOT']
            sells_pb = sells[sells['signal_type'] == 'PULLBACK']
            wr_pp = len(sells_pp[sells_pp['pnl']>0]) / len(sells_pp) * 100 if len(sells_pp) > 0 else 0
            wr_pb = len(sells_pb[sells_pb['pnl']>0]) / len(sells_pb) * 100 if len(sells_pb) > 0 else 0
            
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            pnl_ratio = 0
            wr_pp, wr_pb = 0, 0
            
        print("\n" + "=" * 60)
        print("  回测结果汇总 (A股高势能 Alpha 策略)")
        print("=" * 60)
        print(f"初始资金 : 1,000,000")
        print(f"最终净值 : {final_nav:,.2f}")
        print(f"总收益率 : {ret*100:.2f}%")
        print(f"最大回撤 : -{((1 - nav_df['nav'] / nav_df['nav'].cummax()).max() * 100):.2f}%")
        print(f"总交易数 : {len(sells)} 笔 (完成闭环)")
        print(f"整体胜率 : {win_rate:.1f}%")
        print(f"  └ 口袋支点胜率 : {wr_pp:.1f}% ({len(sells_pp)}笔)")
        print(f"  └ 均线回踩胜率 : {wr_pb:.1f}% ({len(sells_pb)}笔)")
        print(f"平均盈利 : +{avg_win:.2f}%")
        print(f"平均亏损 : {avg_loss:.2f}%")
        print(f"盈亏比   : {pnl_ratio:.2f}")
        
        report_dir = REPORTS_DIR / "pocket_pivot_backtest"
        report_dir.mkdir(parents=True, exist_ok=True)
        trades_df.to_csv(report_dir / "pocket_pivot_trades.csv", index=False, encoding='utf-8-sig')
        nav_df.to_csv(report_dir / "pocket_pivot_nav.csv", index=False, encoding='utf-8-sig')
        print(f"\n详细交易记录已保存至: {report_dir / 'pocket_pivot_trades.csv'}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"回测引擎运行失败: {e}")

if __name__ == '__main__':
    main()
