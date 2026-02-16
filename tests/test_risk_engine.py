from risk_engine import evaluate_risk_controls


def test_risk_engine_no_trigger_keeps_full_cap():
    result = evaluate_risk_controls(
        position_before_risk=60,
        context={
            "daily_pnl_pct": -0.5,
            "rolling_drawdown_pct": -3.0,
            "loss_streak": 1,
        },
    )
    assert result["risk_state"] == "normal"
    assert result["halt_new_entries"] is False
    assert result["position_cap_after_risk"] == 60
    assert result["halt_reason"] == ""


def test_risk_engine_daily_loss_halts_new_entries():
    result = evaluate_risk_controls(
        position_before_risk=60,
        context={
            "daily_pnl_pct": -3.2,
            "rolling_drawdown_pct": -2.0,
            "loss_streak": 1,
        },
    )
    assert result["risk_state"] == "halt"
    assert result["halt_new_entries"] is True
    assert result["position_cap_after_risk"] == 0
    assert "daily" in result["halt_reason"]


def test_risk_engine_drawdown_applies_hard_cap():
    result = evaluate_risk_controls(
        position_before_risk=80,
        context={
            "daily_pnl_pct": -1.0,
            "rolling_drawdown_pct": -9.1,
            "loss_streak": 2,
        },
    )
    assert result["risk_state"] == "cap"
    assert result["halt_new_entries"] is False
    assert result["position_cap_after_risk"] == 20
    assert "drawdown" in result["halt_reason"]


def test_risk_engine_loss_streak_reduces_position():
    result = evaluate_risk_controls(
        position_before_risk=80,
        context={
            "daily_pnl_pct": -1.0,
            "rolling_drawdown_pct": -2.0,
            "loss_streak": 4,
        },
    )
    assert result["risk_state"] == "cap"
    assert result["halt_new_entries"] is False
    assert result["position_cap_after_risk"] == 40
    assert "streak" in result["halt_reason"]
