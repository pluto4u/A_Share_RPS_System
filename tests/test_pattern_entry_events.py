"""Equivalence of the slim pattern_entry_events scan against pattern_events."""

from __future__ import annotations

import numpy as np
import pandas as pd
from tests.test_drop_base_retest import sample
from trend_start.drop_base_retest import PatternConfig, pattern_entry_events, pattern_events


def flags_from_frame(e: pd.DataFrame) -> list[tuple[int, str, float, float]]:
    out: list[tuple[int, str, float, float]] = []
    for t, row in e.iterrows():
        for flag, kind in (("breakout", "breakout"), ("quiet_touch", "quiet"),
                           ("confirmed", "confirmed"), ("loud_touch", "loud")):
            if bool(row[flag]):
                out.append((int(t), kind, float(row["pivot"]), float(row.floor)))
    return out


def check_equivalence(d: pd.DataFrame) -> None:
    cfg = PatternConfig()
    assert pattern_entry_events(d, cfg) == flags_from_frame(pattern_events(d, cfg))


def test_equivalence_on_canonical_sample() -> None:
    check_equivalence(sample())


def test_equivalence_on_random_walks() -> None:
    rng = np.random.default_rng(11)
    for _ in range(20):
        n = 400
        ret = rng.normal(0, 0.02, n)
        c = 20 * np.exp(np.cumsum(ret))
        o = c * (1 + rng.normal(0, 0.005, n))
        h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.008, n)))
        lo = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.008, n)))
        v = rng.uniform(50, 200, n) * (1 + 3 * (abs(ret) > 0.03))
        d = pd.DataFrame({"open": o, "close": c, "high": h, "low": lo,
                          "vol": v, "amount": np.full(n, 2e8)})
        check_equivalence(d)


def test_equivalence_on_failure_and_expiry_paths() -> None:
    d = sample()
    d.loc[78, ["open", "close", "high", "low"]] = [9, 9, 9.1, 8.9]  # break the base
    check_equivalence(d)
    d2 = sample()
    d2.loc[76:, ["open", "close", "high", "low"]] = \
        np.tile([10.4, 10.4, 10.45, 10.35], (len(d2) - 76, 1))  # drift past event_days
    d2.loc[76:, "vol"] = 40
    check_equivalence(d2)
