"""Synthetic fixtures verify confirmation timing, not profitability."""
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

spec=importlib.util.spec_from_file_location('round2',Path(__file__).resolve().parents[1]/'research/short_volume_round2/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def bars():
    c=np.full(50,10.)
    return pd.DataFrame({'date':pd.bdate_range('2025-01-01',periods=50),'cal':np.arange(50),
        'open':c,'high':c+.2,'low':c-.1,'close':c+.1,'factor':1.,'vol':100.,
        'qopen':c,'qhigh':c+.2,'qlow':c-.1,'qclose':c+.1})


def test_one_day_confirmation_is_prefix_safe():
    d=bars();before=m.confirmations(d,20)
    d.loc[22:,['qhigh','qlow','qclose','vol']]*=5
    after=m.confirmations(d,20)
    assert before['P1']==after['P1']
    assert before['V1']==after['V1']
    assert m.confirmations(d.iloc[:22],20)['P1']==before['P1']


def test_confirmed_entry_must_wait_until_following_open():
    d=bars();r=m.outcome(d,20,1,2,'600000.SH',49)
    assert r['entry_date']==str(d.date.iloc[22].date())
    assert r['exit_date']==str(d.date.iloc[23].date())


def test_full_entry_day_range_cannot_decide_open_rejection():
    d=bars();a=m.outcome(d,20,0,2,'600000.SH',49)
    d.loc[21,['high','low','close']]=10.
    b=m.outcome(d,20,0,2,'600000.SH',49)
    assert a['status']==b['status']=='estimated_filled'


def test_volume_confirmation_is_not_monotonic_volume_demand():
    d=bars();d.loc[20,'vol']=250;d.loc[21,'vol']=150
    assert m.confirmations(d,20)['V1']
    d.loc[21,'vol']=50
    assert m.confirmations(d,20)['P1']
    assert not m.confirmations(d,20)['V1']


def test_gap_cannot_be_replaced_by_later_confirmation():
    d=bars().drop(index=21).reset_index(drop=True)
    f=m.confirmations(d,20)
    assert not f['observe1'] and not f['P1'] and not f['observe2']
    assert m.outcome(d,20,0,2,'600000.SH',49)['status']=='missing_entry_session'


def test_adjusted_reference_for_open_limit_and_censoring():
    d=bars();d.loc[20,'factor']=.9;d.loc[21,'open']=10.
    assert m.outcome(d,20,0,2,'600000.SH',49)['status']=='indicative_open_rejected'
    assert m.outcome(d,48,2,3,'600000.SH',49)['status']=='censored'
