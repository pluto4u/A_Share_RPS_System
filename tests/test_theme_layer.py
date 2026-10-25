import unittest

import numpy as np
import pandas as pd

from trend_start.theme_layer import concept_strength, ladder, latest_reason, reason_heat, split_reasons, strong_stocks


class ConceptStrengthTests(unittest.TestCase):
    def test_returns_and_ranking(self):
        idx = pd.bdate_range("2026-09-01", periods=21)
        closes = pd.DataFrame({"A": np.linspace(100, 120, 21), "B": np.linspace(100, 90, 21),
                               "C": [np.nan] + list(np.linspace(100, 110, 20))}, index=idx)
        out = concept_strength(closes, {"A": "甲", "B": "乙"})
        self.assertEqual(list(out.name), ["甲", "乙"])          # C lacks a 20-session return
        self.assertAlmostEqual(out.ret20.iloc[0], 20.0)
        self.assertAlmostEqual(out.ret5.iloc[0], (120 / 115 - 1) * 100)
        self.assertEqual(list(out.rank5), [1, 2])

    def test_too_short_history_is_empty(self):
        self.assertTrue(concept_strength(pd.DataFrame({"A": [1.0, 2.0]}), {}).empty)


class ReasonTests(unittest.TestCase):
    pools = {"2026-10-22": [{"thscode": "1", "name": "甲", "limit_up_reason": "人形机器人+军工", "continue_day_cnt": 1}],
             "2026-10-23": [{"thscode": "1", "name": "甲", "limit_up_reason": "人形机器人＋减速器", "continue_day_cnt": 2,
                             "limit_up_time": "09:31"},
                            {"thscode": "2", "name": "乙", "limit_up_reason": "AI应用", "continue_day_cnt": 1,
                             "limit_up_time": "10:00"}]}

    def test_split_handles_fullwidth_plus(self):
        self.assertEqual(split_reasons("人形机器人＋减速器+ "), ["人形机器人", "减速器"])

    def test_heat_counts_today_window_and_days(self):
        h = reason_heat(self.pools, "2026-10-23").set_index("keyword")
        self.assertEqual(h.at["人形机器人", "today"], 1)
        self.assertEqual(h.at["人形机器人", "window"], 2)
        self.assertEqual(h.at["人形机器人", "days"], 2)
        self.assertEqual(h.at["军工", "today"], 0)
        self.assertEqual(h.index[0], "人形机器人")

    def test_ladder_and_latest_reason(self):
        lad = ladder(self.pools["2026-10-23"])
        self.assertEqual(list(lad.name), ["甲"])
        self.assertEqual(latest_reason(self.pools)["1"], ("2026-10-23", "人形机器人＋减速器"))


class StrongStockTests(unittest.TestCase):
    def test_top_gainers_respect_liquidity_and_exclusion(self):
        close = np.vstack([np.linspace(10, 20, 12), np.linspace(10, 15, 12), np.linspace(10, 30, 12)])
        amount = np.vstack([np.full(12, 2e8), np.full(12, 2e8), np.full(12, 1e7)])
        out = strong_stocks(np.array(["A", "B", "C"]), close, amount, np.array([False, False, False]))
        self.assertEqual(list(out.code), ["A", "B"])             # C fails the 1e8 amount floor
        out = strong_stocks(np.array(["A", "B", "C"]), close, amount, np.array([True, False, False]))
        self.assertEqual(list(out.code), ["B"])


if __name__ == "__main__":
    unittest.main()
