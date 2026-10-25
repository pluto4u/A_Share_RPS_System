import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import run_daily_review as rdr
import xhs_daily_post as xhs

TZ = ZoneInfo("Asia/Shanghai")


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


class DecideTests(unittest.TestCase):
    latest = date(2026, 10, 23)  # Friday

    def test_manual_run_uses_latest_session(self):
        a, t, _ = rdr.decide(at(2026, 10, 25, 10, 0), self.latest, False, None)
        self.assertEqual((a, t), ("run", self.latest))

    def test_weekend_is_skipped_for_scheduled_run(self):
        self.assertEqual(rdr.decide(at(2026, 10, 25, 17, 30), self.latest, True, None)[0], "skip")

    def test_before_close_waits(self):
        self.assertEqual(rdr.decide(at(2026, 10, 26, 14, 0), self.latest, True, None)[0], "wait")

    def test_after_close_but_data_not_downloaded_waits(self):
        a, t, msg = rdr.decide(at(2026, 10, 26, 17, 30), self.latest, True, None)
        self.assertEqual((a, t), ("wait", None))
        self.assertIn("2026-10-23", msg)

    def test_after_close_with_todays_data_runs(self):
        a, t, _ = rdr.decide(at(2026, 10, 26, 17, 30), date(2026, 10, 26), True, None)
        self.assertEqual((a, t), ("run", date(2026, 10, 26)))

    def test_explicit_date_beyond_data_stops(self):
        self.assertEqual(rdr.decide(at(2026, 10, 26, 17, 30), self.latest, True, date(2026, 10, 27))[0], "stop")

    def test_explicit_past_date_runs_even_on_weekend(self):
        a, t, _ = rdr.decide(at(2026, 10, 25, 9, 0), self.latest, True, date(2026, 10, 22))
        self.assertEqual((a, t), ("run", date(2026, 10, 22)))


def synthetic(up_over, up_base, up_conf=85.0):
    def sr(signal, h, up, n=14):
        return {"signal": signal, "horizon": h, "index_up_pct": up, "observations": n, "episodes": n}
    L = {"eligible": 5000, "up": 3000, "down": 1800, "flat": 200, "breadth": .6, "amount_yi": 15000, "amount_ratio20": 1.05,
         "ma20_pct": 35.0, "high60": 120, "low60": 200, "bottom_state": "正常", "vol_temp": 50.0, "vol_state": "中"}
    series = [{"trade_date": "2026-10-22", "bottom_state": "正常", "breadth": .5, "ma20_pct": 30.0},
              {"trade_date": "2026-10-23", "bottom_state": "正常", "breadth": .6, "ma20_pct": 35.0}]
    return xhs.Ctx({"asof": "2026-10-23", "latest": L, "bottom_config": {}, "series": series,
                    "bottom_study": [sr("oversold", 20, up_over), sr("baseline", 20, up_base), sr("bottom_confirmed", 20, up_conf)]}, {})


class PostTests(unittest.TestCase):
    def test_title_and_body_within_limits(self):
        post = xhs.build_post(synthetic(55, 54))
        title = post.split("## 标题")[1].split("\n")[1]
        self.assertLessEqual(len(title), xhs.TITLE_MAX)
        body = post.split("## 正文")[1].split("## 话题标签")[0]
        self.assertLess(len(body), xhs.BODY_MAX + 40)
        self.assertIn("不构成任何投资建议", post)

    def test_oversold_comparison_is_computed(self):
        self.assertIn("差别不大", xhs.build_post(synthetic(56, 54)))
        self.assertIn("高出平时", xhs.build_post(synthetic(70, 54)))
        self.assertIn("低于平时", xhs.build_post(synthetic(40, 54)))

    def test_no_individual_stock_names_or_advice_words(self):
        post = xhs.build_post(synthetic(55, 54))
        for word in ("买入", "卖出", "建议买", "推荐", "稳赚", "必涨"):
            self.assertNotIn(word, post.replace("不构成任何投资建议", ""))


if __name__ == "__main__":
    unittest.main()
