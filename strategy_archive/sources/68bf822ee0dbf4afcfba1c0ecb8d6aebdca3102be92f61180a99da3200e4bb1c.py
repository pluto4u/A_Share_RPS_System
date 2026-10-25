import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class StockScreener:
    """
    选股引擎 - 实现4种选股策略
    - 策略A: 放量长上影
    - 策略B: 一进二
    - 策略C: 横盘突破涨停
    - 策略D: 强势股回调确认
    """
    
    def __init__(self, data_path: str = None):
        """
        初始化选股引擎
        
        Args:
            data_path: 股票日线数据路径
        """
        self.data_path = data_path
        self.stock_data = None
        
        if data_path:
            self.load_data()
    
    def load_data(self) -> pd.DataFrame:
        try:
            if self.data_path.endswith('.parquet'):
                self.stock_data = pd.read_parquet(self.data_path)
            else:
                self.stock_data = pd.read_csv(self.data_path)
            
            required_columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
            for col in required_columns:
                if col not in self.stock_data.columns:
                    raise ValueError(f"数据缺少必要字段: {col}")
            
            self.stock_data['trade_date'] = pd.to_datetime(self.stock_data['trade_date'])
            self.stock_data.sort_values(['ts_code', 'trade_date'], inplace=True)
            self.stock_data['prev_close'] = self.stock_data.groupby('ts_code')['close'].shift(1)
            
            print(f"成功加载股票数据: {len(self.stock_data)} 条记录")
            return self.stock_data
            
        except Exception as e:
            print(f"加载股票数据失败: {e}")
            return None
    
    @staticmethod
    def _get_limit_ratio(ts_code: str) -> float:
        """根据股票代码返回涨跌幅限制比例"""
        code = ts_code.split('.')[0] if '.' in ts_code else ts_code
        if code.startswith('3') or code.startswith('68'):
            return 0.20  # 创业板 / 科创板
        if 'ST' in ts_code.upper():
            return 0.05  # ST
        return 0.10  # 主板
    
    def _detect_limit_up(self, df: pd.DataFrame) -> pd.Series:
        """基于 prev_close 检测涨停，区分板块涨跌幅"""
        if 'prev_close' not in df.columns:
            df = df.copy()
            df['prev_close'] = df.groupby('ts_code')['close'].shift(1)
        
        limit_ratios = df['ts_code'].map(self._get_limit_ratio)
        limit_price = df['prev_close'] * (1 + limit_ratios - 0.005)
        return (df['close'] >= limit_price) & (df['close'] == df['high']) & df['prev_close'].notna()
    
    def strategy_a(self, data: pd.DataFrame = None) -> pd.DataFrame:
        """
        策略A: 放量突破前高
        
        选股条件:
        - 收盘价突破近10日最高价 (创新高)
        - 收盘价 > 开盘价 (阳线实体)
        - 成交量 >= 5日均量的 1.5 倍 (放量确认)
        - 涨幅 > 3% (有力度的突破, 非磨蹭式)
        """
        if data is None:
            data = self.stock_data
        if data is None:
            return pd.DataFrame()
        
        df = data.copy()
        
        # 前10日最高价 (不含当天)
        df['prev_high_10'] = df.groupby('ts_code')['high'].shift(1).rolling(10).max()
        df['prev_high_10'] = df.groupby('ts_code')['prev_high_10'].transform(lambda x: x)
        # 重新算: 每只股票独立
        grouped = df.groupby('ts_code')
        df['prev_high_10'] = grouped['high'].transform(lambda x: x.shift(1).rolling(10, min_periods=5).max())
        
        df['vol_avg5'] = grouped['vol'].transform(lambda x: x.rolling(5, min_periods=3).mean())
        
        if 'prev_close' not in df.columns:
            df['prev_close'] = grouped['close'].transform(lambda x: x.shift(1))
        
        df['pct'] = (df['close'] - df['prev_close']) / df['prev_close']
        
        condition = (
            (df['close'] > df['prev_high_10']) &
            (df['close'] > df['open']) &
            (df['vol'] >= df['vol_avg5'] * 1.5) &
            (df['pct'] >= 0.03) &
            df['prev_high_10'].notna()
        )
        
        result = df[condition][['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']]
        print(f"策略A选出股票数量: {len(result)}")
        return result
    
    def strategy_b(self, data: pd.DataFrame = None) -> pd.DataFrame:
        """
        策略B: 一进二 -- 连续两日涨停
        """
        if data is None:
            data = self.stock_data
        if data is None:
            return pd.DataFrame()
        
        df = data.copy()
        df['limit_up'] = self._detect_limit_up(df)
        df['prev_limit_up'] = df.groupby('ts_code')['limit_up'].shift(1)
        
        condition = df['prev_limit_up'].fillna(False) & df['limit_up']
        
        result = df[condition][['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']]
        print(f"策略B选出股票数量: {len(result)}")
        return result
    
    def strategy_c(self, data: pd.DataFrame = None) -> pd.DataFrame:
        """
        策略C: 横盘突破涨停 -- 低波动收敛后放量涨停突破
        """
        if data is None:
            data = self.stock_data
        if data is None:
            return pd.DataFrame()
        
        df = data.copy()
        
        df['high_10'] = df.groupby('ts_code')['high'].rolling(10).max().reset_index(level=0, drop=True)
        df['low_10'] = df.groupby('ts_code')['low'].rolling(10).min().reset_index(level=0, drop=True)
        df['volatility'] = (df['high_10'] - df['low_10']) / df['low_10']
        df['limit_up'] = self._detect_limit_up(df)
        df['vol_avg5'] = df.groupby('ts_code')['vol'].rolling(5).mean().reset_index(level=0, drop=True)
        
        condition = (
            (df['volatility'] <= 0.12) &
            df['limit_up'] &
            (df['vol'] >= df['vol_avg5'] * 1.5)
        )
        
        result = df[condition][['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']]
        print(f"策略C选出股票数量: {len(result)}")
        return result
    
    def strategy_d(self, data: pd.DataFrame = None) -> pd.DataFrame:
        """
        策略D: 强势股回调确认 -- 多板股缩量回踩后企稳
        """
        if data is None:
            data = self.stock_data
        if data is None:
            return pd.DataFrame()
        
        df = data.copy()
        
        df['limit_up'] = self._detect_limit_up(df)
        df['limit_count_10'] = df.groupby('ts_code')['limit_up'].rolling(10).sum().reset_index(level=0, drop=True)
        df['high_10'] = df.groupby('ts_code')['high'].rolling(10).max().reset_index(level=0, drop=True)
        df['pullback'] = (df['high_10'] - df['close']) / df['high_10']
        df['vol_avg5'] = df.groupby('ts_code')['vol'].rolling(5).mean().reset_index(level=0, drop=True)
        
        condition = (
            (df['limit_count_10'] >= 2) &
            (df['pullback'] <= 0.10) &
            (df['close'] > df['open']) &
            (df['vol'] <= df['vol_avg5'] * 0.8)
        )
        
        result = df[condition][['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']]
        print(f"策略D选出股票数量: {len(result)}")
        return result
    
    def screen_stocks(self, date: str or datetime = None, strategies: list = None) -> pd.DataFrame:
        """
        综合选股: 在指定日期应用所有策略
        
        Args:
            date: 指定日期 (可选, 默认选择最新日期)
            strategies: 要应用的策略列表 (可选, 默认应用所有策略)
        
        Returns:
            符合条件的股票列表 (去重)
        """
        if self.stock_data is None:
            return pd.DataFrame()
        
        if strategies is None:
            strategies = ['A', 'B', 'C', 'D']
        
        # 确定选股日期
        if date is None:
            trade_date = self.stock_data['trade_date'].max()
        else:
            if isinstance(date, str):
                trade_date = pd.to_datetime(date)
            else:
                trade_date = date
        
        # 获取指定日期及之前的数据
        date_data = self.stock_data[self.stock_data['trade_date'] <= trade_date]
        
        selected_stocks = []
        
        # 应用各个策略
        if 'A' in strategies:
            stocks_a = self.strategy_a(date_data)
            if not stocks_a.empty:
                stocks_a['strategy'] = 'A'
                selected_stocks.append(stocks_a)
        
        if 'B' in strategies:
            stocks_b = self.strategy_b(date_data)
            if not stocks_b.empty:
                stocks_b['strategy'] = 'B'
                selected_stocks.append(stocks_b)
        
        if 'C' in strategies:
            stocks_c = self.strategy_c(date_data)
            if not stocks_c.empty:
                stocks_c['strategy'] = 'C'
                selected_stocks.append(stocks_c)
        
        if 'D' in strategies:
            stocks_d = self.strategy_d(date_data)
            if not stocks_d.empty:
                stocks_d['strategy'] = 'D'
                selected_stocks.append(stocks_d)
        
        # 合并结果
        if selected_stocks:
            result = pd.concat(selected_stocks)
            # 去重
            result = result.drop_duplicates(subset=['ts_code', 'trade_date'])
            print(f"综合选股: {len(result)} 只股票符合条件")
            return result
        
        return pd.DataFrame()
    
    def get_strategy_performance(self, strategy: str = 'A') -> pd.DataFrame:
        """
        获取策略选股的历史表现
        
        Args:
            strategy: 策略类型 ('A', 'B', 'C', 'D')
        
        Returns:
            策略选股的历史表现数据
        """
        if self.stock_data is None:
            return pd.DataFrame()
        
        # 获取所有选股日期
        all_dates = sorted(self.stock_data['trade_date'].unique())
        
        performance = []
        
        for date in all_dates:
            # 获取该日期的选股结果
            date_data = self.stock_data[self.stock_data['trade_date'] <= date]
            
            if strategy == 'A':
                selected = self.strategy_a(date_data)
            elif strategy == 'B':
                selected = self.strategy_b(date_data)
            elif strategy == 'C':
                selected = self.strategy_c(date_data)
            elif strategy == 'D':
                selected = self.strategy_d(date_data)
            else:
                return pd.DataFrame()
            
            if not selected.empty:
                for _, stock in selected.iterrows():
                    # 获取选股后的收益
                    future_data = self.stock_data[
                        (self.stock_data['ts_code'] == stock['ts_code']) &
                        (self.stock_data['trade_date'] > date)
                    ].head(5)  # 看未来5个交易日
                
                    if len(future_data) >= 2:
                        entry_price = stock['close']
                        exit_price = future_data.iloc[-1]['close']
                        return_rate = (exit_price - entry_price) / entry_price
                        
                        performance.append({
                            'ts_code': stock['ts_code'],
                            'trade_date': date,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'return_rate': return_rate,
                            'hold_days': len(future_data)
                        })
        
        return pd.DataFrame(performance)
    
    def calculate_statistics(self, strategy: str = 'A') -> dict:
        """
        计算策略的统计指标
        
        Args:
            strategy: 策略类型 ('A', 'B', 'C', 'D')
        
        Returns:
            策略统计指标
        """
        performance = self.get_strategy_performance(strategy)
        
        if performance.empty:
            return {
                'total_picks': 0,
                'avg_return': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'max_profit': 0,
                'max_loss': 0
            }
        
        stats = {
            'total_picks': len(performance),
            'avg_return': performance['return_rate'].mean(),
            'win_rate': len(performance[performance['return_rate'] > 0]) / len(performance),
            'profit_factor': performance[performance['return_rate'] > 0]['return_rate'].sum() / 
                            abs(performance[performance['return_rate'] < 0]['return_rate'].sum()) if 
                            len(performance[performance['return_rate'] < 0]) > 0 else 0,
            'max_profit': performance['return_rate'].max(),
            'max_loss': performance['return_rate'].min()
        }
        
        return stats
    
    def run_strategy_comparison(self) -> pd.DataFrame:
        """
        运行所有策略的对比分析
        
        Returns:
            策略对比结果
        """
        results = []
        
        for strategy in ['A', 'B', 'C', 'D']:
            stats = self.calculate_statistics(strategy)
            results.append({
                'strategy': strategy,
                'total_picks': stats['total_picks'],
                'avg_return': stats['avg_return'],
                'win_rate': stats['win_rate'],
                'profit_factor': stats['profit_factor'],
                'max_profit': stats['max_profit'],
                'max_loss': stats['max_loss']
            })
        
        return pd.DataFrame(results)
    
    def save_selected_stocks(self, selected_stocks: pd.DataFrame, output_path: str) -> bool:
        """
        保存选股结果
        
        Args:
            selected_stocks: 选股结果
            output_path: 输出路径
        
        Returns:
            是否成功保存
        """
        try:
            if selected_stocks.empty:
                print("没有选股结果需要保存")
                return True
            
            if output_path.endswith('.parquet'):
                selected_stocks.to_parquet(output_path, index=False)
            else:
                selected_stocks.to_csv(output_path, index=False, encoding='utf-8-sig')
            
            print(f"选股结果已保存到: {output_path}")
            return True
            
        except Exception as e:
            print(f"保存选股结果失败: {e}")
            return False


