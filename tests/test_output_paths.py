"""Output folders: new writes go to outputs/, reads fall back to the legacy output/ folder."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import run_daily_board
import run_macro_regime
from project_paths import OUTPUT_ROOT

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BoardReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.new, self.legacy = base / "outputs", base / "output"
        self.new.mkdir(); self.legacy.mkdir()
        self._saved = (run_daily_board.OUT, run_daily_board.LEGACY_OUTPUT_DIR)
        run_daily_board.OUT, run_daily_board.LEGACY_OUTPUT_DIR = self.new, self.legacy

    def tearDown(self):
        run_daily_board.OUT, run_daily_board.LEGACY_OUTPUT_DIR = self._saved
        self._tmp.cleanup()

    def test_legacy_only_file_is_read_from_output(self):
        (self.legacy / "market_timing_20261023.json").write_text("{}", encoding="utf-8")
        self.assertEqual(run_daily_board.read_out("market_timing_20261023.json"), self.legacy / "market_timing_20261023.json")

    def test_new_copy_wins_over_legacy(self):
        (self.legacy / "macro_regime_20261023.csv").write_text("old", encoding="utf-8")
        (self.new / "macro_regime_20261023.csv").write_text("new", encoding="utf-8")
        self.assertEqual(run_daily_board.read_out("macro_regime_20261023.csv"), self.new / "macro_regime_20261023.csv")

    def test_missing_everywhere_points_to_legacy_so_exists_check_reruns(self):
        self.assertFalse(run_daily_board.read_out("sector_whitelist_20261023.csv").exists())


class BoardDefaultTests(unittest.TestCase):
    def test_board_writes_to_outputs_root_by_default(self):
        self.assertEqual(run_daily_board.OUT, OUTPUT_ROOT)


class MacroPathTests(unittest.TestCase):
    def test_macro_output_dir_is_absolute_outputs_root(self):
        self.assertEqual(run_macro_regime.OUTPUT_DIR, OUTPUT_ROOT)
        self.assertTrue(run_macro_regime.OUTPUT_DIR.is_absolute())


class MarketTimingPathTests(unittest.TestCase):
    def test_gate_read_falls_back_to_legacy(self):
        mt = _load(ROOT / "research/legacy_workspace/run_market_timing.py", "run_market_timing_paths")
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            new, legacy = base / "outputs", base / "output"
            new.mkdir(); legacy.mkdir()
            saved = (mt.OUTPUT_ROOT, mt.LEGACY_OUTPUT_DIR)
            mt.OUTPUT_ROOT, mt.LEGACY_OUTPUT_DIR = new, legacy
            try:
                (legacy / "qfq_verification.json").write_text("{}", encoding="utf-8")
                self.assertEqual(mt.read_output("qfq_verification.json"), legacy / "qfq_verification.json")
                (new / "qfq_verification.json").write_text("{}", encoding="utf-8")
                self.assertEqual(mt.read_output("qfq_verification.json"), new / "qfq_verification.json")
            finally:
                mt.OUTPUT_ROOT, mt.LEGACY_OUTPUT_DIR = saved


if __name__ == "__main__":
    unittest.main()
