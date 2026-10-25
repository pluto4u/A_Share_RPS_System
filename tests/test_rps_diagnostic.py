import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diag", ROOT / "research/rps_diag_20261006/diagnostic.py")
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class DiagnosticTests(unittest.TestCase):
    def test_match_excludes_candidate_and_weights_events(self):
        ca = np.array([True,True]+[False]*10)
        ba = np.ones(12,dtype=bool)
        cells = np.array([0,1]+[0]*5+[1]*5)
        y = np.array([.10,.20]+[.02]*5+[.04]*5)
        stats,usable,means = diag.match_day(ca,ba,cells,y)
        self.assertEqual(stats["known_cell_matched_selected"],2)
        self.assertEqual(stats["matched_used_labels"],2)
        np.testing.assert_array_equal(usable,ca)
        self.assertAlmostEqual(means[0],.02)
        self.assertAlmostEqual(means[1],.04)
        self.assertAlmostEqual(stats["matched_difference"],.12)

    def test_missing_future_never_changes_known_match_coverage(self):
        ca = np.array([True]+[False]*5)
        ba = np.ones(6,dtype=bool)
        cells = np.zeros(6,dtype=int)
        y = np.array([.10]+[np.nan]*5)
        stats,_,_ = diag.match_day(ca,ba,cells,y)
        self.assertEqual(stats["known_cell_matched_selected"],1)
        self.assertEqual(stats["matched_valid_labels"],1)
        self.assertEqual(stats["no_comparable_control_label"],1)
        self.assertTrue(np.isnan(stats["matched_difference"]))
        y[:] = np.nan
        other,_,_ = diag.match_day(ca,ba,cells,y)
        self.assertEqual(other["known_cell_matched_selected"],1)
        self.assertEqual(other["labels"],0)
        self.assertEqual(other["unavailable"],1)

    def test_known_minimum_five_is_not_relaxed(self):
        stats,_,_ = diag.match_day([1,0,0,0,0],[1]*5,[0]*5,[.1,.2,.3,.4,.5])
        self.assertEqual(stats["known_cell_matched_selected"],0)
        self.assertEqual(stats["labels"],1)

    def test_risk_prefix_and_signal_day_mutation(self):
        rng = np.random.default_rng(3)
        idx = pd.bdate_range("2020-01-01",periods=90)
        close = pd.DataFrame(np.exp(rng.normal(0,.02,(90,15)).cumsum(axis=0)),index=idx)
        amount = pd.DataFrame(rng.uniform(2e7,8e7,(90,15)),index=idx)
        eligible = pd.DataFrame(True,index=idx,columns=close.columns)
        a = diag.risk_features(close,amount,eligible)
        altered_close,altered_amount = close.copy(),amount.copy()
        altered_close.iloc[65:] *= 100
        altered_amount.iloc[65:] *= 100
        b = diag.risk_features(altered_close,altered_amount,eligible)
        prefix = diag.risk_features(close.iloc[:66],amount.iloc[:66],eligible.iloc[:66])
        for original,other,short in zip(a,b,prefix):
            pd.testing.assert_frame_equal(original.iloc[:66],other.iloc[:66])
            pd.testing.assert_frame_equal(original.iloc[:66],short)

    def test_ties_are_not_split_by_future_or_ticker(self):
        idx = pd.bdate_range("2020-01-01",periods=30)
        series = np.exp(np.sin(np.arange(30))*.01)
        close = pd.DataFrame(np.tile(series[:,None],(1,20)),index=idx)
        amount = pd.DataFrame(4e7,index=idx,columns=close.columns)
        eligible = close.notna()
        _,_,cells = diag.risk_features(close,amount,eligible)
        self.assertEqual(cells.iloc[-1].nunique(),1)
        self.assertEqual(cells.iloc[-1,0],12)

    def test_boundary_censors_cross_period_exits(self):
        idx = pd.bdate_range("2021-12-20",periods=20)
        allowed = diag.period_label_mask(idx,"2021-12-20","2021-12-31",5)
        for i,ok in enumerate(allowed):
            self.assertEqual(ok, i+6 < len(idx) and idx[i] <= pd.Timestamp("2021-12-31") and idx[i+6] <= pd.Timestamp("2021-12-31"))


if __name__ == "__main__":
    unittest.main()
