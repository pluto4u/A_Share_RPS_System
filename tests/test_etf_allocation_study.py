import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

PATH = Path(__file__).resolve().parents[1] / "research/etf_allocation_20261006/run_study.py"
SPEC = importlib.util.spec_from_file_location("etf_study", PATH)
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def frame(dates, close):
    values = np.broadcast_to(close, len(dates)).astype(float).copy()
    return pd.DataFrame({"open": values, "close": values, "tri": values,
                         "ex_cash": 0., "capacity": 1e9}, index=dates)


def test_signal_prefix_invariance():
    dates = pd.bdate_range("2019-01-01", periods=280)
    data = {"510300.SH": frame(dates, np.linspace(100, 150, len(dates))),
            "510500.SH": frame(dates, np.linspace(100, 90, len(dates))),
            "511010.SH": frame(dates, np.linspace(100, 103, len(dates))),
            "518880.SH": frame(dates, np.linspace(100, 120, len(dates)))}
    for model in ("trend200", "momentum126"):
        original = study.weights(data, 210, model)
        truncated = {s: f.iloc[:211].copy() for s, f in data.items()}
        assert study.weights(truncated, 210, model) == original
        changed = {s: f.copy() for s, f in data.items()}
        for f in changed.values():
            f.iloc[211:, f.columns.get_loc("tri")] = np.arange(len(f)-211) + 1e8
        assert study.weights(changed, 210, model) == original


def test_sales_cash_not_spent_at_same_open(monkeypatch):
    dates = pd.bdate_range("2020-01-01", "2020-02-05")
    data = {s: frame(dates, 100.) for s in ("510300.SH", "510500.SH")}
    switch = dates.get_loc(pd.Timestamp("2020-01-31"))
    monkeypatch.setattr(study, "weights", lambda frames, pos, model: {
        "510300.SH": float(pos < switch), "510500.SH": float(pos >= switch)})
    book, orders, _, _ = study.simulate(
        data, {s: [] for s in data}, "momentum126", "2020-01-03", "2020-02-05", 0., capital=100000.)
    sold = orders[(orders.date=="2020-02-03") & (orders.side=="sell")]
    same_day_buys = orders[(orders.date=="2020-02-03") & (orders.side=="buy")]
    next_day_buys = orders[(orders.date=="2020-02-04") & (orders.side=="buy")]
    assert sold.quantity.sum() == 900
    assert same_day_buys.quantity.sum() == 0
    assert next_day_buys.quantity.sum() == 900
    assert book.cash.min() >= 0
    assert (pd.to_datetime(orders.date) > pd.to_datetime(orders.signal_date)).all()
    assert ((book[["510300.SH", "510500.SH"]] % 100) == 0).all().all()


def test_dividend_entitlement_and_payment():
    dates = pd.bdate_range("2020-01-01", "2020-01-15")
    f = frame(dates, 100.)
    f.loc["2020-01-08":, ["open", "close"]] = 99.
    f.loc["2020-01-08", "ex_cash"] = 1.
    data = {"510300.SH": f}
    events = {"510300.SH": [dict(reg=pd.Timestamp("2020-01-07"),
                                  ex=pd.Timestamp("2020-01-08"),
                                  pay=pd.Timestamp("2020-01-10"), amount=1.)]}
    book, _, dividends, _ = study.simulate(
        data, events, "equity_hold", "2020-01-03", "2020-01-15", 0., capital=100000.)
    assert book.loc["2020-01-08", "receivable"] == 900
    assert book.loc["2020-01-08", "equity"] == book.loc["2020-01-07", "equity"]
    assert dividends.cash.sum() == 900
    assert book.loc["2020-01-10", "cash"] - book.loc["2020-01-09", "cash"] == 900
    assert book.loc["2020-01-10", "receivable"] == 0
    _, _, no_entitlement, _ = study.simulate(
        data, events, "equity_hold", "2020-01-08", "2020-01-15", 0., capital=100000.)
    assert no_entitlement.empty
