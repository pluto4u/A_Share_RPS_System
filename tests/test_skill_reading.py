import unittest

import numpy as np

from trend_start.skill_reading import Bars, classify_type, judge_trend, read_520, read_smart_money, sma


def bars(close, vol=None, limit=0.10, spread=0.01):
    c = np.asarray(close, float)
    v = np.full(len(c), 1e6) if vol is None else np.asarray(vol, float)
    return Bars(o=c, h=c * (1 + spread), l=c * (1 - spread), c=c, v=v, limit=limit)


class SkillReadingTests(unittest.TestCase):
    def test_sma_matches_numpy(self):
        a = np.arange(1.0, 30.0)
        np.testing.assert_allclose(sma(a, 5)[4:], np.convolve(a, np.ones(5) / 5, "valid"))

    def test_steady_uptrend_reads_as_multi_head_high_confidence(self):
        c = 10 * np.cumprod(np.full(300, 1.004))
        v = np.where(np.arange(300) % 2 == 0, 1.6e6, 1.0e6)  # 上涨日放量
        r = judge_trend(bars(c, v))
        self.assertEqual(r["ma_state"], "多头排列")
        self.assertEqual(r["direction"], "上升")
        self.assertIn(r["confidence"], ("中", "高"))

    def test_downtrend_is_not_applicable_for_520(self):
        c = 30 * np.cumprod(np.full(300, 0.996))
        b = bars(c)
        t = judge_trend(b)
        self.assertEqual(t["direction"], "下降")
        self.assertEqual(read_520(b, t["direction"])["state"], "不适用")

    def test_short_history_degrades(self):
        self.assertEqual(judge_trend(bars(10 * np.cumprod(np.full(120, 1.001))))["direction"], "数据不足")

    def test_two_closes_below_ma20_is_effective_break(self):
        c = list(10 * np.cumprod(np.full(280, 1.003)))
        c += [c[-1] * 0.93, c[-1] * 0.90]
        self.assertEqual(judge_trend(bars(c))["ma20_state"], "有效跌破20日线")

    def test_golden_cross_detected_with_volume_enhancer(self):
        c = list(np.linspace(30, 20, 260)) + list(np.linspace(20, 21, 20)) + [21.8, 22.8, 24.0, 25.0, 26.0]
        v = [1e6] * (len(c) - 1) + [3e6]
        b = bars(c, v)
        r = read_520(b, "震荡")
        self.assertTrue(r["volume_confirm"])
        self.assertIn(r["strength"], ("中", "强"))

    def test_bear_regime_sharp_rise_is_flagged_as_bull_trap_risk(self):
        c = list(np.linspace(30, 20, 290)) + [20.0] * 9 + [21.2]
        v = [1e6] * 299 + [2.5e6]
        self.assertIn("诱多", read_smart_money(bars(c, v), "熊市恐慌")["label"])
        self.assertNotIn("诱多", read_smart_money(bars(c, v), "主升行情")["label"])

    def test_high_volume_stall_near_high_is_distribution_risk(self):
        c = list(np.linspace(10, 30, 300)) + [30.0]
        v = [1e6] * 300 + [2.5e6]
        self.assertIn("出货", read_smart_money(bars(c, v), "主升行情")["label"])

    def test_classifier_votes(self):
        calm = classify_type(bars(10 * np.cumprod(np.full(300, 1.001))), theme_hot=False)
        self.assertEqual(calm["type"], "偏趋势")
        c = list(10 * np.cumprod(np.full(280, 1.001)))
        for _ in range(3):
            c.append(c[-1] * 1.0999)
        hot = classify_type(bars(c, spread=0.06), theme_hot=True)
        self.assertEqual(hot["type"], "偏情绪")
        self.assertIn("换手率", hot["unavailable"])


if __name__ == "__main__":
    unittest.main()