# 使用示例
if __name__ == "__main__":
    # 创建选股引擎实例
    screener = StockScreener()
    
    # 测试数据加载
    test_data = pd.DataFrame({
        'ts_code': ['000001.SZ', '000001.SZ', '000001.SZ', '000002.SZ', '000002.SZ', '000002.SZ'],
        'trade_date': pd.to_datetime(['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-01', '2024-01-02', '2024-01-03']),
        'open': [10.0, 10.5, 11.0, 20.0, 21.0, 22.0],
        'high': [11.0, 12.0, 13.0, 22.0, 24.0, 25.0],
        'low': [9.8, 10.2, 10.8, 19.8, 20.8, 21.8],
        'close': [10.8, 11.5, 12.5, 21.5, 23.0, 24.5],
        'vol': [1000000, 2000000, 3000000, 500000, 1000000, 1500000],
        'amount': [10800000, 23000000, 37500000, 10750000, 23000000, 36750000]
    })
    
    print("=== 策略A选股测试 ===")
    result_a = screener.strategy_a(test_data)
    print(result_a)
    
    print("\n=== 策略B选股测试 ===")
    result_b = screener.strategy_b(test_data)
    print(result_b)
    
    print("\n=== 策略C选股测试 ===")
    result_c = screener.strategy_c(test_data)
    print(result_c)
    
    print("\n=== 策略D选股测试 ===")
    result_d = screener.strategy_d(test_data)
    print(result_d)