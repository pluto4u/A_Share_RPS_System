"""Offline unit tests for trend_start.regime — synthetic data only, no network/LLM."""

from __future__ import annotations

import numpy as np
from trend_start.regime import (
    GATE_ICE_BUY,
    GATE_NEUTRAL,
    GATE_OFF,
    GATE_SURGE_CHASE,
    RegimeConfig,
    classify,
    ice_streaks,
    volume_regime,
)


def test_volume_regime_shrink_neutral_surge() -> None:
    cfg = RegimeConfig(vol_short=3, vol_long=6)
    amount = np.r_[np.full(6, 100.0), np.full(3, 50.0), np.full(3, 200.0)]
    regime, ratio = volume_regime(amount, cfg)
    assert regime[5] == "中性"          # 1.0 ratio
    assert regime[8] == "缩量"          # 50 vs 100 -> 0.5
    assert regime[11] == "放量"         # 200 vs ~75 -> >1.2
    assert np.isfinite(ratio[8])


def test_ice_streak_requires_consecutive_days() -> None:
    cfg = RegimeConfig(ice_breadth=0.2, ice_min_streak=2, ice_window=3)
    # single isolated ice day: no 连续冰点
    breadth = np.array([0.5, 0.1, 0.5, 0.1, 0.1, 0.5, 0.5, 0.5])
    ice_day, recent = ice_streaks(breadth, cfg)
    assert ice_day[1] and ice_day[3] and ice_day[4]
    assert not recent[2]                # isolated ice day does not open window
    assert recent[4]                    # streak of 2 opens the window
    assert recent[6]                    # window lasts ice_window days
    assert not recent[7]                # then closes


def test_gate_mapping_follows_user_rules() -> None:
    cfg = RegimeConfig(vol_short=2, vol_long=4, ice_breadth=0.2,
                       ice_min_streak=2, ice_window=1)
    # 8 sessions: neutral -> shrink(no ice) -> shrink+ice streak -> shrink(window closed) -> surge
    amount = np.array([100, 100, 100, 30, 30, 30, 300, 300.0])
    breadth = np.array([0.5, 0.5, 0.5, 0.1, 0.1, 0.5, 0.6, 0.6])
    frame = classify(breadth, amount, cfg)
    g = frame["gate"].to_numpy()
    assert g[0] == GATE_NEUTRAL
    assert g[3] == GATE_OFF             # 缩量, streak only 1 -> no window
    assert g[4] == GATE_ICE_BUY         # 缩量 + streak of 2 ice days
    assert g[5] == GATE_OFF             # 缩量 but window closed (ice_window=1)
    assert g[7] == GATE_SURGE_CHASE     # 放量
    assert frame["position_mult"].to_numpy()[5] == 0.0
    assert frame["position_mult"].to_numpy()[0] == cfg.mult_neutral


def test_classification_is_causal() -> None:
    cfg = RegimeConfig(vol_short=2, vol_long=4)
    amount = np.array([100, 100, 40, 40, 40, 40, 300, 300.0])
    breadth = np.array([0.5, 0.5, 0.1, 0.1, 0.5, 0.5, 0.6, 0.6])
    full = classify(breadth, amount, cfg)
    for cut in (3, 5, 7):
        part = classify(breadth[:cut], amount[:cut], cfg)
        assert (part["gate"].to_numpy() == full["gate"].to_numpy()[:cut]).all()
