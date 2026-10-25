"""Offline unit tests for trend_start.icepoint — synthetic panels, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
from trend_start.icepoint import (
    IceConfig,
    classify_ice,
    daily_sentiment,
    limit_matrix,
)


def tiny_panel(n: int = 8, m: int = 70) -> dict:
    codes = np.array([f"60000{i}.SH" for i in range(n)])
    dates = pd.date_range("2024-01-01", periods=m, freq="B").to_numpy().astype("datetime64[D]")
    rng = np.random.default_rng(3)
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, (n, m)), axis=1))
    o = c * 0.995
    h = c * 1.01
    amount = np.full((n, m), 2e8)
    return {"codes": codes, "dates": dates, "o": o, "h": h, "c": c, "amount": amount}


def test_limit_matrix_boards() -> None:
    codes = np.array(["600000.SH", "300001.SZ", "688001.SH"])
    dates = pd.date_range("2020-08-20", periods=10, freq="B").to_numpy().astype("datetime64[D]")
    lim = limit_matrix(codes, dates)
    assert lim[0, 0] == 0.10
    assert lim[1, 0] == 0.10 and lim[1, -1] == 0.20   # GEM reform 2020-08-24
    assert lim[2, 0] == 0.20


def test_sentiment_indicators_on_known_day() -> None:
    p = tiny_panel()
    c = p["c"].copy()
    h = p["h"].copy()
    t = 30
    # day t: stock 0 sealed limit-up (+10%), stock 1 touched but broke, 2 down limit, rest up 1%
    c[0, t] = c[0, t - 1] * 1.10
    h[0, t] = c[0, t]
    c[1, t] = c[1, t - 1] * 1.05
    h[1, t] = c[1, t - 1] * 1.10   # touched but not sealed -> broken board
    c[2, t] = c[2, t - 1] * 0.90
    for i in range(3, 8):
        c[i, t] = c[i, t - 1] * 1.01
        h[i, t] = np.maximum(h[i, t], c[i, t])
    h[2, t] = np.maximum(h[2, t - 1], c[2, t])
    sent = daily_sentiment(p["codes"], p["dates"], p["o"], h, c, p["amount"],
                           IceConfig(breadth_ma=5, zt_baseline_window=10))
    assert sent.zt_count.iloc[t] == 1
    assert sent.dt_count.iloc[t] == 1
    assert abs(sent.broken_rate.iloc[t] - 0.5) < 1e-9       # 2 touched, 1 sealed
    assert abs(sent.advance_share.iloc[t] - 7 / 8) < 1e-9   # +5% + 5×1% + limit-up
    # streak: stock 0 sealed again next day -> height 2
    c[0, t + 1] = c[0, t] * 1.10
    h[0, t + 1] = c[0, t + 1]
    sent2 = daily_sentiment(p["codes"], p["dates"], p["o"], h, c, p["amount"],
                            IceConfig(breadth_ma=5, zt_baseline_window=10))
    assert sent2.max_streak.iloc[t + 1] == 2


def sent_frame(adv: list[float], zt: list[float], dt: list[float],
               streak: list[float], amount_ratio: list[float],
               total: list[float], breadth: list[float]) -> pd.DataFrame:
    m = len(adv)
    return pd.DataFrame({
        "advance_share": adv, "decline_share": [1 - a for a in adv],
        "zt_count": zt, "dt_count": dt, "broken_rate": [0.5] * m,
        "max_streak": streak, "premium": [0.0] * m, "total_amount": total,
        "amount_ratio": amount_ratio, "prev5_amount": [np.nan] + total[:-1],
        "breadth": breadth, "zt_median60": [60.0] * m,
    }, index=pd.date_range("2024-01-01", periods=m, freq="B"))


def test_ice_levels_and_weakening_path() -> None:
    # day1 低位(0.3) -> day2 市场冰点(0.15)且由弱转更弱, 情绪+资金双低 -> L3
    # day3 从强(0.6)直接打到冰点(0.15) -> 只能 L1
    f = sent_frame(
        adv=[0.5, 0.30, 0.15, 0.60, 0.15],
        zt=[50, 20, 15, 80, 15],
        dt=[5, 10, 30, 2, 30],
        streak=[4, 2, 2, 5, 2],
        amount_ratio=[1.0, 0.7, 0.7, 1.3, 0.7],
        total=[100, 80, 80, 200, 80],
        breadth=[0.6, 0.3, 0.15, 0.65, 0.15],
    )
    ice = classify_ice(f)
    lv = ice.ice_level.to_numpy()
    assert lv[2] == 3        # 市场+情绪+资金三冰点, 且前日已弱
    assert lv[4] == 1        # 从强转弱 -> 封顶 L1
    assert not ice.weakening_path.iloc[4]


def test_repair_and_reversal_resonance() -> None:
    # day1 冰点(L1 以上), day2 强修复+放量共振 -> reversal; day4 修复但不放量 -> rebound_only
    f = sent_frame(
        adv=[0.30, 0.15, 0.55, 0.15, 0.50],
        zt=[20, 15, 40, 12, 30],
        dt=[10, 30, 4, 25, 8],
        streak=[2, 2, 4, 2, 2],
        amount_ratio=[0.7, 0.7, 1.3, 0.7, 0.9],
        total=[80, 80, 200, 75, 80],
        breadth=[0.3, 0.15, 0.60, 0.14, 0.45],
    )
    ice = classify_ice(f, index_close=np.full(5, 100.0))
    assert ice.ice_level.iloc[1] >= 1
    assert ice.repair.iloc[2] and ice.reversal.iloc[2]
    assert ice.ice_level.iloc[3] >= 1
    assert ice.repair.iloc[4] and ice.rebound_only.iloc[4] and not ice.reversal.iloc[4]


def test_no_ice_no_repair() -> None:
    f = sent_frame(
        adv=[0.6, 0.55, 0.6], zt=[50, 60, 70], dt=[3, 2, 2],
        streak=[4, 5, 5], amount_ratio=[1.0, 1.1, 1.2],
        total=[100, 110, 120], breadth=[0.6, 0.6, 0.7],
    )
    ice = classify_ice(f)
    assert ice.ice_level.sum() == 0
    assert not ice.repair.any()
