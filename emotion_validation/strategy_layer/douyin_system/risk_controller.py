import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class RiskController:
    """
    风控引擎 - 实现交易风险控制
    
    核心功能:
    - 连亏2天减仓50%、连亏3天清仓暂停3天
    - 3000家下跌砍半仓、4000家清仓
    - 仓位管理
    - 止损止盈管理
    """
    
    def __init__(self):
        """
        初始化风控引擎
        """
        self.trade_log = []  # 交易日志
        self.current_position = 0.0  # 当前仓位
        self.max_position = 1.0  # 最大仓位
        self.stop_loss_ratio = 0.05  # 止损比例 (5%)
        self.stop_profit_ratio = 0.15  # 止盈比例 (15%)
        self.continuous_loss_days = 0  # 连续亏损天数
        self.pause_days = 0  # 暂停交易天数
        self.last_pause_date = None  # 最后暂停日期
        self.risk_thresholds = {
            'sell_half': 3000,  # 下跌家数 >= 3000 减仓
            'sell_all': 4000,   # 下跌家数 >= 4000 清仓
            'continuous_loss_2': 0.5,  # 连亏2天减仓
            'continuous_loss_3': 0.0  # 连亏3天清仓
        }
    
    def set_risk_thresholds(self, **kwargs):
        """
        设置风险阈值
        
        Args:
            sell_half: 下跌家数 >= X 减仓
            sell_all: 下跌家数 >= X 清仓
            continuous_loss_2: 连亏2天减仓比例
            continuous_loss_3: 连亏3天减仓比例
        """
        for key, value in kwargs.items():
            if key in self.risk_thresholds:
                self.risk_thresholds[key] = value
    
    def update_trade_log(self, trade: dict):
        """
        更新交易日志
        
        Args:
            trade: 交易记录
        """
        self.trade_log.append(trade)
    
    def calculate_daily_result(self, date: str or datetime, daily_result: float):
        """
        计算每日结果
        
        Args:
            date: 日期
            daily_result: 当日盈亏
        """
        # 更新连续亏损天数
        if daily_result < 0:
            self.continuous_loss_days += 1
        else:
            self.continuous_loss_days = 0
        
        # 更新暂停天数
        if self.pause_days > 0:
            self.pause_days -= 1
    
    def check_market_risk(self, date: str or datetime, decline_count: int) -> str:
        """
        检查市场风险
        
        Args:
            date: 日期
            decline_count: 下跌家数
        
        Returns:
            'hold': 持有
            'sell_half': 减仓
            'sell_all': 清仓
            'pause': 暂停交易
        """
        # 检查是否在暂停期
        if self.pause_days > 0:
            return 'pause'
        
        # 检查市场下跌家数
        if decline_count >= self.risk_thresholds['sell_all']:
            return 'sell_all'
        elif decline_count >= self.risk_thresholds['sell_half']:
            return 'sell_half'
        
        return 'hold'
    
    def check_position_risk(self, position: float, cost_price: float, current_price: float) -> str:
        """
        检查持仓风险
        
        Args:
            position: 仓位
            cost_price: 成本价
            current_price: 当前价格
        
        Returns:
            'hold': 持有
            'sell_half': 减仓
            'sell_all': 清仓
        """
        if position <= 0:
            return 'hold'
        
        # 计算收益率
        return_rate = (current_price - cost_price) / cost_price
        
        # 止盈
        if return_rate >= self.stop_profit_ratio:
            return 'sell_all'
        
        # 止损
        if return_rate <= -self.stop_loss_ratio:
            return 'sell_all'
        
        return 'hold'
    
    def check_continuous_loss(self) -> str:
        """
        检查连续亏损风险
        
        Returns:
            'hold': 持有
            'sell_half': 减仓
            'sell_all': 清仓
            'pause': 暂停交易
        """
        if self.continuous_loss_days >= 3:
            self.pause_days = 3
            self.last_pause_date = datetime.now()
            return 'sell_all'
        elif self.continuous_loss_days == 2:
            return 'sell_half'
        
        return 'hold'
    
    def determine_action(self, 
                       date: str or datetime,
                       decline_count: int = 0,
                       position: float = 0.0,
                       cost_price: float = 0.0,
                       current_price: float = 0.0) -> str:
        """
        综合判断交易策略
        
        Args:
            date: 日期
            decline_count: 下跌家数
            position: 仓位
            cost_price: 成本价
            current_price: 当前价格
        
        Returns:
            交易策略
        """
        # 检查暂停期
        if self.pause_days > 0:
            return 'pause'
        
        # 检查市场风险
        market_action = self.check_market_risk(date, decline_count)
        if market_action in ['sell_half', 'sell_all']:
            return market_action
        
        # 检查连续亏损
        loss_action = self.check_continuous_loss()
        if loss_action in ['sell_half', 'sell_all']:
            return loss_action
        
        # 检查持仓风险
        position_action = self.check_position_risk(position, cost_price, current_price)
        if position_action in ['sell_half', 'sell_all']:
            return position_action
        
        return 'hold'
    
    def calculate_position_adjustment(self, current_position: float, action: str) -> float:
        """
        计算仓位调整
        
        Args:
            current_position: 当前仓位
            action: 交易策略
        
        Returns:
            目标仓位
        """
        if action == 'hold':
            return current_position
        elif action == 'sell_half':
            return max(0.0, current_position * 0.5)
        elif action == 'sell_all':
            return 0.0
        elif action == 'pause':
            return current_position
        else:
            return current_position
    
    def get_risk_report(self) -> dict:
        """
        获取风险报告
        
        Returns:
            风险报告
        """
        # 计算交易统计
        if not self.trade_log:
            return {
                'total_trades': 0,
                'win_count': 0,
                'lose_count': 0,
                'win_rate': 0.0,
                'avg_profit': 0.0,
                'max_drawdown': 0.0,
                'current_position': self.current_position,
                'continuous_loss_days': self.continuous_loss_days,
                'pause_days_remaining': self.pause_days
            }
        
        # 计算盈亏统计
        trades_df = pd.DataFrame(self.trade_log)
        
        win_count = len(trades_df[trades_df['profit'] > 0])
        lose_count = len(trades_df[trades_df['profit'] < 0])
        win_rate = win_count / len(trades_df) if len(trades_df) > 0 else 0.0
        avg_profit = trades_df['profit'].mean() if len(trades_df) > 0 else 0.0
        
        # 计算最大回撤
        cumulative_profit = trades_df['profit'].cumsum()
        if len(cumulative_profit) > 0:
            peak = cumulative_profit.expanding().max()
            drawdown = (cumulative_profit - peak) / peak
            max_drawdown = drawdown.min()
        else:
            max_drawdown = 0.0
        
        return {
            'total_trades': len(trades_df),
            'win_count': win_count,
            'lose_count': lose_count,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'max_drawdown': max_drawdown,
            'current_position': self.current_position,
            'continuous_loss_days': self.continuous_loss_days,
            'pause_days_remaining': self.pause_days
        }
    
    def should_trade(self, date: str or datetime) -> bool:
        """
        判断是否应该交易
        
        Args:
            date: 日期
        
        Returns:
            是否应该交易
        """
        if self.pause_days > 0:
            return False
        
        return True
    
    def calculate_position_size(self, 
                              base_size: float, 
                              stock_count: int, 
                              max_position_per_stock: float = 0.1) -> float:
        """
        计算仓位大小
        
        Args:
            base_size: 基础仓位
            stock_count: 股票数量
            max_position_per_stock: 单只股票最大仓位
        
        Returns:
            单只股票仓位
        """
        single_position = base_size / stock_count if stock_count > 0 else 0
        return min(single_position, max_position_per_stock)
    
    def get_current_position(self) -> float:
        """
        获取当前仓位
        
        Returns:
            当前仓位
        """
        return self.current_position
    
    def set_current_position(self, position: float):
        """
        设置当前仓位
        
        Args:
            position: 仓位
        """
        self.current_position = max(0.0, min(position, self.max_position))
    
    def update_continuous_loss_days(self, daily_profit: float):
        """
        更新连续亏损天数
        
        Args:
            daily_profit: 当日盈亏
        """
        if daily_profit < 0:
            self.continuous_loss_days += 1
        else:
            self.continuous_loss_days = 0
    
    def get_pause_info(self) -> dict:
        """
        获取暂停信息
        
        Returns:
            暂停信息
        """
        return {
            'pause_days_remaining': self.pause_days,
            'last_pause_date': self.last_pause_date
        }
    
    def reset_risk_state(self):
        """
        重置风险状态
        """
        self.continuous_loss_days = 0
        self.pause_days = 0
        self.last_pause_date = None
    
    def calculate_position_risk_level(self, position: float, cost_price: float, current_price: float) -> float:
        """
        计算持仓风险等级
        
        Args:
            position: 仓位
            cost_price: 成本价
            current_price: 当前价格
        
        Returns:
            风险等级 (0-1)
        """
        if position <= 0:
            return 0.0
        
        return_rate = (current_price - cost_price) / cost_price
        
        # 根据收益率计算风险等级
        if return_rate <= -self.stop_loss_ratio:
            return 1.0
        elif return_rate <= -0.02:
            return 0.8
        elif return_rate <= 0.02:
            return 0.3
        elif return_rate <= self.stop_profit_ratio:
            return 0.2
        else:
            return 0.1
    
    def calculate_market_risk_level(self, decline_count: int) -> float:
        """
        计算市场风险等级
        
        Args:
            decline_count: 下跌家数
        
        Returns:
            风险等级 (0-1)
        """
        if decline_count >= self.risk_thresholds['sell_all']:
            return 1.0
        elif decline_count >= self.risk_thresholds['sell_half']:
            return 0.8
        elif decline_count >= 2000:
            return 0.5
        else:
            return 0.2
    
    def get_overall_risk_level(self, 
                            position: float, 
                            cost_price: float, 
                            current_price: float, 
                            decline_count: int) -> float:
        """
        计算综合风险等级
        
        Args:
            position: 仓位
            cost_price: 成本价
            current_price: 当前价格
            decline_count: 下跌家数
        
        Returns:
            综合风险等级 (0-1)
        """
        position_risk = self.calculate_position_risk_level(position, cost_price, current_price)
        market_risk = self.calculate_market_risk_level(decline_count)
        
        # 权重平均
        return position_risk * 0.6 + market_risk * 0.4
    
    def get_recommendation(self, 
                        date: str or datetime, 
                        decline_count: int, 
                        position: float, 
                        cost_price: float, 
                        current_price: float) -> dict:
        """
        获取风控建议
        
        Args:
            date: 日期
            decline_count: 下跌家数
            position: 仓位
            cost_price: 成本价
            current_price: 当前价格
        
        Returns:
            风控建议
        """
        action = self.determine_action(date, decline_count, position, cost_price, current_price)
        target_position = self.calculate_position_adjustment(position, action)
        risk_level = self.get_overall_risk_level(position, cost_price, current_price, decline_count)
        
        return {
            'action': action,
            'target_position': target_position,
            'risk_level': risk_level,
            'decline_count': decline_count,
            'return_rate': (current_price - cost_price) / cost_price if cost_price > 0 else 0,
            'continuous_loss_days': self.continuous_loss_days,
            'pause_days_remaining': self.pause_days
        }


