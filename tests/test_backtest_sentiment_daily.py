import pandas as pd

from backtest_sentiment_daily import run_backtest


def _mock_price_df():
    """模拟沪深300每日收盘价序列"""
    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    prices = [100 + i * 0.5 for i in range(60)]
    return pd.DataFrame({"date": dates, "close": prices})


def _mock_signal_df():
    """模拟每日情绪仓位信号序列"""
    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    positions = [60] * 30 + [30] * 30  # 前30天60%仓位，后30天30%仓位
    return pd.DataFrame({"date": dates, "position": positions})


def test_backtest_outputs_metrics_dict():
    metrics = run_backtest(_mock_price_df(), _mock_signal_df())
    assert "annual_return" in metrics
    assert "max_drawdown" in metrics
    assert "calmar" in metrics
