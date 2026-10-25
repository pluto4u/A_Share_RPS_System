from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trend_start.simple_trend import TrendConfig, trend_state


def data() -> pd.DataFrame:
    c = np.linspace(10, 20, 300)
    return pd.DataFrame(
        {
            "open": c,
            "close": c,
            "high": c + 0.1,
            "low": c - 0.1,
            "vol": np.full(300, 100.0),
            "amount": np.full(300, 1e8),
        }
    )


def test_only_price_and_ma20_slope_decide_membership() -> None:
    d = data()
    f = trend_state(d, TrendConfig())
    assert f.trend.iloc[-1]
    assert f.aligned.iloc[-1]
    assert not f.trend.iloc[249]
    d.loc[299, "vol"] = 1
    d.loc[299, "amount"] = 1
    assert trend_state(d, TrendConfig()).trend.iloc[-1]


def test_rps_ignored() -> None:
    d = data()
    reference = trend_state(d, TrendConfig())
    d["RPS120"] = 0
    d["RPS250"] = 100
    pd.testing.assert_frame_equal(reference, trend_state(d, TrendConfig()))


def test_future_bars_do_not_change_prefix() -> None:
    d = data()
    f = trend_state(d, TrendConfig())
    d.loc[280:, ["open", "close", "high", "low"]] *= 2
    d.loc[280:, "vol"] *= 10
    pd.testing.assert_frame_equal(f.iloc[:280], trend_state(d, TrendConfig()).iloc[:280])


def test_quiet_down_close_is_not_vetoed() -> None:
    d = data()
    d.loc[299, ["open", "high", "low", "close"]] = [19.95, 20, 19.85, 19.90]
    d.loc[299, "vol"] = 50
    f = trend_state(d, TrendConfig())
    assert f.trend.iloc[-1]
    assert f.stage.iloc[-1] == "回踩观察"


def test_breakout_has_no_hard_volume_threshold() -> None:
    d = data()
    d.loc[299, ["open", "high", "low", "close"]] = [20.3, 20.6, 20.2, 20.5]
    d.loc[299, "vol"] = 50
    f = trend_state(d, TrendConfig())
    assert f.stage.iloc[-1] == "突破扩展"
    assert f.volume_ratio5.iloc[-1] == pytest.approx(0.5)


def test_missing_and_bad_envelope_fail_explicitly() -> None:
    d = data()
    d.loc[299, "close"] = np.nan
    with pytest.raises(ValueError, match="Missing"):
        trend_state(d, TrendConfig())
    d = data()
    d.loc[299, "high"] = 1
    with pytest.raises(ValueError, match="Invalid high"):
        trend_state(d, TrendConfig())


def test_warning_stays_in_trend_pool() -> None:
    d = data()
    d.loc[299, ["open", "high", "low", "close"]] = [19.75, 19.8, 19.65, 19.7]
    d.loc[299, "vol"] = 200
    f = trend_state(d, TrendConfig())
    assert f.trend.iloc[-1]
    assert f.warning_flag.iloc[-1]
    assert f.stage.iloc[-1] == "转弱警示"


def test_quiet_contraction_without_down_close() -> None:
    d = data()
    d.loc[295:, ["open", "high", "low", "close"]] = [19.9, 19.92, 19.88, 19.9]
    d.loc[295:, "vol"] = 50
    f = trend_state(d, TrendConfig())
    assert f.trend.iloc[-1]
    assert f.stage.iloc[-1] == "缩量整理"


def test_close_below_ma20_is_not_in_pool() -> None:
    d = data()
    d.loc[299, ["open", "high", "low", "close"]] = [18, 18.1, 17.9, 18]
    f = trend_state(d, TrendConfig())
    assert not f.trend.iloc[-1]
    assert f.stage.iloc[-1] == "趋势条件不满足"
