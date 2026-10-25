"""Offline unit tests for trend_start.ice_opt — synthetic data, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
from trend_start.ice_opt import IceGateParams, ice_gate_mask, simulate_exit_variants


def sent_frame(adv, streak, zt, zt_med, ratio):
    m = len(adv)
    return pd.DataFrame({
        "advance_share": adv, "max_streak": streak, "zt_count": zt,
        "zt_median60": zt_med, "amount_ratio": ratio,
    }, index=pd.date_range("2024-01-01", periods=m, freq="B"))


def test_gate_grid_dimensions() -> None:
    # day0 0.30(弱) day1 0.15(冰) day2 0.15(连冰) day3 0.15(连冰) day4 0.6
    f = sent_frame([0.30, 0.15, 0.15, 0.15, 0.60],
                   [2, 2, 2, 4, 5], [20, 15, 15, 60, 80],
                   [60, 60, 60, 60, 60], [0.7, 0.7, 0.7, 1.0, 1.1])
    m1 = ice_gate_mask(f, IceGateParams(advance_thr=0.22, min_streak=1))
    assert m1.tolist() == [False, True, True, True, False]
    m2 = ice_gate_mask(f, IceGateParams(advance_thr=0.22, min_streak=2))
    assert m2.tolist() == [False, False, True, True, False]
    m3 = ice_gate_mask(f, IceGateParams(advance_thr=0.22, min_streak=1,
                                        require_weakening=False))
    assert m3[1] and m1[1]  # day1 passes either way (prev 0.30 <= 0.35)
    f2 = sent_frame([0.60, 0.15, 0.15, 0.60, 0.60],
                    [5, 2, 2, 5, 5], [80, 15, 15, 80, 80],
                    [60, 60, 60, 60, 60], [1.1, 0.7, 0.7, 1.1, 1.1])
    g1 = ice_gate_mask(f2, IceGateParams(advance_thr=0.22, min_streak=1,
                                         require_weakening=True))
    g2 = ice_gate_mask(f2, IceGateParams(advance_thr=0.22, min_streak=1,
                                         require_weakening=False))
    assert not g1[1] and g2[1]      # 由强转弱被 require_weakening 拦下
    g3 = ice_gate_mask(f, IceGateParams(advance_thr=0.22, min_streak=1,
                                        resonance="money"))
    assert g3[1] and not g3[3]      # day3 ratio=1.0 不满足资金面
    g4 = ice_gate_mask(f, IceGateParams(advance_thr=0.22, min_streak=1,
                                        resonance="sentiment"))
    assert g4[1] and not g4[3]      # day3 涨停 60 不 <= 中位一半


def _bars(n: int = 15):
    dates = pd.date_range("2024-01-01", periods=n, freq="B").to_numpy().astype("datetime64[D]")
    cal = np.arange(n)
    c = np.linspace(10, 11, n)
    return dates, cal, c - 0.02, c + 0.05, c - 0.05, c


def test_fixed_and_invalidation_exits() -> None:
    dates, cal, o, h, lo, c = _bars()
    none = np.zeros(len(dates), dtype=bool)
    out = simulate_exit_variants("600000.SH", 2, pivot=10.0, floor=9.0,
                                 o=o, h=h, lo=lo, c=c, cal=cal, dates=dates,
                                 repair_days=none, climax_days=none)
    assert out["fixed3"][0] == 5 and out["fixed3"][2] == "time_stop"
    assert out["invalidation"][2] == "unfinished"  # never breaks floor 9.0


def test_target_exit_triggers_on_rally() -> None:
    dates, cal, o, h, lo, c = _bars()
    c = c.copy()
    c[4] = c[2] * 1.06  # +6% vs entry close -> target5 hit at bar 4
    h = np.maximum(h, c)
    none = np.zeros(len(dates), dtype=bool)
    out = simulate_exit_variants("600000.SH", 2, pivot=10.0, floor=9.0,
                                 o=o, h=h, lo=lo, c=c, cal=cal, dates=dates,
                                 repair_days=none, climax_days=none)
    assert out["target5"][0] == 4 and out["target5"][2] == "target"
    assert out["target8"][2] != "target"


def test_repair_exit_waits_for_repair_day() -> None:
    dates, cal, o, h, lo, c = _bars()
    repair = np.zeros(len(dates), dtype=bool)
    repair[6] = True
    none = np.zeros(len(dates), dtype=bool)
    out = simulate_exit_variants("600000.SH", 2, pivot=10.0, floor=9.0,
                                 o=o, h=h, lo=lo, c=c, cal=cal, dates=dates,
                                 repair_days=repair, climax_days=none)
    assert out["repair_day"][0] == 6 and out["repair_day"][2] == "repair_day"
    assert out["climax_day"][2] == "time_stop"


def test_trail_hard_stop_sells_next_open() -> None:
    from trend_start.ice_opt import simulate_trail_exit
    dates, cal, o, h, lo, c = _bars()
    c = c.copy()
    c[5] = c[2] * 0.90                      # -10% vs entry -> hard stop at bar5
    lo = np.minimum(lo, c)
    bar, px, reason, at_close = simulate_trail_exit(
        "600000.SH", 2, o=o, h=h, lo=lo, c=c, dates=dates,
        hard_stop=0.04, trail_pct=0.08, max_hold=20)
    assert bar == 6 and reason == "hard_stop" and not at_close
    assert abs(px - o[6]) < 1e-9


def test_trail_exit_triggers_after_peak_pullback() -> None:
    from trend_start.ice_opt import simulate_trail_exit
    dates, cal, o, h, lo, c = _bars()
    c = c.copy()
    c[5], c[6], c[7], c[8] = 11.6, 11.3, 11.2, 11.6 * 0.90  # peak 11.6 -> -10%
    h = np.maximum(h, c)
    bar, px, reason, at_close = simulate_trail_exit(
        "600000.SH", 2, o=o, h=h, lo=lo, c=c, dates=dates,
        hard_stop=0.04, trail_pct=0.08, max_hold=20)
    assert bar == 9 and reason == "trail" and not at_close
    assert abs(px - o[9]) < 1e-9


def test_trail_time_stop_when_no_trigger() -> None:
    from trend_start.ice_opt import simulate_trail_exit
    dates, cal, o, h, lo, c = _bars()      # monotone rise, never pulls back 8%
    bar, px, reason, at_close = simulate_trail_exit(
        "600000.SH", 2, o=o, h=h, lo=lo, c=c, dates=dates,
        hard_stop=0.04, trail_pct=0.08, max_hold=4)
    assert bar == 6 and reason == "time_stop" and at_close
    assert abs(px - c[6]) < 1e-9


def test_trail_hard_stop_rolls_over_locked_day() -> None:
    from trend_start.ice_opt import simulate_trail_exit
    dates, cal, o, h, lo, c = _bars()
    c = c.copy()
    c[5] = c[2] * 0.90                      # hard stop triggers at bar5
    c[6] = c[5] * 0.90                      # bar6 limit-down
    o[6] = c[6]
    h[6] = c[6]
    lo[6] = c[6]                            # one-price locked bar
    lo = np.minimum(lo, c)
    bar, px, reason, at_close = simulate_trail_exit(
        "600000.SH", 2, o=o, h=h, lo=lo, c=c, dates=dates,
        hard_stop=0.04, trail_pct=0.08, max_hold=20)
    assert bar == 7 and reason == "hard_stop"      # rolled past locked bar6
    assert abs(px - o[7]) < 1e-9
