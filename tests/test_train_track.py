"""Offline unit tests for trend_start.train_track — synthetic data only."""

from __future__ import annotations

import numpy as np
import pandas as pd
from trend_start.train_track import train_track_signals


def mk_frame(series: list[np.ndarray]) -> tuple[pd.DataFrame, ...]:
    c = pd.DataFrame(np.vstack(series))
    o = c - 0.01
    h = c + 0.02
    lo = c - 0.02
    return o, h, lo, c


def uptrend(n: int = 300) -> np.ndarray:
    """Steady climber: log-linear rise with tiny noise -> strong RPS, tight range."""
    x = np.arange(n)
    return 10 * np.exp(0.002 * x) + 0.005 * np.sin(x / 3)


def test_uptrend_stock_fires_laggard_does_not() -> None:
    n = 300
    flat = np.full(n, 10.0)                      # never trends
    down = 10 * np.exp(-0.002 * np.arange(n))    # persistent decline
    o, h, lo, c = mk_frame([uptrend(n), flat, down])
    sig = train_track_signals(o, h, lo, c)
    assert sig.iloc[0].sum() >= 1          # the climber gets signal(s)
    assert sig.iloc[1].sum() == 0          # flat stock: no trend, no signal
    assert sig.iloc[2].sum() == 0          # decliner: never


def test_dedup_first_within_8_bars() -> None:
    n = 300
    o, h, lo, c = mk_frame([uptrend(n), np.full(n, 5.0)])
    sig = train_track_signals(o, h, lo, c).iloc[0].to_numpy()
    idx = np.flatnonzero(sig)
    if len(idx) >= 2:
        assert np.all(np.diff(idx) > 8)    # consecutive signals > 8 bars apart


def test_stricter_rps_threshold_reduces_signals() -> None:
    n = 300
    o, h, lo, c = mk_frame([uptrend(n), 10 * np.exp(0.001 * np.arange(n))])
    loose = train_track_signals(o, h, lo, c, rps_sum_min=150).to_numpy().sum()
    strict = train_track_signals(o, h, lo, c, rps_sum_min=195).to_numpy().sum()
    assert strict <= loose
