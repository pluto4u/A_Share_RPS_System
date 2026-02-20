import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class TradeSimulator:
    """
    交易模拟器 - 日线级交易模拟
    - 买入价估算: (open + high) / 2 * 0.95
    - 卖出规则: 止损/三根K线/放量滞涨/盈利了结
    """
    
    LOT_SIZE = 100  # A股最小交易单位

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.portfolio = {}
        self.trade_log = []
        self.position = 0.0
        self.max_position = 1.0
        
        # A股真实交易成本
        self.buy_commission = 0.00025   # 买入佣金 0.025%
        self.sell_commission = 0.00025  # 卖出佣金 0.025%
        self.stamp_tax = 0.0005        # 印花税 0.05% (仅卖出)
        self.transfer_fee = 0.00001    # 过户费 0.001%
        self.slippage = 0.002          # 滑点 0.2%
        
        # 向后兼容: transaction_cost 现在只用于 get_trade_summary 统计
        self.transaction_cost = 0.003
        
        self.stop_loss_ratio = 0.05
        self.stop_profit_ratio = 0.15
        self.holding_period = 3
        self.volume_threshold = 2.0
    
    def set_transaction_cost(self, cost: float):
        """
        设置交易成本
        
        Args:
            cost: 交易成本比例
        """
        self.transaction_cost = cost
    
    def set_sell_parameters(self, 
                           stop_loss: float = 0.05,
                           stop_profit: float = 0.15,
                           holding_period: int = 3,
                           volume_threshold: float = 2.0):
        """
        设置卖出参数
        
        Args:
            stop_loss: 止损比例
            stop_profit: 止盈比例
            holding_period: 最大持有天数
            volume_threshold: 放量滞涨阈值
        """
        self.stop_loss_ratio = stop_loss
        self.stop_profit_ratio = stop_profit
        self.holding_period = holding_period
        self.volume_threshold = volume_threshold
    
    def get_portfolio_value(self, current_prices: dict) -> float:
        """
        计算当前持仓市值
        
        Args:
            current_prices: 当前价格字典 {ts_code: price}
        
        Returns:
            持仓市值
        """
        portfolio_value = 0.0
        
        for ts_code, info in self.portfolio.items():
            if ts_code in current_prices:
                portfolio_value += info['shares'] * current_prices[ts_code]
        
        return portfolio_value
    
    def get_total_assets(self, current_prices: dict) -> float:
        """
        计算总资产
        
        Args:
            current_prices: 当前价格字典 {ts_code: price}
        
        Returns:
            总资产
        """
        return self.current_capital + self.get_portfolio_value(current_prices)
    
    def calculate_position(self, current_prices: dict) -> float:
        """
        计算当前仓位
        
        Args:
            current_prices: 当前价格字典 {ts_code: price}
        
        Returns:
            仓位比例
        """
        total_assets = self.get_total_assets(current_prices)
        portfolio_value = self.get_portfolio_value(current_prices)
        
        if total_assets == 0:
            return 0.0
        
        return portfolio_value / total_assets
    
    def calculate_entry_price(self, open_price: float, high_price: float) -> float:
        """
        计算买入价格
        
        Args:
            open_price: 开盘价
            high_price: 最高价
        
        Returns:
            买入价格
        """
        return (open_price + high_price) / 2 * 0.95
    
    def _calc_buy_cost(self, amount: float) -> float:
        """买入总费用: 佣金 + 过户费 + 滑点"""
        commission = max(amount * self.buy_commission, 5.0)
        return commission + amount * self.transfer_fee + amount * self.slippage

    def _calc_sell_cost(self, amount: float) -> float:
        """卖出总费用: 佣金 + 印花税 + 过户费 + 滑点"""
        commission = max(amount * self.sell_commission, 5.0)
        return commission + amount * self.stamp_tax + amount * self.transfer_fee + amount * self.slippage

    def buy(self, ts_code: str, 
           date: str or datetime, 
           open_price: float, 
           high_price: float, 
           available_funds: float = None,
           max_shares: int = None):
        if available_funds is None:
            available_funds = self.current_capital
        
        entry_price = self.calculate_entry_price(open_price, high_price)
        if entry_price <= 0:
            return False
        
        cost_rate = self.buy_commission + self.transfer_fee + self.slippage
        max_amount = available_funds / (1 + cost_rate)
        max_purchase_shares = int(max_amount / entry_price / self.LOT_SIZE) * self.LOT_SIZE
        
        if max_shares:
            max_shares = int(max_shares / self.LOT_SIZE) * self.LOT_SIZE
            if max_shares < max_purchase_shares:
                max_purchase_shares = max_shares
        
        purchase_shares = max_purchase_shares
        if purchase_shares < self.LOT_SIZE:
            return False
        
        purchase_amount = purchase_shares * entry_price
        transaction_fee = self._calc_buy_cost(purchase_amount)
        total_cost = purchase_amount + transaction_fee
        
        if total_cost > self.current_capital:
            return False
        
        self.current_capital -= total_cost
        
        if ts_code in self.portfolio:
            existing = self.portfolio[ts_code]
            new_total_shares = existing['shares'] + purchase_shares
            new_total_cost = existing['shares'] * existing['cost_price'] + purchase_amount
            new_cost = new_total_cost / new_total_shares
            self.portfolio[ts_code] = {
                'shares': new_total_shares,
                'cost_price': new_cost,
                'purchase_dates': existing['purchase_dates'] + [date],
                'purchase_prices': existing['purchase_prices'] + [entry_price],
                'purchase_amounts': existing['purchase_amounts'] + [purchase_amount],
                'holding_days': existing.get('holding_days', 0),
                'buy_date': existing.get('buy_date', date),
            }
        else:
            self.portfolio[ts_code] = {
                'shares': purchase_shares,
                'cost_price': entry_price,
                'purchase_dates': [date],
                'purchase_prices': [entry_price],
                'purchase_amounts': [purchase_amount],
                'holding_days': 0,
                'buy_date': date,
            }
        
        self.trade_log.append({
            'ts_code': ts_code,
            'date': date,
            'action': 'buy',
            'shares': purchase_shares,
            'price': entry_price,
            'amount': purchase_amount,
            'fee': transaction_fee,
            'total_cost': total_cost,
            'capital': self.current_capital
        })
        
        return True
    
    def sell(self, ts_code: str, 
            date: str or datetime, 
            current_price: float,
            shares: int = None):
        if ts_code not in self.portfolio:
            return False
        
        holding = self.portfolio[ts_code]
        
        # T+1 约束: 买入当天不能卖出
        buy_date = holding.get('buy_date')
        if buy_date is not None:
            d = pd.to_datetime(date) if isinstance(date, str) else date
            bd = pd.to_datetime(buy_date) if isinstance(buy_date, str) else buy_date
            if d <= bd:
                return False
        
        available_shares = holding['shares']
        if shares is None or shares > available_shares:
            shares = available_shares
        if shares <= 0:
            return False
        
        sell_amount = shares * current_price
        transaction_fee = self._calc_sell_cost(sell_amount)
        net_amount = sell_amount - transaction_fee
        
        self.current_capital += net_amount
        
        if shares == available_shares:
            del self.portfolio[ts_code]
        else:
            self.portfolio[ts_code]['shares'] -= shares
        
        self.trade_log.append({
            'ts_code': ts_code,
            'date': date,
            'action': 'sell',
            'shares': shares,
            'price': current_price,
            'amount': sell_amount,
            'fee': transaction_fee,
            'total_revenue': net_amount,
            'capital': self.current_capital
        })
        
        return True
    
    def should_sell(self, ts_code: str,
                  current_price: float,
                  date: str or datetime,
                  volume: float,
                  avg_volume: float,
                  latest_high: float) -> str:
        """
        判断是否应该卖出
        
        Args:
            ts_code: 股票代码
            current_price: 当前价格
            date: 日期
            volume: 当前成交量
            avg_volume: 平均成交量
            latest_high: 近期最高价
        
        Returns:
            卖出原因
        """
        if ts_code not in self.portfolio:
            return None
        
        holding_info = self.portfolio[ts_code]
        
        # 计算收益率
        return_rate = (current_price - holding_info['cost_price']) / holding_info['cost_price']
        
        # 止损规则
        if return_rate <= -self.stop_loss_ratio:
            return 'stop_loss'
        
        # 止盈规则
        if return_rate >= self.stop_profit_ratio:
            return 'stop_profit'
        
        # 持有期规则
        purchase_dates = holding_info['purchase_dates']
        if purchase_dates:
            first_purchase_date = purchase_dates[0]
            if isinstance(date, str):
                date = pd.to_datetime(date)
            if isinstance(first_purchase_date, str):
                first_purchase_date = pd.to_datetime(first_purchase_date)
            
            holding_days = (date - first_purchase_date).days
            if holding_days >= self.holding_period:
                return 'holding_period'
        
        # 放量滞涨规则
        if volume > avg_volume * self.volume_threshold and current_price < latest_high * 0.98:
            return 'volume_surge'
        
        return None
    
    def execute_sell_rules(self, 
                          stocks_to_check: list,
                          date: str or datetime,
                          current_prices: dict,
                          volumes: dict,
                          avg_volumes: dict,
                          latest_highs: dict) -> list:
        """
        执行卖出规则
        
        Args:
            stocks_to_check: 需要检查的股票列表
            date: 日期
            current_prices: 当前价格
            volumes: 当前成交量
            avg_volumes: 平均成交量
            latest_highs: 近期最高价
        
        Returns:
            卖出的股票列表
        """
        sold_stocks = []
        
        for ts_code in stocks_to_check:
            if ts_code not in self.portfolio:
                continue
            
            if ts_code not in current_prices:
                continue
            
            reason = self.should_sell(
                ts_code,
                current_prices[ts_code],
                date,
                volumes.get(ts_code, 0),
                avg_volumes.get(ts_code, 0),
                latest_highs.get(ts_code, current_prices[ts_code])
            )
            
            if reason:
                success = self.sell(
                    ts_code,
                    date,
                    current_prices[ts_code]
                )
                
                if success:
                    sold_stocks.append({
                        'ts_code': ts_code,
                        'date': date,
                        'reason': reason,
                        'price': current_prices[ts_code]
                    })
        
        return sold_stocks
    
    def execute_buy_rules(self, 
                         selected_stocks: list,
                         date: str or datetime,
                         open_prices: dict,
                         high_prices: dict,
                         target_position: float = 1.0):
        """
        执行买入规则
        
        Args:
            selected_stocks: 选股结果
            date: 日期
            open_prices: 开盘价
            high_prices: 最高价
            target_position: 目标仓位
        
        Returns:
            买入的股票列表
        """
        if not selected_stocks:
            return []
        
        bought_stocks = []
        
        # 计算目标购买金额
        total_assets = self.get_total_assets({ts_code: open_prices[ts_code] for ts_code in selected_stocks if ts_code in open_prices})
        current_position = self.calculate_position({ts_code: open_prices[ts_code] for ts_code in selected_stocks if ts_code in open_prices})
        
        if current_position >= target_position:
            return bought_stocks
        
        available_buy_amount = (target_position - current_position) * total_assets
        
        if available_buy_amount <= 0:
            return bought_stocks
        
        # 平均分配资金
        per_stock_buy_amount = available_buy_amount / len(selected_stocks)
        
        for ts_code in selected_stocks:
            if ts_code not in open_prices or ts_code not in high_prices:
                continue
            
            # 计算最大可买数量
            entry_price = self.calculate_entry_price(open_prices[ts_code], high_prices[ts_code])
            max_shares = per_stock_buy_amount / entry_price
            
            success = self.buy(
                ts_code,
                date,
                open_prices[ts_code],
                high_prices[ts_code],
                available_funds=per_stock_buy_amount,
                max_shares=max_shares
            )
            
            if success:
                bought_stocks.append({
                    'ts_code': ts_code,
                    'date': date,
                    'price': entry_price
                })
        
        return bought_stocks
    
    def simulate_day(self, date: str or datetime,
                   selected_stocks: list,
                   daily_prices: dict,
                   daily_volumes: dict,
                   avg_volumes: dict,
                   latest_highs: dict,
                   decline_count: int,
                   risk_controller=None):
        """
        模拟一天的交易
        
        Args:
            date: 日期
            selected_stocks: 当日选股结果
            daily_prices: 当日价格 {ts_code: {open, high, low, close}}
            daily_volumes: 当日成交量 {ts_code: vol}
            avg_volumes: 平均成交量 {ts_code: avg_vol}
            latest_highs: 近期最高价 {ts_code: high}
            decline_count: 下跌家数
            risk_controller: 风险控制器
        
        Returns:
            当日交易记录
        """
        day_log = {
            'date': date,
            'bought': [],
            'sold': []
        }
        
        # 1. 执行卖出操作
        portfolio_stocks = list(self.portfolio.keys())
        current_prices = {ts_code: daily_prices[ts_code]['close'] for ts_code in daily_prices if ts_code in portfolio_stocks}
        volumes = {ts_code: daily_volumes[ts_code] for ts_code in daily_volumes if ts_code in portfolio_stocks}
        avg_vols = {ts_code: avg_volumes[ts_code] for ts_code in avg_volumes if ts_code in portfolio_stocks}
        high_prices = {ts_code: latest_highs[ts_code] for ts_code in latest_highs if ts_code in portfolio_stocks}
        
        sold_stocks = self.execute_sell_rules(
            portfolio_stocks,
            date,
            current_prices,
            volumes,
            avg_vols,
            high_prices
        )
        
        day_log['sold'] = sold_stocks
        
        # 2. 计算目标仓位
        target_position = self.max_position
        
        if risk_controller:
            current_price_info = {
                'current_capital': self.current_capital,
                'portfolio_value': self.get_portfolio_value(current_prices),
                'total_assets': self.get_total_assets(current_prices),
                'current_position': self.calculate_position(current_prices)
            }
            
            action = risk_controller.determine_action(
                date,
                decline_count,
                current_price_info['current_position'],
                0,
                0
            )
            
            if action == 'sell_half':
                target_position = max(0.0, current_price_info['current_position'] * 0.5)
            elif action == 'sell_all':
                target_position = 0.0
            elif action == 'pause':
                target_position = current_price_info['current_position']
        
        # 3. 执行买入操作
        open_prices = {ts_code: daily_prices[ts_code]['open'] for ts_code in daily_prices if ts_code in selected_stocks}
        high_prices = {ts_code: daily_prices[ts_code]['high'] for ts_code in daily_prices if ts_code in selected_stocks}
        
        bought_stocks = self.execute_buy_rules(
            selected_stocks,
            date,
            open_prices,
            high_prices,
            target_position
        )
        
        day_log['bought'] = bought_stocks
        
        return day_log
    
    def update_holding_days(self, date: str or datetime):
        """
        更新持股天数
        
        Args:
            date: 日期
        """
        for ts_code, info in self.portfolio.items():
            if 'holding_days' in info:
                info['holding_days'] += 1
    
    def get_trade_summary(self) -> dict:
        """
        获取交易摘要
        
        Returns:
            交易摘要
        """
        total_buys = sum(1 for trade in self.trade_log if trade['action'] == 'buy')
        total_sells = sum(1 for trade in self.trade_log if trade['action'] == 'sell')
        total_volume = 0.0
        total_fees = 0.0
        
        for trade in self.trade_log:
            if trade['action'] == 'buy':
                total_volume += trade['amount']
                total_fees += trade['fee']
            elif trade['action'] == 'sell':
                total_volume += trade['amount']
                total_fees += trade['fee']
        
        return {
            'initial_capital': self.initial_capital,
            'current_capital': self.current_capital,
            'current_portfolio': len(self.portfolio),
            'total_buys': total_buys,
            'total_sells': total_sells,
            'total_volume': total_volume,
            'total_fees': total_fees,
            'active_trades': len([t for t in self.trade_log if t['action'] == 'buy']),
            'closed_trades': len([t for t in self.trade_log if t['action'] == 'sell'])
        }
    
    def get_trade_log(self) -> pd.DataFrame:
        """
        获取交易日志
        
        Returns:
            交易日志DataFrame
        """
        return pd.DataFrame(self.trade_log)
    
    def reset(self):
        """
        重置模拟状态
        """
        self.current_capital = self.initial_capital
        self.portfolio = {}
        self.trade_log = []
        self.position = 0.0
    
    def calculate_total_return(self, final_prices: dict) -> float:
        """
        计算总收益率
        
        Args:
            final_prices: 最终价格
        
        Returns:
            总收益率
        """
        final_assets = self.get_total_assets(final_prices)
        return (final_assets - self.initial_capital) / self.initial_capital
    
    def get_max_drawdown(self, daily_assets: list) -> float:
        """
        计算最大回撤
        
        Args:
            daily_assets: 每日资产列表
        
        Returns:
            最大回撤
        """
        if not daily_assets:
            return 0.0
        
        peak = daily_assets[0]
        max_drawdown = 0.0
        
        for asset in daily_assets:
            if asset > peak:
                peak = asset
            drawdown = (peak - asset) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        return max_drawdown
    
    def get_sharpe_ratio(self, daily_returns: list, risk_free_rate: float = 0.03) -> float:
        """
        计算夏普比率
        
        Args:
            daily_returns: 日收益率列表
            risk_free_rate: 无风险收益率
        
        Returns:
            夏普比率
        """
        if not daily_returns:
            return 0.0
        
        returns = np.array(daily_returns)
        avg_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        excess_return = avg_return - risk_free_rate / 252
        return excess_return / std_return * np.sqrt(252)
    
    def generate_performance_report(self, daily_assets: list) -> dict:
        """
        生成绩效报告
        
        Args:
            daily_assets: 每日资产列表
        
        Returns:
            绩效报告
        """
        if not daily_assets:
            return {}
        
        final_return = self.calculate_total_return(daily_assets[-1])
        daily_returns = []
        
        for i in range(1, len(daily_assets)):
            daily_returns.append((daily_assets[i] - daily_assets[i-1]) / daily_assets[i-1])
        
        max_drawdown = self.get_max_drawdown(daily_assets)
        sharpe_ratio = self.get_sharpe_ratio(daily_returns)
        
        return {
            'initial_capital': self.initial_capital,
            'final_assets': daily_assets[-1],
            'total_return': final_return,
            'daily_returns': daily_returns,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'trade_summary': self.get_trade_summary()
        }


