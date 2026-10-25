import unittest

import run_trend_start_review as rv


class StageTests(unittest.TestCase):
    def test_highest_stage_wins(self):
        self.assertEqual(rv.stage_of({"YJ2": True, "QD2": True}), "QD2")
        self.assertEqual(rv.stage_of({"YJ2": True, "QR2": True, "QQR2": True}), "QQR2")
        self.assertEqual(rv.stage_of({"YJ2": True}), "YJ2")
        self.assertIsNone(rv.stage_of({}))

    def test_negative_pe_reads_as_loss_and_dominant_label_is_flagged(self):
        self.assertEqual(rv.fmt_pe(-70.2), "亏损")
        self.assertEqual(rv.fmt_pe(23.4), "23")
        same = [{"money": {"label": "熊市急涨：诱多风险（x）"}}] * 9 + [{"money": {"label": "无明显主力行为信号"}}]
        self.assertIn("几乎没有区分度", rv.dominant_label_note(same))
        mixed = [{"money": {"label": "A：x"}}, {"money": {"label": "B：x"}}]
        self.assertEqual(rv.dominant_label_note(mixed), "")

    def test_fmt_handles_missing(self):
        self.assertEqual(rv.fmt(None), "—")
        self.assertEqual(rv.fmt(float("nan")), "—")
        self.assertEqual(rv.fmt(3.14159, 2, "%"), "3.14%")


if __name__ == "__main__":
    unittest.main()
