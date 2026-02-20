import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import os


class BacktestReport:
    """
    回测报告生成器
    - 逐笔交易明细、整体统计、按策略分类统计
    - 与情绪策略和指数的对比
    """
    
    def __init__(self, output_dir: str = "backtest/reports"):
        """
        初始化报告生成器
        
        Args:
            output_dir: 报告输出目录
        """
        self.output_dir = output_dir
        self.trade_data = None
        self.daily_data = None
        self.strategy_performance = {}
        
        # 创建输出目录
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
    
    def load_trade_data(self, trade_log: pd.DataFrame, trade_details: list = None):
        """
        加载交易数据
        
        Args:
            trade_log: 交易日志
            trade_details: 交易详情
        """
        if isinstance(trade_log, list):
            self.trade_data = pd.DataFrame(trade_log)
        else:
            self.trade_data = trade_log
    
    def load_daily_data(self, daily_assets: list, dates: list = None):
        """
        加载每日资产数据
        
        Args:
            daily_assets: 每日资产列表
            dates: 日期列表
        """
        if dates is None:
            dates = pd.date_range(start='2024-01-01', periods=len(daily_assets))
        
        self.daily_data = pd.DataFrame({
            'date': dates,
            'total_assets': daily_assets
        })
        
        # 计算每日收益率
        self.daily_data['daily_return'] = self.daily_data['total_assets'].pct_change()
        self.daily_data['cumulative_return'] = (1 + self.daily_data['daily_return']).cumprod() - 1
    
    def calculate_overall_statistics(self) -> dict:
        """
        计算整体统计指标
        
        Returns:
            整体统计指标
        """
        if self.daily_data is None:
            return {}
        
        initial_capital = self.daily_data['total_assets'].iloc[0]
        final_assets = self.daily_data['total_assets'].iloc[-1]
        total_return = (final_assets - initial_capital) / initial_capital
        
        # 计算年化收益率
        trading_days = len(self.daily_data)
        annualized_return = (1 + total_return) ** (252 / trading_days) - 1
        
        # 计算最大回撤
        peak = self.daily_data['total_assets'].iloc[0]
        max_drawdown = 0.0
        
        for asset in self.daily_data['total_assets']:
            if asset > peak:
                peak = asset
            drawdown = (peak - asset) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 计算夏普比率
        daily_returns = self.daily_data['daily_return'].dropna()
        sharpe_ratio = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() != 0 else 0
        
        # 计算胜率
        winning_days = len(self.daily_data[self.daily_data['daily_return'] > 0])
        losing_days = len(self.daily_data[self.daily_data['daily_return'] < 0])
        win_rate = winning_days / (winning_days + losing_days) if (winning_days + losing_days) > 0 else 0
        
        return {
            'initial_capital': initial_capital,
            'final_assets': final_assets,
            'total_return': total_return,
            'annualized_return': annualized_return,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'win_rate': win_rate,
            'trading_days': trading_days,
            'avg_daily_return': daily_returns.mean(),
            'volatility': daily_returns.std()
        }
    
    def calculate_strategy_performance(self, strategy_labels: dict = None) -> pd.DataFrame:
        """
        计算各策略表现
        
        Args:
            strategy_labels: 策略标签
        
        Returns:
            策略表现统计
        """
        if self.trade_data is None:
            return pd.DataFrame()
        
        if 'strategy' in self.trade_data.columns:
            strategy_groups = self.trade_data.groupby('strategy')
        else:
            return pd.DataFrame()
        
        strategy_stats = []
        
        for strategy, group in strategy_groups:
            strategy_name = strategy_labels.get(strategy, strategy) if strategy_labels else strategy
            
            # 计算策略统计
            num_trades = len(group)
            win_count = len(group[group['profit'] > 0])
            lose_count = len(group[group['profit'] < 0])
            total_profit = group['profit'].sum()
            avg_profit = group['profit'].mean() if num_trades > 0 else 0
            win_rate = win_count / num_trades if num_trades > 0 else 0
            
            strategy_stats.append({
                'strategy': strategy_name,
                'num_trades': num_trades,
                'win_count': win_count,
                'lose_count': lose_count,
                'total_profit': total_profit,
                'avg_profit': avg_profit,
                'win_rate': win_rate
            })
        
        return pd.DataFrame(strategy_stats)
    
    def generate_trade_details_report(self, filename: str = "trade_details.csv") -> str:
        """
        生成交易详情报告
        
        Args:
            filename: 输出文件名
        
        Returns:
            文件路径
        """
        if self.trade_data is None:
            return ""
        
        output_path = os.path.join(self.output_dir, filename)
        self.trade_data.to_csv(output_path, index=False, encoding='utf-8-sig')
        
        return output_path
    
    def generate_performance_summary(self, filename: str = "performance_summary.csv") -> str:
        """
        生成绩效汇总报告
        
        Args:
            filename: 输出文件名
        
        Returns:
            文件路径
        """
        stats = self.calculate_overall_statistics()
        
        summary = pd.DataFrame([{
            '指标名称': '初始资金',
            '数值': f"{stats['initial_capital']:.2f}"
        }, {
            '指标名称': '最终资产',
            '数值': f"{stats['final_assets']:.2f}"
        }, {
            '指标名称': '总收益率',
            '数值': f"{stats['total_return']:.2%}"
        }, {
            '指标名称': '年化收益率',
            '数值': f"{stats['annualized_return']:.2%}"
        }, {
            '指标名称': '最大回撤',
            '数值': f"{stats['max_drawdown']:.2%}"
        }, {
            '指标名称': '夏普比率',
            '数值': f"{stats['sharpe_ratio']:.2f}"
        }, {
            '指标名称': '胜率',
            '数值': f"{stats['win_rate']:.2%}"
        }, {
            '指标名称': '交易天数',
            '数值': f"{stats['trading_days']}"
        }, {
            '指标名称': '平均日收益率',
            '数值': f"{stats['avg_daily_return']:.2%}"
        }, {
            '指标名称': '波动率',
            '数值': f"{stats['volatility']:.2%}"
        }])
        
        output_path = os.path.join(self.output_dir, filename)
        summary.to_csv(output_path, index=False, encoding='utf-8-sig')
        
        return output_path
    
    def generate_strategy_comparison(self, filename: str = "strategy_comparison.csv") -> str:
        """
        生成策略对比报告
        
        Args:
            filename: 输出文件名
        
        Returns:
            文件路径
        """
        strategy_stats = self.calculate_strategy_performance()
        
        if strategy_stats.empty:
            return ""
        
        output_path = os.path.join(self.output_dir, filename)
        strategy_stats.to_csv(output_path, index=False, encoding='utf-8-sig')
        
        return output_path
    
    def generate_plot_equity_curve(self, filename: str = "equity_curve.png", show: bool = False):
        """
        生成资金曲线图表
        
        Args:
            filename: 输出文件名
            show: 是否显示图表
        """
        if self.daily_data is None:
            return
        
        plt.figure(figsize=(12, 6))
        plt.plot(self.daily_data['date'], self.daily_data['total_assets'])
        plt.title('资金曲线')
        plt.xlabel('日期')
        plt.ylabel('总资产 (元)')
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        output_path = os.path.join(self.output_dir, filename)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        
        if show:
            plt.show()
        
        plt.close()
    
    def generate_plot_drawdown(self, filename: str = "drawdown.png", show: bool = False):
        """
        生成最大回撤图表
        
        Args:
            filename: 输出文件名
            show: 是否显示图表
        """
        if self.daily_data is None:
            return
        
        # 计算每日回撤
        self.daily_data['peak'] = self.daily_data['total_assets'].expanding().max()
        self.daily_data['drawdown'] = (self.daily_data['total_assets'] - self.daily_data['peak']) / self.daily_data['peak']
        
        plt.figure(figsize=(12, 4))
        plt.fill_between(self.daily_data['date'], self.daily_data['drawdown'], alpha=0.3, color='red')
        plt.plot(self.daily_data['date'], self.daily_data['drawdown'], color='red', linewidth=1)
        plt.title('最大回撤')
        plt.xlabel('日期')
        plt.ylabel('回撤率')
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        output_path = os.path.join(self.output_dir, filename)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        
        if show:
            plt.show()
        
        plt.close()
    
    def generate_plot_return_distribution(self, filename: str = "return_distribution.png", show: bool = False):
        """
        生成收益率分布图表
        
        Args:
            filename: 输出文件名
            show: 是否显示图表
        """
        if self.daily_data is None:
            return
        
        plt.figure(figsize=(10, 6))
        sns.histplot(self.daily_data['daily_return'].dropna(), bins=30, kde=True)
        plt.title('每日收益率分布')
        plt.xlabel('每日收益率')
        plt.ylabel('频率')
        plt.grid(True)
        plt.tight_layout()
        
        output_path = os.path.join(self.output_dir, filename)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        
        if show:
            plt.show()
        
        plt.close()
    
    def generate_plot_monthly_returns(self, filename: str = "monthly_returns.png", show: bool = False):
        """
        生成月度收益率图表
        
        Args:
            filename: 输出文件名
            show: 是否显示图表
        """
        if self.daily_data is None:
            return
        
        monthly_returns = self.daily_data.set_index('date').resample('M')['daily_return'].apply(lambda x: (1 + x).prod() - 1)
        
        plt.figure(figsize=(12, 6))
        colors = ['red' if r < 0 else 'green' for r in monthly_returns]
        plt.bar(monthly_returns.index.strftime('%Y-%m'), monthly_returns, color=colors)
        plt.title('月度收益率')
        plt.xlabel('月份')
        plt.ylabel('收益率')
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        output_path = os.path.join(self.output_dir, filename)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        
        if show:
            plt.show()
        
        plt.close()
    
    def generate_plot_strategy_performance(self, filename: str = "strategy_performance.png", show: bool = False):
        """
        生成策略表现图表
        
        Args:
            filename: 输出文件名
            show: 是否显示图表
        """
        strategy_stats = self.calculate_strategy_performance()
        
        if strategy_stats.empty:
            return
        
        plt.figure(figsize=(10, 6))
        sns.barplot(x='strategy', y='win_rate', data=strategy_stats)
        plt.title('各策略胜率')
        plt.xlabel('策略')
        plt.ylabel('胜率')
        plt.grid(True)
        plt.tight_layout()
        
        output_path = os.path.join(self.output_dir, filename)
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        
        if show:
            plt.show()
        
        plt.close()
    
    def generate_html_report(self, filename: str = "backtest_report.html") -> str:
        """
        生成HTML报告
        
        Args:
            filename: 输出文件名
        
        Returns:
            文件路径
        """
        stats = self.calculate_overall_statistics()
        strategy_stats = self.calculate_strategy_performance()
        
        html_content = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>回测报告</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    margin: 20px;
                    background-color: #f5f5f5;
                }
                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                    background-color: white;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }
                h1 {
                    text-align: center;
                    color: #2c3e50;
                    margin-bottom: 30px;
                }
                .section {
                    margin-bottom: 40px;
                    padding: 20px;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    background-color: #f9f9f9;
                }
                h2 {
                    color: #34495e;
                    margin-top: 0;
                    margin-bottom: 20px;
                    border-bottom: 2px solid #3498db;
                    padding-bottom: 10px;
                }
                .stats-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin: 15px 0;
                }
                .stats-table th, .stats-table td {
                    border: 1px solid #ddd;
                    padding: 12px;
                    text-align: left;
                }
                .stats-table th {
                    background-color: #3498db;
                    color: white;
                    font-weight: bold;
                }
                .stats-table tr:nth-child(even) {
                    background-color: #f2f2f2;
                }
                .stats-table tr:hover {
                    background-color: #e0e0e0;
                }
                .image-container {
                    text-align: center;
                    margin: 20px 0;
                }
                .image-container img {
                    max-width: 100%;
                    height: auto;
                    margin: 10px 0;
                }
                .highlight {
                    background-color: #fff3cd;
                    padding: 10px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                .footer {
                    text-align: center;
                    margin-top: 40px;
                    color: #666;
                    font-size: 0.9em;
                    border-top: 1px solid #ddd;
                    padding-top: 20px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎯 回测报告</h1>
                
                <div class="section">
                    <h2>📊 整体绩效</h2>
                    <table class="stats-table">
                        <tr><th>指标</th><th>数值</th></tr>
                        <tr><td>初始资金</td><td>{initial_capital:.2f} 元</td></tr>
                        <tr><td>最终资产</td><td>{final_assets:.2f} 元</td></tr>
                        <tr><td>总收益率</td><td><span class="highlight">{total_return:.2%}</span></td></tr>
                        <tr><td>年化收益率</td><td><span class="highlight">{annualized_return:.2%}</span></td></tr>
                        <tr><td>最大回撤</td><td><span class="highlight">{max_drawdown:.2%}</span></td></tr>
                        <tr><td>夏普比率</td><td><span class="highlight">{sharpe_ratio:.2f}</span></td></tr>
                        <tr><td>胜率</td><td><span class="highlight">{win_rate:.2%}</span></td></tr>
                        <tr><td>交易天数</td><td>{trading_days} 天</td></tr>
                        <tr><td>平均日收益率</td><td>{avg_daily_return:.2%}</td></tr>
                        <tr><td>波动率</td><td>{volatility:.2%}</td></tr>
                    </table>
                </div>
                
                <div class="section">
                    <h2>📈 资金曲线</h2>
                    <div class="image-container">
                        <img src="equity_curve.png" alt="资金曲线">
                    </div>
                </div>
                
                <div class="section">
                    <h2>📉 最大回撤</h2>
                    <div class="image-container">
                        <img src="drawdown.png" alt="最大回撤">
                    </div>
                </div>
                
                <div class="section">
                    <h2>💰 策略表现</h2>
                    <div class="image-container">
                        <img src="strategy_performance.png" alt="策略表现">
                    </div>
                    {strategy_table}
                </div>
                
                <div class="section">
                    <h2>📊 收益率分布</h2>
                    <div class="image-container">
                        <img src="return_distribution.png" alt="收益率分布">
                    </div>
                </div>
                
                <div class="section">
                    <h2>📅 月度收益率</h2>
                    <div class="image-container">
                        <img src="monthly_returns.png" alt="月度收益率">
                    </div>
                </div>
                
                <div class="section">
                    <h2>📋 报告说明</h2>
                    <p><strong>报告生成时间:</strong> {report_time}</p>
                    <p><strong>回测日期范围:</strong> {start_date} 至 {end_date}</p>
                    <p><strong>策略:</strong> 抖音实战派交易系统</p>
                </div>
                
                <div class="footer">
                    <p>报告由自动化回测系统生成</p>
                </div>
            </div>
        </body>
        </html>
        """.format(
            initial_capital=stats.get('initial_capital', 0),
            final_assets=stats.get('final_assets', 0),
            total_return=stats.get('total_return', 0),
            annualized_return=stats.get('annualized_return', 0),
            max_drawdown=stats.get('max_drawdown', 0),
            sharpe_ratio=stats.get('sharpe_ratio', 0),
            win_rate=stats.get('win_rate', 0),
            trading_days=stats.get('trading_days', 0),
            avg_daily_return=stats.get('avg_daily_return', 0),
            volatility=stats.get('volatility', 0),
            report_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            start_date=self.daily_data['date'].iloc[0].strftime('%Y-%m-%d') if not self.daily_data.empty else '',
            end_date=self.daily_data['date'].iloc[-1].strftime('%Y-%m-%d') if not self.daily_data.empty else '',
            strategy_table=self._generate_strategy_table(strategy_stats)
        )
        
        output_path = os.path.join(self.output_dir, filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return output_path
    
    def _generate_strategy_table(self, strategy_stats: pd.DataFrame) -> str:
        """
        生成策略表格
        
        Args:
            strategy_stats: 策略统计数据
        
        Returns:
            HTML表格字符串
        """
        if strategy_stats.empty:
            return "<p>暂无策略数据</p>"
        
        table_html = """
        <table class="stats-table">
            <tr><th>策略</th><th>交易次数</th><th>盈利次数</th><th>亏损次数</th><th>总盈利</th><th>平均盈利</th><th>胜率</th></tr>
        """
        
        for _, row in strategy_stats.iterrows():
            table_html += f"""
            <tr>
                <td>{row['strategy']}</td>
                <td>{row['num_trades']}</td>
                <td>{row['win_count']}</td>
                <td>{row['lose_count']}</td>
                <td>{row['total_profit']:.2f}</td>
                <td>{row['avg_profit']:.2f}</td>
                <td>{row['win_rate']:.2%}</td>
            </tr>
            """
        
        table_html += "</table>"
        
        return table_html
    
    def generate_all_reports(self, include_plots: bool = True, show_plots: bool = False) -> dict:
        """
        生成所有报告
        
        Args:
            include_plots: 是否包含图表
            show_plots: 是否显示图表
        
        Returns:
            报告文件路径
        """
        reports = {
            'trade_details': self.generate_trade_details_report(),
            'performance_summary': self.generate_performance_summary(),
            'strategy_comparison': self.generate_strategy_comparison()
        }
        
        if include_plots:
            self.generate_plot_equity_curve(show=show_plots)
            self.generate_plot_drawdown(show=show_plots)
            self.generate_plot_return_distribution(show=show_plots)
            self.generate_plot_monthly_returns(show=show_plots)
            self.generate_plot_strategy_performance(show=show_plots)
            
            reports['equity_curve'] = os.path.join(self.output_dir, 'equity_curve.png')
            reports['drawdown'] = os.path.join(self.output_dir, 'drawdown.png')
            reports['return_distribution'] = os.path.join(self.output_dir, 'return_distribution.png')
            reports['monthly_returns'] = os.path.join(self.output_dir, 'monthly_returns.png')
            reports['strategy_performance'] = os.path.join(self.output_dir, 'strategy_performance.png')
        
        # 生成HTML报告
        reports['html_report'] = self.generate_html_report()
        
        return reports
    
    def print_summary(self):
        """
        打印摘要信息
        """
        if self.daily_data is None:
            print("无数据可显示")
            return
        
        stats = self.calculate_overall_statistics()
        
        print("=" * 60)
        print("🎯 回测报告摘要")
        print("=" * 60)
        print(f"📅 回测日期: {self.daily_data['date'].iloc[0].strftime('%Y-%m-%d')} 至 {self.daily_data['date'].iloc[-1].strftime('%Y-%m-%d')}")
        print(f"💰 初始资金: {stats['initial_capital']:.2f} 元")
        print(f"📊 最终资产: {stats['final_assets']:.2f} 元")
        print(f"📈 总收益率: {stats['total_return']:.2%}")
        print(f"⏰ 年化收益率: {stats['annualized_return']:.2%}")
        print(f"📉 最大回撤: {stats['max_drawdown']:.2%}")
        print(f"🎯 夏普比率: {stats['sharpe_ratio']:.2f}")
        print(f"🏆 胜率: {stats['win_rate']:.2%}")
        print(f"📊 交易天数: {stats['trading_days']} 天")
        print(f"📅 平均日收益率: {stats['avg_daily_return']:.2%}")
        print(f"📉 波动率: {stats['volatility']:.2%}")
        print("=" * 60)


# 使用示例
if __name__ == "__main__":
    # 创建报告生成器
    report_generator = BacktestReport()
    
    # 生成模拟数据
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=200)
    
    # 生成随机资产曲线
    daily_assets = [100000]
    for _ in range(1, 200):
        daily_assets.append(daily_assets[-1] * (1 + np.random.normal(0.001, 0.01)))
    
    report_generator.load_daily_data(daily_assets, dates)
    
    # 生成模拟交易数据
    trade_data = []
    for i in range(50):
        strategy = np.random.choice(['A', 'B', 'C', 'D'])
        trade_data.append({
            'ts_code': f'00000{i%3+1}.SZ',
            'date': dates[np.random.randint(0, 200)],
            'strategy': strategy,
            'profit': np.random.normal(100, 50)
        })
    
    report_generator.load_trade_data(trade_data)
    
    # 生成报告
    print("=== 生成回测报告 ===")
    reports = report_generator.generate_all_reports(show_plots=False)
    
    print(f"\n报告已保存到: {report_generator.output_dir}")
    
    # 打印报告文件
    print("\n生成的文件:")
    for name, path in reports.items():
        if path:
            print(f"- {name}: {path}")
    
    # 打印摘要信息
    print("\n=== 回测摘要 ===")
    report_generator.print_summary()