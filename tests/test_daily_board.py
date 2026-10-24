import unittest

import numpy as np
import pandas as pd

from trend_start.daily_board import (CHASE_AMOUNT, CHASE_STREAK, CHASE_ZT, OVERHEAT, engine_days,
                                     leader_ranking, whitelist_tag)
from trend_start.panel import FIELDS, Panel


def make_panel(n=60, m=300, seed=0, drift=None):
    rng = np.random.default_rng(seed)
    drift = np.zeros(n) if drift is None else drift
    r = rng.normal(0, .02, (n, m)) + drift[:, None]
    c = 10 * np.cumprod(1 + r, axis=1)
    data = {"close": c, "open": c / (1 + r / 2), "high": c * 1.01, "low": c * .99,
            "vol": rng.uniform(1e5, 1e6, (n, m)), "amount": rng.uniform(1e7, 1e8, (n, m))}
    codes = np.array([f"{600000 + i}.SH" for i in range(n)])
    dates = pd.bdate_range("2025-01-01", periods=m).to_numpy().astype("datetime64[D]")
    return Panel(dates=dates, codes=codes, data={f: data[f].astype(np.float32) for f in FIELDS},
                 index_close=3000 * np.cumprod(1 + r.mean(axis=0)))


class EngineDaysTests(unittest.TestCase):
    def test_engine_flags_follow_frozen_definitions(self):
        d = engine_days(make_panel())
        self.assertFalse((d.ice_engine_on & ~d.ice_gate).any())
        self.assertFalse((d.ice_engine_on & (d.advance_share >= OVERHEAT)).any())
        chase = d.chase_engine_on
        self.assertFalse((chase & ~d.repair).any())
        self.assertFalse((chase & ((d.amount_vs_prev5 < CHASE_AMOUNT) | (d.zt_count < CHASE_ZT)
                                   | (d.max_streak <= CHASE_STREAK))).any())

    def test_engine_days_are_causal(self):
        p = make_panel()
        cut = Panel(dates=p.dates[:200], codes=p.codes, data={f: v[:, :200] for f, v in p.data.items()},
                    index_close=p.index_close[:200])
        cols = ["advance_share", "zt_count", "ice_level", "repair", "ice_engine_on", "chase_engine_on"]
        pd.testing.assert_frame_equal(engine_days(p)[cols].iloc[:200], engine_days(cut)[cols])


class LeaderTests(unittest.TestCase):
    def test_zone_requires_strength_and_excludes_st(self):
        drift = np.r_[np.full(40, .004), np.full(20, -.002)]
        p = make_panel(drift=drift)
        st = np.zeros(60, bool); st[0] = True
        out = leader_ranking(p, st)
        self.assertNotIn("600000.SH", set(out.code))
        self.assertTrue((out.rps120 >= 90).all())
        self.assertTrue((out.near_high >= .70).all())
        self.assertTrue(out.score.is_monotonic_decreasing)
        self.assertGreater(out.attrs["zone_size"], 0)


class WhitelistTests(unittest.TestCase):
    def test_prefix_membership_and_longest_quadrant(self):
        sectors = pd.DataFrame({"name": ["钢铁", "特钢", "建材"], "quadrant_cn": ["主升", "启动", "退潮"]},
                               index=["T0401", "T040102", "T0601"])
        ind = pd.Series(["T040102", "T040101", "T060101", None, "T9999"])
        tags = whitelist_tag(ind, sectors, {"T0401"})
        self.assertEqual(list(tags.in_whitelist), [True, True, False, False, False])
        self.assertEqual(list(tags.quadrant), ["启动", "主升", "退潮", None, None])


if __name__ == "__main__":
    unittest.main()