# 使用示例
if __name__ == "__main__":
    # 创建交易模拟器
    simulator = TradeSimulator(initial_capital=100000.0)
    
    # 测试基本功能
    print("=== 交易模拟器测试 ===")
    
    # 设置参数
    simulator.set_sell_parameters(
        stop_loss=0.05,
        stop_profit=0.15,
        holding_period=3,
        volume_threshold=2.0
    )
    
    print("\n=== 初始资金 ===")
    print(f"初始资金: {simulator.initial_capital:.2f}")
    print(f"当前资金: {simulator.current_capital:.2f}")
    print(f"持仓股票: {list(simulator.portfolio.keys())}")
    
    print("\n=== 模拟买入 ===")
    buy_success = simulator.buy('000001.SZ', '2024-01-01', 10.0, 11.0)
    print(f"买入成功: {buy_success}")
    print(f"持仓信息: {simulator.portfolio}")
    print(f"剩余资金: {simulator.current_capital:.2f}")
    
    print("\n=== 模拟卖出 ===")
    sell_success = simulator.sell('000001.SZ', '2024-01-02', 10.5)
    print(f"卖出成功: {sell_success}")
    print(f"持仓信息: {simulator.portfolio}")
    print(f"剩余资金: {simulator.current_capital:.2f}")
    
    print("\n=== 交易摘要 ===")
    summary = simulator.get_trade_summary()
    for key, value in summary.items():
        if isinstance(value, float):
            print(f"{key}: {value:.2f}")
        else:
            print(f"{key}: {value}")
    
    print("\n=== 交易日志 ===")
    log_df = simulator.get_trade_log()
    print(log_df)