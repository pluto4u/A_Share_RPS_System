"""
================================================================================
日线情绪择时回测  backtest_sentiment_daily.py
================================================================================
根据每日情绪评分 → 仓位信号 对沪深300指数进行日线级别的择时回测。

使用:
    python backtest_sentiment_daily.py --start 2018-01-01 --end 2026-12-31
================================================================================
"""

import argparse
import os
from datetime import datetime

import numpy as np
import pandas as pd

from risk_engine import evaluate_risk_controls


def _max_recovery_days(nav_series):
    """Longest days required to recover a previous peak."""
    peak = -1.0
    drawdown_start = None
    max_days = 0
    for i, nav in enumerate(nav_series):
        if nav >= peak:
            if drawdown_start is not None:
                max_days = max(max_days, i - drawdown_start)
                drawdown_start = None
            peak = nav
        elif drawdown_start is None:
            drawdown_start = i
    return int(max_days)


def _max_loss_streak(returns):
    streak = 0
    max_streak = 0
    for r in returns:
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        elif r > 0:
            streak = 0
    return int(max_streak)


def run_backtest(price_df, signal_df, risk_context_df=None):
    """
    日线级别情绪择时回测。

    Parameters
    ----------
    price_df : pd.DataFrame
        至少包含 date(datetime), close(float)
    signal_df : pd.DataFrame
        至少包含 date(datetime), position(int, 0-100 表示仓位百分比)
    risk_context_df : pd.DataFrame | None
        可选风险上下文:
          date, daily_pnl_pct, rolling_drawdown_pct, loss_streak

    Returns
    -------
    dict
        包含 annual_return, max_drawdown, calmar, total_return, sharpe 等指标
    """
    # 合并并按日期排序
    df = pd.merge(price_df, signal_df, on="date", how="inner").sort_values("date").reset_index(drop=True)
    if risk_context_df is not None and not risk_context_df.empty:
        required = ["date", "daily_pnl_pct", "rolling_drawdown_pct", "loss_streak"]
        cols = [c for c in required if c in risk_context_df.columns]
        if "date" in cols:
            df = pd.merge(df, risk_context_df[cols], on="date", how="left")

    if len(df) < 2:
        return {
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "total_return": 0.0,
            "sharpe": 0.0,
            "trade_days": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_loss_streak": 0,
            "recovery_days": 0,
            "risk_halt_days": 0,
        }

    # 每日收益率
    df["daily_return"] = df["close"].pct_change().fillna(0.0)

    # 风控后仓位，再使用前一天信号 (避免未来函数)
    capped_positions = []
    risk_halt_days = 0
    for _, row in df.iterrows():
        risk_result = evaluate_risk_controls(
            position_before_risk=row["position"],
            context={
                "daily_pnl_pct": row.get("daily_pnl_pct", 0.0),
                "rolling_drawdown_pct": row.get("rolling_drawdown_pct", 0.0),
                "loss_streak": row.get("loss_streak", 0),
            },
        )
        capped_positions.append(risk_result["position_cap_after_risk"])
        if risk_result["halt_new_entries"]:
            risk_halt_days += 1

    df["position_capped"] = capped_positions
    df["weight"] = df["position_capped"].shift(1).fillna(0) / 100.0

    # 策略每日收益
    df["strategy_return"] = df["daily_return"] * df["weight"]

    # 净值曲线
    df["nav"] = (1 + df["strategy_return"]).cumprod()

    # 基准净值 (满仓持有)
    df["benchmark_nav"] = (1 + df["daily_return"]).cumprod()

    # 计算指标
    total_return = float(df["nav"].iloc[-1] / df["nav"].iloc[0] - 1) * 100
    trade_days = len(df)
    years = trade_days / 252.0

    annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # 最大回撤
    cummax = df["nav"].cummax()
    drawdown = (df["nav"] - cummax) / cummax
    max_drawdown = float(drawdown.min()) * 100  # 负数

    # Calmar = 年化收益 / |最大回撤|
    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # Sharpe (假设无风险利率 2%)
    excess_returns = df["strategy_return"] - 0.02 / 252
    sharpe = float(excess_returns.mean() / excess_returns.std() * np.sqrt(252)) if excess_returns.std() > 0 else 0.0
    active_returns = df.loc[df["weight"] > 0, "strategy_return"]
    win_rate = float((active_returns > 0).mean() * 100) if len(active_returns) > 0 else 0.0
    gross_profit = float(active_returns[active_returns > 0].sum()) if len(active_returns) > 0 else 0.0
    gross_loss = float(active_returns[active_returns < 0].sum()) if len(active_returns) > 0 else 0.0
    profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else (999.0 if gross_profit > 0 else 0.0)
    max_loss_streak = _max_loss_streak(active_returns.tolist())
    recovery_days = _max_recovery_days(df["nav"].tolist())

    # 基准指标
    bench_total = float(df["benchmark_nav"].iloc[-1] / df["benchmark_nav"].iloc[0] - 1) * 100
    bench_annual = ((1 + bench_total / 100) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    return {
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "calmar": round(calmar, 2),
        "total_return": round(total_return, 2),
        "sharpe": round(sharpe, 2),
        "trade_days": trade_days,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != 999.0 else 999.0,
        "max_loss_streak": int(max_loss_streak),
        "recovery_days": int(recovery_days),
        "risk_halt_days": int(risk_halt_days),
        "benchmark_annual_return": round(bench_annual, 2),
        "benchmark_total_return": round(bench_total, 2),
    }


def print_metrics(metrics):
    """终端打印回测指标"""
    print("\n" + "=" * 50)
    print("  日线情绪择时回测结果")
    print("=" * 50)
    print(f"  交易天数       : {metrics['trade_days']}")
    print(f"  策略总收益     : {metrics['total_return']:+.2f}%")
    print(f"  策略年化收益   : {metrics['annual_return']:+.2f}%")
    print(f"  最大回撤       : {metrics['max_drawdown']:.2f}%")
    print(f"  Calmar 比率    : {metrics['calmar']:.2f}")
    print(f"  Sharpe 比率    : {metrics['sharpe']:.2f}")
    print(f"  胜率           : {metrics.get('win_rate', 0):.2f}%")
    print(f"  盈亏比(ProfitF): {metrics.get('profit_factor', 0):.2f}")
    print(f"  最大连亏天数    : {metrics.get('max_loss_streak', 0)}")
    print(f"  回撤恢复天数    : {metrics.get('recovery_days', 0)}")
    print(f"  风控熔断天数    : {metrics.get('risk_halt_days', 0)}")
    print(f"  基准总收益     : {metrics.get('benchmark_total_return', 0):+.2f}%")
    print(f"  基准年化收益   : {metrics.get('benchmark_annual_return', 0):+.2f}%")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="日线情绪择时回测")
    parser.add_argument("--start", default="2018-01-01", help="回测起始日 (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-12-31", help="回测结束日 (YYYY-MM-DD)")
    args = parser.parse_args()

    print(f"  回测区间: {args.start} ~ {args.end}")
    print("  [注意] 完整回测需要先生成历史情绪信号文件，当前为框架入口。")
    print("  后续版本将接入历史数据回放 + sentiment_engine 生成每日信号。")

    # TODO: 实际回测需要:
    # 1. 从 akshare 获取 args.start ~ args.end 的沪深300日线
    # 2. 对每个交易日回放市场数据 → calculate_sentiment_score → score_to_position → final_position
    # 3. 生成 signal_df
    # 4. 调用 run_backtest(price_df, signal_df)

    # 保存报告目录
    os.makedirs("backtest/reports", exist_ok=True)
    print("  报告目录已就绪: backtest/reports/")


if __name__ == "__main__":
    main()
