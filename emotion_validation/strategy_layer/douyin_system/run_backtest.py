#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音实战派交易系统 - 回测入口
"""

import argparse
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from pathlib import Path

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from emotion_validation.strategy_layer.douyin_system.market_breadth import MarketBreadth
from emotion_validation.strategy_layer.douyin_system.stock_screener import StockScreener
from emotion_validation.strategy_layer.douyin_system.trade_simulator import TradeSimulator
from emotion_validation.strategy_layer.douyin_system.risk_controller import RiskController
from emotion_validation.strategy_layer.douyin_system.backtest_report import BacktestReport


class DouyinBacktest:
    """
    抖音实战派交易系统回测类
    """
    
    def __init__(self, data_dir: str = None, output_dir: str = None):
        """
        初始化回测系统
        
        Args:
            data_dir: 数据目录
            output_dir: 输出目录
        """
        # 配置日志
        self.logger = self._configure_logging()
        
        # 设置路径 - 确保路径正确
        if data_dir is None or output_dir is None:
            # 获取项目根目录（相对于脚本位置）
            # 脚本位置: emotion_validation/strategy_layer/douyin_system/run_backtest.py
            # 根目录: 往上三级
            self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        else:
            self.root_dir = os.path.abspath(os.path.join(os.path.dirname(data_dir), '..'))
            
        self.data_dir = data_dir or os.path.abspath(os.path.join(self.root_dir, 'backtest', 'data'))
        self.output_dir = output_dir or os.path.abspath(os.path.join(self.root_dir, 'backtest', 'reports'))
        
        # 确保输出目录存在
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        # 初始化模块
        self.market_breadth = None
        self.stock_screener = None
        self.trade_simulator = None
        self.risk_controller = None
        self.report_generator = BacktestReport(self.output_dir)
        
        # 回测结果
        self.daily_results = []
        self.trade_details = []
        self.daily_assets = []
        
        # 数据加载状态
        self.data_loaded = False
        
        self.logger.info(f"回测系统初始化完成")
        self.logger.info(f"数据目录: {self.data_dir}")
        self.logger.info(f"输出目录: {self.output_dir}")
    
    def _configure_logging(self):
        """
        配置日志
        
        Returns:
            日志记录器
        """
        logger = logging.getLogger('DouyinBacktest')
        logger.setLevel(logging.INFO)
        
        # 控制台输出
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def load_data(self) -> bool:
        """
        加载回测数据
        
        Returns:
            是否加载成功
        """
        self.logger.info("开始加载回测数据...")
        
        try:
            # 加载股票日线数据
            stock_daily_path = os.path.join(self.data_dir, 'stock_daily.csv')
            if not os.path.exists(stock_daily_path):
                stock_daily_path = os.path.join(self.data_dir, 'stock_daily.parquet')
            
            if not os.path.exists(stock_daily_path):
                raise FileNotFoundError(f"股票日线数据文件不存在: {stock_daily_path}")
            
            # 初始化选股引擎
            self.stock_screener = StockScreener(stock_daily_path)
            self.logger.info("股票日线数据加载成功")
            
            # 加载生态缓存数据
            ecology_cache_path = os.path.join(self.data_dir, 'ecology_cache.csv')
            if os.path.exists(ecology_cache_path):
                self.market_breadth = MarketBreadth(stock_daily_path)
                self.logger.info("生态缓存数据加载成功")
            
            # 初始化交易模拟器
            self.trade_simulator = TradeSimulator(initial_capital=100000.0)
            
            # 初始化风险控制器
            self.risk_controller = RiskController()
            
            self.data_loaded = True
            self.logger.info("数据加载完成")
            
            return True
            
        except Exception as e:
            self.logger.error(f"数据加载失败: {e}")
            self.data_loaded = False
            return False
    
    def preprocess_data(self):
        """
        数据预处理
        """
        if not self.data_loaded:
            self.load_data()
        
        self.logger.info("开始数据预处理...")
        
        if self.stock_screener and self.stock_screener.stock_data is not None:
            # 确保必要字段存在
            required_columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            for col in required_columns:
                if col not in self.stock_screener.stock_data.columns:
                    self.logger.warning(f"数据缺少字段: {col}")
        
        self.logger.info("数据预处理完成")
    
    def run_backtest(self, 
                    start_date: str = None, 
                    end_date: str = None,
                    initial_capital: float = 100000.0,
                    strategies: list = None):
        """
        运行回测
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            initial_capital: 初始资金
            strategies: 使用的策略列表
        """
        self.logger.info("开始回测...")
        
        if not self.data_loaded:
            if not self.load_data():
                return False
        
        if strategies is None:
            strategies = ['A', 'B', 'C', 'D']
        
        # 初始化交易模拟器
        self.trade_simulator = TradeSimulator(initial_capital=initial_capital)
        
        # 初始化风险控制器
        self.risk_controller = RiskController()
        
        # 获取日期范围
        if self.stock_screener and self.stock_screener.stock_data is not None:
            all_dates = sorted(self.stock_screener.stock_data['trade_date'].unique())
            
            if start_date:
                start_date = pd.to_datetime(start_date)
                all_dates = [d for d in all_dates if d >= start_date]
            
            if end_date:
                end_date = pd.to_datetime(end_date)
                all_dates = [d for d in all_dates if d <= end_date]
        
        self.logger.info(f"回测日期范围: {all_dates[0].strftime('%Y-%m-%d')} 至 {all_dates[-1].strftime('%Y-%m-%d')}")
        self.logger.info(f"使用策略: {', '.join(strategies)}")
        
        # 初始化每日结果
        self.daily_results = []
        self.trade_details = []
        self.daily_assets = [initial_capital]
        self._pending_selections = []
        
        # 逐天回测: T日选股 -> T+1日买入 (消除未来函数)
        for date_idx, current_date in enumerate(all_dates):
            self.logger.debug(f"回测日期: {current_date.strftime('%Y-%m-%d')}")
            
            day_result = self._backtest_single_day(
                current_date, 
                date_idx, 
                all_dates,
                strategies
            )
            
            if day_result:
                self.daily_results.append(day_result)
                self.daily_assets.append(day_result['total_assets'])
        
        # 生成绩效报告
        self._generate_backtest_report(all_dates)
        
        return True
    
    def _backtest_single_day(self, current_date, date_idx, all_dates, strategies):
        """
        单日回测 (无未来函数版本)
        
        流程: 用昨日选股结果在今日开盘买入 -> 今日盘中检查卖出 -> 今日收盘选股留给明日
        """
        day_result = {
            'date': current_date,
            'total_assets': self.trade_simulator.get_total_assets({}),
            'portfolio_value': 0,
            'cash': self.trade_simulator.current_capital,
            'num_positions': len(self.trade_simulator.portfolio),
            'daily_return': 0,
            'cumulative_return': 0
        }
        
        try:
            # 1. 获取当日数据
            current_data = self.stock_screener.stock_data[
                self.stock_screener.stock_data['trade_date'] == current_date
            ]
            
            # 2. 准备当日价格和成交量数据
            daily_prices = {}
            daily_volumes = {}
            
            for _, row in current_data.iterrows():
                daily_prices[row['ts_code']] = {
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close']
                }
                daily_volumes[row['ts_code']] = row['vol']
            
            # 3. 计算平均成交量和近期最高价
            avg_volumes = {}
            latest_highs = {}
            
            if date_idx > 5:
                historical_data = self.stock_screener.stock_data[
                    (self.stock_screener.stock_data['trade_date'] <= current_date) &
                    (self.stock_screener.stock_data['trade_date'] > all_dates[max(0, date_idx - 10)])
                ]
                
                for ts_code, group in historical_data.groupby('ts_code'):
                    avg_volumes[ts_code] = group['vol'].mean()
                    latest_highs[ts_code] = group['high'].max()
            
            # 4. 计算市场广度 (用于风控)
            decline_count = 0
            if self.market_breadth:
                signal = self.market_breadth.get_daily_signal(
                    current_date.strftime('%Y-%m-%d')
                )
                decline_count = signal.get('down_count', 0) if signal else 0
            
            # 5. 用昨日选股结果执行今日买入 (T-1选股 -> T买入, 消除未来函数)
            stocks_to_buy = self._pending_selections
            
            day_trades = self.trade_simulator.simulate_day(
                current_date,
                stocks_to_buy,
                daily_prices,
                daily_volumes,
                avg_volumes,
                latest_highs,
                decline_count,
                self.risk_controller
            )
            
            # 6. 今日收盘后选股，存入 pending 留给明天买入
            prev_day_data = self.stock_screener.stock_data[
                self.stock_screener.stock_data['trade_date'] <= current_date
            ]
            selected_stocks = self.stock_screener.screen_stocks(
                date=current_date,
                strategies=strategies
            )
            # 只保留当日被选中的股票代码
            if not selected_stocks.empty:
                today_picks = selected_stocks[
                    selected_stocks['trade_date'] == current_date
                ]
                self._pending_selections = list(today_picks['ts_code'].unique()) if not today_picks.empty else []
            else:
                self._pending_selections = []
            
            # 7. 更新每日结果
            close_prices = {
                ts_code: data['close'] for ts_code, data in daily_prices.items()
            }
            day_result['total_assets'] = self.trade_simulator.get_total_assets(close_prices)
            day_result['portfolio_value'] = self.trade_simulator.get_portfolio_value(close_prices)
            day_result['cash'] = self.trade_simulator.current_capital
            day_result['num_positions'] = len(self.trade_simulator.portfolio)
            day_result['bought'] = day_trades.get('bought', [])
            day_result['sold'] = day_trades.get('sold', [])
            
            # 8. 更新风险控制器
            if day_result['total_assets'] > 0 and self.daily_assets:
                daily_return = (day_result['total_assets'] - self.daily_assets[-1]) / self.daily_assets[-1]
                day_result['daily_return'] = daily_return
                prev_cum = self.daily_results[-1]['cumulative_return'] if self.daily_results else 0
                day_result['cumulative_return'] = (1 + daily_return) * (1 + prev_cum) - 1
                
                self.risk_controller.update_continuous_loss_days(daily_return)
            
            return day_result
            
        except Exception as e:
            self.logger.error(f"当日回测失败 {current_date}: {e}")
            return None
    
    def _generate_backtest_report(self, dates):
        """
        生成回测报告
        
        Args:
            dates: 日期列表
        """
        self.logger.info("生成回测报告...")
        
        try:
            # 加载每日资产数据
            self.report_generator.load_daily_data(self.daily_assets, dates)
            
            # 加载交易数据
            if self.trade_simulator:
                trade_log = self.trade_simulator.get_trade_log()
                if not trade_log.empty:
                    self.report_generator.load_trade_data(trade_log)
            
            # 生成所有报告
            reports = self.report_generator.generate_all_reports()
            
            # 打印报告路径
            for name, path in reports.items():
                if path:
                    self.logger.info(f"报告已生成: {path}")
            
            # 打印摘要信息
            self.logger.info("回测完成!")
            self.report_generator.print_summary()
            
        except Exception as e:
            self.logger.error(f"报告生成失败: {e}")
    
    def get_performance_summary(self) -> dict:
        """
        获取绩效摘要
        
        Returns:
            绩效摘要
        """
        if self.report_generator:
            return self.report_generator.calculate_overall_statistics()
        
        return {}
    
    def get_strategy_performance(self) -> pd.DataFrame:
        """
        获取策略表现
        
        Returns:
            策略表现数据
        """
        if self.report_generator:
            return self.report_generator.calculate_strategy_performance()
        
        return pd.DataFrame()
    
    def save_backtest_result(self, filename: str = "backtest_result.csv") -> str:
        """
        保存回测结果
        
        Args:
            filename: 文件名
        
        Returns:
            文件路径
        """
        if not self.daily_results:
            return ""
        
        output_path = os.path.join(self.output_dir, filename)
        
        try:
            result_df = pd.DataFrame([{
                'date': day['date'].strftime('%Y-%m-%d'),
                'total_assets': day['total_assets'],
                'portfolio_value': day['portfolio_value'],
                'cash': day['cash'],
                'num_positions': day['num_positions'],
                'daily_return': day['daily_return'],
                'cumulative_return': day['cumulative_return']
            } for day in self.daily_results])
            
            result_df.to_csv(output_path, index=False, encoding='utf-8-sig')
            return output_path
            
        except Exception as e:
            self.logger.error(f"保存结果失败: {e}")
            return ""


def main():
    """
    主函数
    """
    parser = argparse.ArgumentParser(
        description='抖音实战派交易系统 - 回测入口',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python run_backtest.py --start-date 2024-01-01 --end-date 2024-12-31
    python run_backtest.py --data-dir ./data --output-dir ./reports
    python run_backtest.py --strategies A,B,C,D --initial-capital 500000
'''
    )
    
    parser.add_argument('--data-dir', 
                       type=str, 
                       help='数据目录 (默认: backtest/data)',
                       default='backtest/data')
    
    parser.add_argument('--output-dir', 
                       type=str, 
                       help='输出目录 (默认: backtest/reports)',
                       default='backtest/reports')
    
    parser.add_argument('--start-date', 
                       type=str, 
                       help='开始日期 (格式: YYYY-MM-DD)')
    
    parser.add_argument('--end-date', 
                       type=str, 
                       help='结束日期 (格式: YYYY-MM-DD)')
    
    parser.add_argument('--initial-capital', 
                       type=float, 
                       help='初始资金 (默认: 100000.0)',
                       default=100000.0)
    
    parser.add_argument('--strategies', 
                       type=str, 
                       help='使用策略 (A,B,C,D)',
                       default='A,B,C,D')
    
    parser.add_argument('--log-level', 
                       type=str, 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       default='INFO',
                       help='日志级别 (默认: INFO)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🎯 抖音实战派交易系统 - 回测入口")
    print("=" * 60)
    
    # 创建回测实例
    backtest = DouyinBacktest(
        data_dir=args.data_dir,
        output_dir=args.output_dir
    )
    
    # 配置日志
    if args.log_level:
        log_level = getattr(logging, args.log_level)
        backtest.logger.setLevel(log_level)
    
    # 加载数据
    if not backtest.load_data():
        print("❌ 数据加载失败")
        return 1
    
    # 数据预处理
    backtest.preprocess_data()
    
    # 解析策略
    strategies = [s.strip() for s in args.strategies.split(',')]
    
    # 运行回测
    print(f"📈 开始回测...")
    backtest.run_backtest(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        strategies=strategies
    )
    
    print(f"📊 回测完成!")
    print(f"📁 报告目录: {backtest.output_dir}")
    
    return 0


if __name__ == "__main__":
    main()