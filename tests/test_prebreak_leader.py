import pandas as pd

from rps_analyzer.prebreak_leader import _score


def _row(**overrides) -> pd.Series:
    values = {
        "gap_to_reference_pct": -2.0,
        "close_vs_ma20_pct": 2.0,
        "ma5_above_ma20_pct": 1.0,
        "ma20_slope5_pct": 1.0,
        "ma5_slope3_pct": 2.5,
        "rps5": 92.0,
        "rps20": 94.0,
        "rps50": 88.0,
        "vol_contraction3": 0.80,
        "up_down_vol_ratio10": 1.50,
        "close_location5": 0.85,
        "july_max_drawdown_pct": 35.0,
        "ret5_pct": 8.0,
        "range5_pct": 8.0,
        "atr14_pct": 4.0,
        "setup_type": "回踩再突破",
        "ret1_pct": 2.0,
    }
    values.update(overrides)
    return pd.Series(values)


def test_leader_score_rewards_near_line_strength_and_contraction():
    strong = _score(_row())
    weak = _score(
        _row(
            gap_to_reference_pct=-7.0,
            ma5_above_ma20_pct=-3.0,
            ma20_slope5_pct=-2.5,
            rps5=60.0,
            rps20=66.0,
            rps50=35.0,
            vol_contraction3=1.4,
            up_down_vol_ratio10=0.7,
            close_location5=0.45,
            july_max_drawdown_pct=52.0,
            ret5_pct=14.0,
            range5_pct=15.0,
            atr14_pct=8.0,
            setup_type="线边突破确认",
            ret1_pct=6.0,
        )
    )

    assert strong >= 90.0
    assert strong > weak + 30.0
