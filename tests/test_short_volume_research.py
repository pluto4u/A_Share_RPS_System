"""Synthetic fixtures validate mechanics only; they are not market evidence."""
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

SPEC=importlib.util.spec_from_file_location('short_volume',Path(__file__).resolve().parents[1]/'research/short_volume_20261024/analyze.py')
MOD=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def fixture():
    c=np.linspace(10,13,50)
    return pd.DataFrame({'trade_date':pd.bdate_range('2025-01-01',periods=50),
        'open':c-.1,'high':c+.2,'low':c-.2,'close':c,'vol':np.arange(50)+100.,'amount':1e7})


def test_future_perturbation_cannot_rewrite_past_features():
    d=fixture();before=MOD.features(d)
    d.loc[36:,['open','high','low','close','vol','amount']]*=10
    after=MOD.features(d)
    pd.testing.assert_frame_equal(before.iloc[:36],after.iloc[:36])


def test_current_bar_not_in_prior_high_or_volume_baseline():
    d=fixture();before=MOD.features(d)
    d.loc[35,['high','vol']]=[999,999999]
    after=MOD.features(d)
    assert before.loc[35,'prior_high10']==after.loc[35,'prior_high10']
    assert before.loc[35,'vol_base10']==after.loc[35,'vol_base10']
    assert before.loc[35,'overhead_proxy15']==after.loc[35,'overhead_proxy15']


def test_zero_volume_and_flat_prices_do_not_create_infinite_features():
    d=fixture();d[['open','high','low','close']]=10.;d['vol']=0.
    out=MOD.features(d)
    assert out['vol_ratio'].isna().all()
    assert out['efficiency10'].isna().all()
    assert not out['prototype'].any()
