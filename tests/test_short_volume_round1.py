"""Synthetic fixtures test causality/execution mechanics, not trading profitability."""
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

spec=importlib.util.spec_from_file_location('round1',Path(__file__).resolve().parents[1]/'research/short_volume_round1/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def bars(n=50):
    c=np.full(n,10.)
    return pd.DataFrame({'date':pd.bdate_range('2025-01-01',periods=n),'cal':np.arange(n),
        'open':c,'high':c+.1,'low':c-.1,'close':c,'factor':1.,'vol':100.,'amount':2e8,
        'qopen':c,'qhigh':c+.1,'qlow':c-.1,'qclose':c})


def test_no_future_rewrite_and_no_infinite_volume():
    d=bars();a=m.features(d)
    d.loc[35:,['qopen','qhigh','qlow','qclose','vol']]*=9
    pd.testing.assert_frame_equal(m.features(d).iloc[:35],a.iloc[:35])
    d['vol']=0
    assert not np.isinf(m.features(d).vr).any()


def test_base_memory_expires_after_three_sessions():
    d=bars();d.loc[20,'qhigh']=30
    f=m.features(d)
    assert f.base_recent.iloc[23]
    assert not f.base_recent.iloc[24]


def test_t_plus_one_and_fixed_horizon():
    d=bars(8);d.loc[2,'close']=10.05
    r=m.event_outcome(d,0,2,'600000.SH',7)
    assert r['exit_date']==str(d.date.iloc[2].date())
    assert r['entry_date']==str(d.date.iloc[1].date())
    assert np.isclose(r['gross'],.005)


def test_missing_next_market_session_does_not_enter_later():
    d=bars(8).drop(index=1).reset_index(drop=True)
    assert m.event_outcome(d,0,2,'600000.SH',7)['status']=='missing_next_session'


def test_corporate_action_reference_used_for_limit_entry():
    d=bars(8);d.loc[0,'factor']=.9;d.loc[1,'open']=9.9
    assert m.event_outcome(d,0,2,'600000.SH',7)['status']=='indicative_entry_rejected'


def test_limit_down_exit_delays_and_tail_is_censored():
    d=bars(8);d.loc[2,['close','high','low']]=9.
    d.loc[3,['close','high','low']]=[9.5,9.6,9.3]
    r=m.event_outcome(d,0,2,'600000.SH',7)
    assert r['exit_delay']==1
    assert r['exit_date']==str(d.date.iloc[3].date())
    assert m.event_outcome(d,6,5,'600000.SH',7)['status']=='censored'