# 使用示例
if __name__ == "__main__":
    # 创建风控引擎实例
    risk_controller = RiskController()
    
    # 测试基本功能
    print("=== 风控引擎测试 ===")
    
    # 设置风险阈值
    risk_controller.set_risk_thresholds(
        sell_half=3000,
        sell_all=4000
    )
    
    # 测试市场风险检查
    print("\n=== 市场风险检查 ===")
    for decline_count in [1500, 2500, 3500, 4500]:
        action = risk_controller.check_market_risk('2024-01-01', decline_count)
        print(f"下跌家数: {decline_count}, 风险等级: {risk_controller.calculate_market_risk_level(decline_count):.2f}, 操作: {action}")
    
    # 测试连续亏损检查
    print("\n=== 连续亏损检查 ===")
    risk_controller.update_continuous_loss_days(-100)
    risk_controller.update_continuous_loss_days(-200)
    action = risk_controller.check_continuous_loss()
    print(f"连续亏损天数: {risk_controller.continuous_loss_days}, 操作: {action}")
    
    risk_controller.update_continuous_loss_days(-300)
    action = risk_controller.check_continuous_loss()
    print(f"连续亏损天数: {risk_controller.continuous_loss_days}, 操作: {action}")
    
    # 测试持仓风险检查
    print("\n=== 持仓风险检查 ===")
    for return_rate in [-0.06, -0.03, 0.05, 0.16]:
        cost_price = 100
        current_price = cost_price * (1 + return_rate)
        action = risk_controller.check_position_risk(1.0, cost_price, current_price)
        print(f"收益率: {return_rate:.2%}, 操作: {action}, 风险等级: {risk_controller.calculate_position_risk_level(1.0, cost_price, current_price):.2f}")
    
    # 测试综合判断
    print("\n=== 综合判断 ===")
    recommendation = risk_controller.get_recommendation(
        '2024-01-01',
        decline_count=2800,
        position=0.8,
        cost_price=100,
        current_price=98
    )
    print(recommendation)
    
    # 获取风险报告
    print("\n=== 风险报告 ===")
    report = risk_controller.get_risk_report()
    for key, value in report.items():
        print(f"{key}: {value}")