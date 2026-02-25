from dataclasses import dataclass

from sentiment_engine import drawdown_elastic_multiplier


@dataclass(frozen=True)
class RiskConfig:
    daily_stop_loss_pct: float = -2.5
    rolling_drawdown_limit_pct: float = -8.0
    drawdown_cap_position: int = 20
    loss_streak_reduce_threshold: int = 3
    loss_streak_halt_threshold: int = 5
    loss_streak_cap_ratio: float = 0.5
    enable_elastic_drawdown: bool = True


def evaluate_risk_controls(position_before_risk, context=None, config=None):
    """
    Four-layer risk controls (Layer 0 is new elastic drawdown).

    Parameters
    ----------
    position_before_risk : int | float
        Position cap before risk controls (0-100).
    context : dict | None
        Optional runtime/backtest context:
          - daily_pnl_pct: float
          - rolling_drawdown_pct: float
          - loss_streak: int
    config : RiskConfig | None
        Risk configuration. Defaults to RiskConfig().
    """
    cfg = config or RiskConfig()
    ctx = context or {}

    raw_position = int(max(0, min(100, round(float(position_before_risk)))))
    daily_pnl_pct = float(ctx.get("daily_pnl_pct", 0.0))
    rolling_drawdown_pct = float(ctx.get("rolling_drawdown_pct", 0.0))
    loss_streak = int(ctx.get("loss_streak", 0))

    flags = []
    halt_new_entries = False
    cap = raw_position
    reasons = []

    # Layer 0 (NEW): elastic drawdown — continuous position reduction
    elastic_mult = 1.0
    if cfg.enable_elastic_drawdown and rolling_drawdown_pct < 0:
        elastic_mult = drawdown_elastic_multiplier(rolling_drawdown_pct)
        if elastic_mult < 1.0:
            elastic_cap = int(raw_position * elastic_mult)
            cap = min(cap, max(elastic_cap, 5))  # keep at least 5% observation position
            flags.append("elastic_dd")
            reasons.append(f"dd={rolling_drawdown_pct:.1f}%->x{elastic_mult:.2f}")

    # Layer 1: portfolio hard stop
    if daily_pnl_pct <= cfg.daily_stop_loss_pct:
        flags.append("daily_stop")
        halt_new_entries = True
        cap = 0
        reasons.append(f"daily_loss<{cfg.daily_stop_loss_pct}%")

    # Layer 2: rolling drawdown hard cap (unchanged — emergency floor)
    if rolling_drawdown_pct <= cfg.rolling_drawdown_limit_pct:
        flags.append("drawdown_cap")
        cap = min(cap, int(cfg.drawdown_cap_position))
        reasons.append(f"drawdown<{cfg.rolling_drawdown_limit_pct}%")

    # Layer 3: strategy degradation by losing streak
    if loss_streak >= cfg.loss_streak_halt_threshold:
        flags.append("streak_halt")
        halt_new_entries = True
        cap = 0
        reasons.append(f"streak>={cfg.loss_streak_halt_threshold}")
    elif loss_streak >= cfg.loss_streak_reduce_threshold:
        flags.append("streak_reduce")
        cap = min(cap, int(raw_position * cfg.loss_streak_cap_ratio))
        reasons.append(f"streak>={cfg.loss_streak_reduce_threshold}")

    if halt_new_entries:
        risk_state = "halt"
    elif cap < raw_position:
        risk_state = "cap"
    else:
        risk_state = "normal"

    return {
        "risk_state": risk_state,
        "halt_new_entries": halt_new_entries,
        "position_cap_after_risk": int(max(0, min(100, cap))),
        "halt_reason": ";".join(reasons),
        "flags": flags,
        "elastic_multiplier": round(elastic_mult, 3),
    }
