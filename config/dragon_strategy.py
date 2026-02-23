"""Single source of truth for dragon pullback strategy parameters."""

DRAGON_STRATEGY = {
    "emotion": {
        "min_score_for_stock_entries": 37.5,
    },
    "universe": {
        "top_active_stocks": 1000,     # amount 模式下的数量
        "min_history_bars": 60,
        # RPS 选股池 (与 ETF 轮动完全独立)
        "source": "amount",            # "rps" | "amount"
        "rps_periods": [120, 250],
        "rps_min_120": 80.0,           # 回踩后 RPS 会回落，80 更易出信号
        "rps_min_250": 80.0,
        "rps_use_prev_day": True,      # 用前一日 RPS 判断（回踩日多为阴线）
    },
    "finder": {
        "ma_support": 20,           # 主力支撑线(回测黄金参数)
        "ma_support_fast": 10,      # 快线支撑(10日线，趋势龙早期回踩信号)
        "max_risk_pct": 0.08,
        "stop_loss_pct": 0.05,
        "vol_shrink_pct": 0.20,
        "dragon_rise_pct": 0.25,
        "drawdown_min": 0.12,
        "drawdown_max": 0.40,
    },
    "backtest": {
        "initial_capital": 1_000_000,
        "max_pos_pct": 0.30,
        "trailing_stop_pct": 0.15,
        "min_hold_days": 3,
        "breakeven_trigger_pct": 0.05,
    },
}
