import numpy as np
import pandas as pd

from run_optical_overlay import gaps
from run_tdx_formula_audit import formula


def test_gaps_keep_trend_and_heat_separate():
    row = pd.Series(dict(close=120, ma20=100, ma60=110, slope20=.01,
                         slope60=-.01, vs_ma20_pct=20, ret20=.4))
    result = gaps(row)
    assert 'MA20未上穿MA60' in result
    assert 'MA60未向上' in result
    assert '距MA20超过10%' in result
    assert '20日涨幅超过35%' in result


def test_original_formula_inclusive_volume_and_redundant_strong_branch():
    close = np.concatenate([np.linspace(100, 50, 270), np.linspace(50, 90, 30)])
    d = pd.DataFrame(dict(ts_code='600001.SH', name='样本',
        trade_date=pd.bdate_range('2025-01-01', periods=300),
        open=close-.1, close=close, high=close+.2, low=close-.2,
        vol=np.arange(300)+100., amount=2e8))
    got = formula(d)
    assert got.vm20.iloc[-1] == d.vol.iloc[-20:].mean()
    assert not (got.QQR & ~got.QR).any()
    assert (got.XG == (got.BASE & (got.YJ | got.QD | got.QR))).all()
    assert not got.BASE.iloc[249]
    assert got.BASE.iloc[250]


def test_lowbars_uses_most_recent_tie():
    close = np.full(300, 100.)
    d = pd.DataFrame(dict(ts_code='600001.SH', name='样本',
        trade_date=pd.bdate_range('2025-01-01', periods=300),
        open=close, close=close, high=close+1, low=close-1,
        vol=100., amount=2e8))
    got = formula(d)
    assert got.LOWAGE.iloc[-1] == 0
    assert not got.order_ok.iloc[-1]
