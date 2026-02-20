#!/usr/bin/env python3
"""抖音战法回测系统 - 单元测试"""

import sys
import os
import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from emotion_validation.strategy_layer.douyin_system.market_breadth import MarketBreadth
from emotion_validation.strategy_layer.douyin_system.stock_screener import StockScreener
from emotion_validation.strategy_layer.douyin_system.trade_simulator import TradeSimulator
from emotion_validation.strategy_layer.douyin_system.risk_controller import RiskController
from emotion_validation.strategy_layer.douyin_system.backtest_report import BacktestReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_stock_data():
    """生成模拟股票日线数据（3只股票, 10个交易日）"""
    dates = pd.date_range('2024-01-01', periods=10, freq='B')
    codes = ['000001.SZ', '000002.SZ', '600000.SH']
    rows = []
    rng = np.random.default_rng(42)
    for code in codes:
        base = 10.0
        for i, d in enumerate(dates):
            o = base + rng.normal(0, 0.3)
            h = o + abs(rng.normal(0.3, 0.2))
            l = o - abs(rng.normal(0.2, 0.1))
            c = l + rng.random() * (h - l)
            vol = rng.integers(50000, 200000)
            pct = (c - base) / base * 100
            rows.append({
                'trade_date': d, 'ts_code': code,
                'open': round(o, 2), 'high': round(h, 2),
                'low': round(l, 2), 'close': round(c, 2),
                'vol': int(vol), 'pct_chg': round(pct, 2),
                'amount': int(vol * c),
            })
            base = c
    return pd.DataFrame(rows)


@pytest.fixture
def sample_ecology_data():
    """生成模拟生态数据"""
    dates = pd.date_range('2024-01-01', periods=10, freq='B')
    return pd.DataFrame({
        'trade_date': dates,
        'limit_up_count': np.random.default_rng(1).integers(5, 30, size=10),
        'limit_down_count': np.random.default_rng(2).integers(2, 15, size=10),
        'attempt_limit_up_count': np.random.default_rng(3).integers(3, 20, size=10),
    })


@pytest.fixture
def csv_data_dir(tmp_path, sample_stock_data, sample_ecology_data):
    """写入临时 CSV 文件，返回数据目录"""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    sample_stock_data.to_csv(data_dir / 'stock_daily.csv', index=False)
    sample_ecology_data.to_csv(data_dir / 'ecology_cache.csv', index=False)
    return data_dir


# ---------------------------------------------------------------------------
# MarketBreadth
# ---------------------------------------------------------------------------

class TestMarketBreadth:
    def test_load_csv(self, csv_data_dir):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        df = mb.load_data()
        assert len(df) == 30  # 3 stocks * 10 days
        assert 'ts_code' in df.columns
        assert 'pct_chg' in df.columns

    def test_calculate_daily_breadth(self, csv_data_dir, sample_stock_data):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        breadth = mb.calculate_daily_breadth(sample_stock_data)
        assert 'up_count' in breadth.columns
        assert 'down_count' in breadth.columns
        assert len(breadth) == 10

    def test_compute_signals(self, csv_data_dir, sample_stock_data):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        breadth = mb.calculate_daily_breadth(sample_stock_data)
        breadth['limit_up_count'] = 10
        breadth['limit_down_count'] = 5
        breadth['attempt_limit_up_count'] = 8
        signals = mb.compute_signals(breadth)
        assert 'is_buy_eligible' in signals.columns
        assert 'is_half_risk' in signals.columns
        assert 'is_full_risk' in signals.columns

    def test_compute_all_breadth(self, csv_data_dir):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        result = mb.compute_all_breadth()
        assert len(result) > 0
        assert len(mb.breadth_cache) > 0

    def test_get_daily_signal(self, csv_data_dir):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        signal = mb.get_daily_signal('2024-01-01')
        assert isinstance(signal, dict)
        if signal:
            assert 'up_count' in signal
            assert 'is_buy_eligible' in signal

    def test_date_range_filter(self, csv_data_dir):
        mb = MarketBreadth(str(csv_data_dir / 'stock_daily.csv'))
        result = mb.get_date_range('2024-01-03', '2024-01-07')
        for _, row in result.iterrows():
            assert row['trade_date'] >= pd.Timestamp('2024-01-03')
            assert row['trade_date'] <= pd.Timestamp('2024-01-07')


# ---------------------------------------------------------------------------
# StockScreener
# ---------------------------------------------------------------------------

