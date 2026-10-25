import pandas as pd

from run_right_side_research import classify


def base(**kwargs):
    r = dict(bars_count=300, name='Example', ts_code='600001.SH', adv20=2e8,
             amount=2e8, close=105, ma20=100, ma60=90, slope20=.01,
             slope60=.01, vs_ma20_pct=5, ret20=.1, ret1=.02, vr=1.5,
             clv=.8, ph20=104, width20=.15, ud20=1.5, low=103,
             open=103, ph5=104, dry=.8, width10=.08)
    r.update(kwargs)
    return pd.Series(r)


def test_high_strength_does_not_override_broken_trend():
    assert classify(base(ma60=101))[0] == '趋势未齐'


def test_breakout_needs_volume_and_position():
    assert classify(base())[0] == '放量突破'
    assert classify(base(vr=.8))[0] != '放量突破'
    assert classify(base(vs_ma20_pct=11))[0] == '过热等待'


def test_platform_is_not_completed_breakout():
    assert classify(base(close=103, vr=.8, ph5=104))[0] == '平台待突破'


def test_risk_name_is_excluded_before_shape():
    assert classify(base(name='ST Example'))[0] == '排除'
