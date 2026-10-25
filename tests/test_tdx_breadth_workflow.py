import numpy as np
import pandas as pd
import pytest

from workflows.tdx_breadth import DAY, discover, evaluate, read_day


def test_binary_price_scale_and_cutoff(tmp_path):
    path = tmp_path / "sh600000.day"
    data = np.zeros(3, dtype=DAY)
    data["date"] = [20230103, 20230104, 20230105]
    data["close"] = [1000, 1100, 1200]
    data["high"] = data["close"]
    data["low"] = data["close"]
    path.write_bytes(data.tobytes())
    frame, digest = read_day(path, pd.Timestamp("2023-01-04"))
    assert frame.close.tolist() == [10., 11.]
    assert len(digest) == 64
    path.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="incomplete"):
        read_day(path)


def test_breadth_denominators_exclude_stale_suspended_and_short_history():
    dates = pd.bdate_range("2023-01-01", periods=65)
    def frame(n=65, volume=100):
        return pd.DataFrame({"date": dates[-n:], "close": np.arange(n) + 10., "volume": volume})
    samples = {"up": frame(), "flat": frame().assign(close=10.), "new": frame(2),
               "stale": frame().iloc[:-1], "suspended": frame(volume=0)}
    history, stocks = evaluate(samples, dates[-1])
    row = history.iloc[-1]
    assert row.active == 3
    assert row.eligible_ma20 == 2
    assert row.above_ma20_pct == 50.
    assert row.advancers == 2 and row.unchanged == 1
    assert len(stocks) == 3


def test_discovery_excludes_etf_and_index(tmp_path):
    base = tmp_path / "vipdoc/sh/lday"
    base.mkdir(parents=True)
    for name in ("sh600000.day", "sh510300.day", "sh000001.day", "sh688001.day"):
        (base / name).touch()
    assert [p.name for p in discover(tmp_path)] == ["sh600000.day", "sh688001.day"]