class TestStockScreener:
    def test_load_csv(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        assert ss.stock_data is not None
        assert 'ts_code' in ss.stock_data.columns

    def test_strategy_a_returns_dataframe(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.strategy_a()
        assert isinstance(result, pd.DataFrame)

    def test_strategy_b_returns_dataframe(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.strategy_b()
        assert isinstance(result, pd.DataFrame)

    def test_strategy_c_returns_dataframe(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.strategy_c()
        assert isinstance(result, pd.DataFrame)

    def test_strategy_d_returns_dataframe(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.strategy_d()
        assert isinstance(result, pd.DataFrame)

    def test_screen_stocks_all_strategies(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.screen_stocks()
        assert isinstance(result, pd.DataFrame)

    def test_screen_stocks_single_strategy(self, csv_data_dir):
        ss = StockScreener(str(csv_data_dir / 'stock_daily.csv'))
        result = ss.screen_stocks(strategies=['A'])
        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# TradeSimulator
# ---------------------------------------------------------------------------

class TestTradeSimulator:
    def test_init_defaults(self):
        ts = TradeSimulator()
        assert ts.initial_capital == 100000.0
        assert ts.current_capital == 100000.0
        assert ts.portfolio == {}

    def test_set_transaction_cost(self):
        ts = TradeSimulator()
        ts.set_transaction_cost(0.002)
        assert ts.transaction_cost == 0.002

    def test_set_sell_parameters(self):
        ts = TradeSimulator()
        ts.set_sell_parameters(stop_loss=0.08, stop_profit=0.20, holding_period=5)
        assert ts.stop_loss_ratio == 0.08
        assert ts.stop_profit_ratio == 0.20
        assert ts.holding_period == 5

    def test_calculate_entry_price(self):
        ts = TradeSimulator()
        price = ts.calculate_entry_price(10.0, 11.0)
        expected = (10.0 + 11.0) / 2 * 0.95
        assert abs(price - expected) < 0.01

    def test_buy_and_portfolio(self):
        ts = TradeSimulator(initial_capital=100000.0)
        result = ts.buy(
            ts_code='000001.SZ',
            date='2024-01-02',
            open_price=10.0,
            high_price=11.0,
        )
        assert result is True
        assert '000001.SZ' in ts.portfolio
        assert ts.current_capital < 100000.0
        assert len(ts.trade_log) >= 1

    def test_sell_returns_cash(self):
        ts = TradeSimulator(initial_capital=100000.0)
        ts.buy(
            ts_code='000001.SZ',
            date='2024-01-02',
            open_price=10.0,
            high_price=11.0,
        )
        capital_after_buy = ts.current_capital
        ts.sell(
            ts_code='000001.SZ',
            date='2024-01-03',
            current_price=11.0,
        )
        assert ts.current_capital > capital_after_buy
        assert '000001.SZ' not in ts.portfolio

    def test_get_trade_summary(self):
        ts = TradeSimulator()
        summary = ts.get_trade_summary()
        assert isinstance(summary, dict)
        assert 'total_buys' in summary
        assert 'current_capital' in summary

    def test_reset(self):
        ts = TradeSimulator(initial_capital=50000.0)
        ts.current_capital = 30000.0
        ts.trade_log.append({'action': 'test'})
        ts.reset()
        assert ts.current_capital == 50000.0
        assert len(ts.trade_log) == 0


# ---------------------------------------------------------------------------
# RiskController
# ---------------------------------------------------------------------------

class TestRiskController:
    def test_init_defaults(self):
        rc = RiskController()
        assert rc.current_position == 0.0
        assert rc.continuous_loss_days == 0
        assert rc.pause_days == 0

    def test_set_risk_thresholds(self):
        rc = RiskController()
        rc.set_risk_thresholds(sell_half=2500, sell_all=3500)
        assert rc.risk_thresholds['sell_half'] == 2500
        assert rc.risk_thresholds['sell_all'] == 3500

    def test_check_market_risk_normal(self):
        rc = RiskController()
        action = rc.check_market_risk('2024-01-01', decline_count=1000)
        assert action in ('hold', 'normal', 'buy', None, '')

    def test_check_market_risk_half(self):
        rc = RiskController()
        action = rc.check_market_risk('2024-01-01', decline_count=3500)
        assert action is not None

    def test_check_market_risk_full(self):
        rc = RiskController()
        action = rc.check_market_risk('2024-01-01', decline_count=4500)
        assert action is not None

    def test_update_continuous_loss_days(self):
        rc = RiskController()
        rc.update_continuous_loss_days(-100)
        assert rc.continuous_loss_days >= 1
        rc.update_continuous_loss_days(50)
        assert rc.continuous_loss_days == 0

    def test_should_trade_initial(self):
        rc = RiskController()
        assert rc.should_trade('2024-01-01') is True

    def test_reset_risk_state(self):
        rc = RiskController()
        rc.continuous_loss_days = 5
        rc.pause_days = 3
        rc.reset_risk_state()
        assert rc.continuous_loss_days == 0
        assert rc.pause_days == 0


# ---------------------------------------------------------------------------
# BacktestReport
# ---------------------------------------------------------------------------

class TestBacktestReport:
    def test_init_creates_output_dir(self, tmp_path):
        out = tmp_path / 'reports'
        br = BacktestReport(str(out))
        assert out.exists()

    def test_load_trade_data_from_list(self, tmp_path):
        br = BacktestReport(str(tmp_path / 'reports'))
        trades = [
            {'ts_code': '000001.SZ', 'date': '2024-01-02', 'action': 'buy', 'price': 10.0},
            {'ts_code': '000001.SZ', 'date': '2024-01-05', 'action': 'sell', 'price': 11.0},
        ]
        br.load_trade_data(trades)
        assert br.trade_data is not None
        assert len(br.trade_data) == 2

    def test_load_daily_data(self, tmp_path):
        br = BacktestReport(str(tmp_path / 'reports'))
        daily_assets = [100000, 100500, 101000, 99800, 100200]
        br.load_daily_data(daily_assets)
        assert br.daily_data is not None
