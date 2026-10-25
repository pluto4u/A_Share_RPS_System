import numpy as np
import pandas as pd

from trend_start.pattern import a_trend_mask, phase_flags
from trend_start.signals import stock_signals


def _book():
    decline = np.linspace(100, 80, 40)
    base = np.full(40, 82.0)
    base[::2] = 83.0
    close = np.r_[decline, base, 88.0]
    open_ = close.copy()
    open_[-1] = 83.0
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    vol = np.r_[np.full(40, 1000.0), np.full(40, 400.0), 2000.0]
    return open_, high, low, close, vol


def test_pattern_accepts_decline_dry_base_and_right_side_volume():
    flags = phase_flags(*_book())
    assert flags["pattern"][-1]
    assert flags["decline"][-1] and flags["base"][-1] and flags["dry"][-1] and flags["right"][-1]
    assert not flags["pattern"][:-1].any()
    assert abs(flags["base_low"][-1] - 81.8) < 1e-9


def test_pattern_rejects_a_quiet_right_side_bar():
    o, h, lo, c, v = _book()
    v[-1] = 100
    assert not phase_flags(o, h, lo, c, v)["pattern"][-1]


def test_pattern_rejects_a_base_without_a_decline():
    o, h, lo, c, v = _book()
    c[:40] = 82
    o[:40] = 82
    h[:40] = 83
    lo[:40] = 81
    assert not phase_flags(o, h, lo, c, v)["decline"][-1]
    assert not phase_flags(o, h, lo, c, v)["pattern"][-1]


def test_pattern_does_not_read_the_next_bar():
    o, h, lo, c, v = _book()
    o = np.r_[o, 1.0]
    h = np.r_[h, 1.0]
    lo = np.r_[lo, 1.0]
    c = np.r_[c, 1.0]
    v = np.r_[v, 1.0]
    assert phase_flags(o, h, lo, c, v)["pattern"][-2]
    c[-1] = 500
    v[-1] = 9_000_000
    assert phase_flags(o, h, lo, c, v)["pattern"][-2]


def test_a_trend_mask_matches_the_daily_screen():
    rng = np.random.default_rng(0)
    n = 320
    c = 20 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    o = c * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * 1.01
    lo = np.minimum(o, c) * 0.99
    v = rng.integers(1_000, 5_000, n).astype(float)
    d = pd.DataFrame({
        "open": o, "high": h, "low": lo, "close": c, "vol": v, "amount": v * c,
        "RPS50": rng.uniform(0, 100, n),
        "RPS120": rng.uniform(0, 100, n),
        "RPS250": rng.uniform(0, 100, n),
        "RS": rng.uniform(0, 100, n),
    })
    got = a_trend_mask(c, v, d.RPS120.to_numpy(), d.RPS250.to_numpy())
    expect = stock_signals(d, []).A_trend.to_numpy()
    assert np.array_equal(got, expect)
