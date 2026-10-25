import unittest

import numpy as np
import pandas as pd

from trend_start.engine import BUY_COST, sell_cost
from trend_start.forward_log import allocate, pool_members, sessions_to_process, settle
from trend_start.panel import FIELDS, Panel


def panel_from(closes, highs=None, lows=None, code="600000.SH"):
    m = len(closes)
    c = np.array([closes], dtype=np.float32)
    h = np.array([highs or closes], dtype=np.float32)
    lo = np.array([lows or closes], dtype=np.float32)
    data = {"open": c.copy(), "high": h, "low": lo, "close": c, "vol": np.ones_like(c), "amount": np.ones_like(c)}
    dates = pd.bdate_range("2026-10-26", periods=m).to_numpy().astype("datetime64[D]")
    return Panel(dates=dates, codes=np.array([code]), data={f: data[f] for f in FIELDS}, index_close=np.ones(m))


def trade(engine="pool_ice", signal="2026-10-26"):
    return pd.DataFrame([{"trade_id": "t1", "engine": engine, "code": "600000.SH", "signal_date": signal}])


class SessionTests(unittest.TestCase):
    def test_only_after_freeze_and_last_done(self):
        cal = pd.bdate_range("2026-10-20", periods=10)
        got = sessions_to_process(cal, pd.Timestamp("2026-10-25"), None)
        self.assertEqual(got[0], pd.Timestamp("2026-10-26"))
        got = sessions_to_process(cal, pd.Timestamp("2026-10-25"), pd.Timestamp("2026-10-28"))
        self.assertEqual(got[0], pd.Timestamp("2026-10-29"))
        self.assertEqual(sessions_to_process(cal, pd.Timestamp("2026-10-25"), pd.Timestamp("2026-10-15"))[0],
                         pd.Timestamp("2026-10-26"))

    def test_pool_excludes_circle_day_and_expires(self):
        circles = np.zeros((3, 100), bool)
        circles[0, 99] = True   # today: not yet in pool
        circles[1, 98] = True   # yesterday: in pool
        circles[2, 39] = True   # 60 sessions ago -> t-60 = 39 still inside [t-60, t-1]
        self.assertEqual(list(pool_members(circles, 99)), [False, True, True])
        circles[2, 39] = False; circles[2, 38] = True
        self.assertFalse(pool_members(circles, 99)[2])


class AllocateTests(unittest.TestCase):
    def test_priority_capacity_and_held(self):
        cand = pd.DataFrame({"engine": ["chase", "pool_ice", "pattern_ice", "pool_ice"], "code": ["A", "C", "B", "A"],
                             "prio": [2, 0, 1, 0]})
        got = allocate(cand, held={"C"}, free=2)
        self.assertEqual(list(got.code), ["A", "B"])
        self.assertEqual(list(got.engine), ["pool_ice", "pattern_ice"])
        self.assertTrue(allocate(cand, set(), 0).empty)


class SettleTests(unittest.TestCase):
    def test_fixed5_close_with_costs(self):
        p = panel_from([10, 10.5, 10.4, 10.6, 10.8, 11.0, 11.2])
        out = settle(trade(), p).iloc[0]
        self.assertEqual(out.held_bars, 5)
        exp = 11.0 * (1 - sell_cost(p.dates)[5]) / (10 * (1 + BUY_COST)) - 1
        self.assertAlmostEqual(out.ret, exp, places=6)

    def test_open_until_horizon_available(self):
        self.assertTrue(settle(trade(), panel_from([10, 10.1, 10.2])).empty)

    def test_one_price_limit_down_rolls_to_next_open(self):
        closes = [10, 10, 10, 10, 10, 9.0, 9.2]
        highs = [10, 10, 10, 10, 10, 9.0, 9.4]
        lows = [10, 10, 10, 10, 10, 9.0, 8.9]
        out = settle(trade(), panel_from(closes, highs, lows)).iloc[0]
        self.assertEqual(out.held_bars, 6)  # bar 5 locked at -10% -> next bar's open


if __name__ == "__main__":
    unittest.main()
