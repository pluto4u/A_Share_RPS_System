import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "research/rps_pv_20261006/study.py"
SPEC = importlib.util.spec_from_file_location("rps_pv", PATH)
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def data():
    rng = np.random.default_rng(2)
    dates = pd.bdate_range("2019-01-01", periods=360)
    close = pd.DataFrame(np.exp(rng.normal(0, .015, (360, 8)).cumsum(axis=0))*50,
                         index=dates, columns=[str(i) for i in range(8)])
    return close, close*1.005, pd.DataFrame(3e7, index=dates, columns=close.columns), pd.DataFrame(1e6, index=dates, columns=close.columns)


def test_prefix_and_future_mutation_invariance():
    arrays = data()
    full, desc = study.features(*arrays)
    prefix, earlier = study.features(*(a.iloc[:311] for a in arrays))
    changed = [a.copy() for a in arrays]
    for a in changed:
        a.iloc[311:] = 1e9
    mutated, _ = study.features(*changed)
    for model in full:
        pd.testing.assert_frame_equal(full[model].iloc[:311], prefix[model])
        pd.testing.assert_frame_equal(full[model].iloc[:311], mutated[model].iloc[:311])
    for field in desc:
        pd.testing.assert_frame_equal(desc[field].iloc[:311], earlier[field])


def test_rps_ranks_before_liquidity_filter():
    close, high, amount, volume = data()
    _, desc = study.features(close, high, amount, volume)
    changed = amount.copy()
    changed.iloc[:, 0] = 1
    _, after = study.features(close, high, changed, volume)
    pd.testing.assert_frame_equal(desc["rps120"], after["rps120"])


def test_next_open_label_and_missing_no_zero_fill():
    dates = pd.bdate_range("2020-01-01", periods=8)
    open_ = pd.DataFrame({"A": np.arange(100.,108.)}, index=dates)
    volume = open_*1000
    result = study.outcome(open_, volume, 2)
    assert result.iloc[0, 0] == pytest.approx(103/101-1)
    volume.iloc[3, 0] = 0
    assert pd.isna(study.outcome(open_, volume, 2).iloc[0, 0])
    assert result.iloc[-3:].isna().all().all()


def test_no_cross_period_labels():
    dates = pd.to_datetime(["2021-12-29", "2021-12-30"])
    daily = pd.DataFrame(dict(signal_date=dates, selected=[10, 10], valid=[8, 8], unavailable=[2, 2],
                              gross_proxy=[.1, .2], positive_fraction=[.5, .5],
                              label_exit_date=pd.to_datetime(["2021-12-31", "2022-01-04"])))
    result = study.isolate_period(daily, "2021-01-01", "2021-12-31")
    assert result.loc[0, "valid"] == 8
    assert result.loc[1, "valid"] == 0 and result.loc[1, "unavailable"] == 10
    assert pd.isna(result.loc[1, "gross_proxy"])


def test_statistical_point_uses_same_month_weights_as_interval():
    dates = pd.bdate_range("2020-01-01", "2021-12-31")
    x = pd.Series(np.linspace(-.01,.03,len(dates)), index=dates)
    x = x.drop(x.loc["2020-02"].index[2:])
    result = study.paired_interval(x)
    assert result["mean_pp"] == pytest.approx(x.resample("ME").mean().mean()*100)
    assert result["daily_mean_pp"] == pytest.approx(x.mean()*100)
