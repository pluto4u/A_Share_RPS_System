from rps_analyzer.dragon_pullback_finder import identify_dragon_pullback


def test_dragon_strategy_config_constants():
    from config.dragon_strategy import DRAGON_STRATEGY

    assert DRAGON_STRATEGY["finder"]["stop_loss_pct"] == 0.05
    assert DRAGON_STRATEGY["finder"]["vol_shrink_pct"] == 0.20
    assert DRAGON_STRATEGY["finder"]["dragon_rise_pct"] == 0.25
    assert DRAGON_STRATEGY["finder"]["drawdown_min"] == 0.12
    assert DRAGON_STRATEGY["finder"]["drawdown_max"] == 0.40
    assert DRAGON_STRATEGY["backtest"]["trailing_stop_pct"] == 0.15
    assert DRAGON_STRATEGY["backtest"]["min_hold_days"] == 3
    assert DRAGON_STRATEGY["backtest"]["breakeven_trigger_pct"] == 0.05


def test_finder_defaults_match_strategy_config():
    from config.dragon_strategy import DRAGON_STRATEGY

    defaults = identify_dragon_pullback.__defaults__
    assert defaults is not None

    # Signature defaults after max_risk_pct:
    # stop_loss_pct, vol_shrink_pct, dragon_rise_pct, drawdown_min, drawdown_max
    assert defaults[2] == DRAGON_STRATEGY["finder"]["stop_loss_pct"]
    assert defaults[3] == DRAGON_STRATEGY["finder"]["vol_shrink_pct"]
    assert defaults[4] == DRAGON_STRATEGY["finder"]["dragon_rise_pct"]
    assert defaults[5] == DRAGON_STRATEGY["finder"]["drawdown_min"]
    assert defaults[6] == DRAGON_STRATEGY["finder"]["drawdown_max"]
