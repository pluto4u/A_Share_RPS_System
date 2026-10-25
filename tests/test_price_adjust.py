import numpy as np
import pandas as pd
import pytest

from rps_analyzer.price_adjust import adjust_one, forward_adjust_market


def _bars(closes, start="2026-01-05"):
    dates = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "trade_date": dates, "open": c, "high": c, "low": c, "close": c,
        "amount": 1e8, "vol": 1e6,
    })


def _event(date, category=1, cash=0.0, bonus=0.0, rights_price=0.0, rights=0.0):
    return pd.DataFrame([{
        "date": pd.Timestamp(date), "category": category,
        "hongli_panqianliutong": cash, "peigujia_qianzongguben": rights_price,
        "songgu_qianzongguben": bonus, "peigu_houzongguben": rights,
    }])


def test_bonus_share_gap_is_removed():
    raw = _bars([20, 20, 10, 10])
    ev = _event(raw.trade_date[2], bonus=10)
    adj, n = adjust_one(raw, ev, raw.trade_date.iloc[-1])
    assert n == 1
    assert adj.close.tolist() == pytest.approx([10, 10, 10, 10])


def test_cash_dividend_ratio_and_latest_anchor():
    raw = _bars([10, 10, 9.9, 9.9])
    ev = _event(raw.trade_date[2], cash=1.0)
    adj, _ = adjust_one(raw, ev, raw.trade_date.iloc[-1])
    assert adj.close.iloc[0] == pytest.approx(9.9)
    assert adj.close.iloc[-1] == raw.close.iloc[-1]


def test_events_after_target_are_ignored():
    raw = _bars([20, 20, 10, 10])
    ev = _event(raw.trade_date[2], bonus=10)
    adj, n = adjust_one(raw, ev, raw.trade_date[1])
    assert n == 0
    assert len(adj) == 2
    assert adj.close.tolist() == [20, 20]


def test_special_category_excluded_not_guessed():
    a, b = _bars([10, 10, 10]), _bars([10, 10, 10])
    a["ts_code"], b["ts_code"] = "000001.SZ", "000002.SZ"
    events = {"000002.SZ": _event(b.trade_date[1], category=11)}
    out, excluded = forward_adjust_market(pd.concat([a, b]), events, verbose=False)
    assert set(out.ts_code) == {"000001.SZ"}
    assert excluded.to_dict("records") == [
        {"ts_code": "000002.SZ", "reason": "unsupported adjustment category"}
    ]
