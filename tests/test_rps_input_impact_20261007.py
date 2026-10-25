import importlib.util
from pathlib import Path
import pandas as pd

PATH = Path(__file__).resolve().parents[1] / "research/rps_input_impact_20261007/impact.py"
spec = importlib.util.spec_from_file_location("impact_test", PATH)
impact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impact)


def test_adapter_exactly_matches_full_frozen_features():
    dates = pd.bdate_range("2018-01-01", periods=310)
    close = pd.DataFrame({"a":[10+i*.01 for i in range(310)], "b":[10+i*.02 for i in range(310)]}, index=dates)
    high = close * 1.005
    amount = pd.DataFrame(30_000_000.0,index=dates,columns=close.columns)
    volume = pd.DataFrame(3_000_000.0,index=dates,columns=close.columns)
    old, fixed = impact.study.features(close, high, amount, volume)
    actual = impact.activity_masks(close, high, amount, volume, fixed)
    assert all(actual[name].equals(old[name]) for name in old)


def test_positive_volume_magnitude_does_not_change_masks():
    dates = pd.bdate_range("2018-01-01",periods=310)
    close = pd.DataFrame({"a":[10+i*.01 for i in range(310)]},index=dates)
    amount = close * 3_000_000
    volume = close * 300_000
    old, fixed = impact.study.features(close,close*1.01,amount,volume)
    actual = impact.activity_masks(close,close*1.01,amount,volume*10,fixed)
    assert all(actual[name].equals(old[name]) for name in old)
