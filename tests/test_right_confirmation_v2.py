"""Offline window/boundary regression tests, not a trading performance backtest."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rps_analyzer.right_confirmation_v2 import LayeredConfig, evaluate, shape_series, tdx_formula


def frame() -> pd.DataFrame:
    c = np.linspace(10, 40, 320)
    c[-16:] = 40
    data = pd.DataFrame(
        {
            "close": c,
            "high": c + 1,
            "low": c - 1,
            "vol": np.full(320, 1e7),
            "amount": np.full(320, 2e8),
        }
    )
    data.loc[314:318, "high"] = 40.4
    data.loc[314:318, "low"] = 39.6
    data.loc[314:318, "vol"] = 5e6
    return data


def test_layered_pool_does_not_require_today_breakout() -> None:
    result = evaluate(frame(), 96, 95, 2)
    assert result["trend_pool"] is True
    assert result["setup"] is True
    assert result["confirmed"] is False
    assert result["stage"] == "缩量整理观察"


def test_breakout_has_positive_control() -> None:
    d = frame()
    d.loc[319, ["close", "high", "low", "vol"]] = [42, 42.1, 40, 1.5e7]
    result = evaluate(d, 96, 95, 2)
    assert result["confirmed"] is True
    assert result["trend_pool"] is True
    assert result["setup"] is False
    assert result["stage"] == "放量突破确认"


def test_independent_nonoverlapping_windows() -> None:
    d = frame()
    s = shape_series(d, LayeredConfig())
    tr = [
        max(
            d.high.iloc[i] - d.low.iloc[i],
            abs(d.high.iloc[i] - d.close.iloc[i - 1]),
            abs(d.low.iloc[i] - d.close.iloc[i - 1]),
        )
        for i in range(1, len(d))
    ]
    assert s["compression"].iloc[-1] == pytest.approx(np.mean(tr[-6:-1]) / np.mean(tr[-16:-6]))
    assert s["dry"].iloc[-1] == pytest.approx(d.vol.iloc[-6:-1].mean() / d.vol.iloc[-26:-6].mean())
    assert s["atr"].iloc[-1] == pytest.approx(np.mean(tr[-21:-1]))


def test_today_and_future_cannot_change_prior_setup_references() -> None:
    d = frame()
    a = shape_series(d, LayeredConfig())
    d.loc[319, ["high", "low", "vol"]] = [400, 1, 1e10]
    b = shape_series(d, LayeredConfig())
    for key in ("compression", "dry", "width", "atr", "pivot"):
        assert a[key].iloc[-1] == b[key].iloc[-1]
    future = pd.concat([d, d.tail(30)], ignore_index=True)
    for key, value in shape_series(future, LayeredConfig()).items():
        pd.testing.assert_series_equal(value.iloc[:320], b[key])


@pytest.mark.parametrize("turnover", [None, 10])
def test_unknown_or_excessive_turnover_never_enters_pool(turnover: float | None) -> None:
    result = evaluate(frame(), 96, 95, turnover)
    assert result["trend_pool"] is not True
    assert result["confirmed"] is False


def test_strength_boundary_and_invalid_inputs() -> None:
    assert evaluate(frame(), 90, 90, 1)["trend_pool"] is False
    with pytest.raises(ValueError):
        evaluate(frame(), 101, 95, 1)
    with pytest.raises(ValueError):
        evaluate(frame(), 96, 95, float("nan"))
    d = frame()
    d.loc[319, "vol"] = np.nan
    with pytest.raises(ValueError):
        evaluate(d, 96, 95, 1)


def test_no_flat_atr_or_zero_volume_breakout() -> None:
    d = frame()
    d.loc[:, ["close", "high", "low"]] = 40
    d.loc[:, "vol"] = 0
    result = evaluate(d, 96, 95, 1)
    assert result["confirmed"] is False
    assert result["trend_pool"] is False


def test_formula_files_match_parameters_and_do_not_use_dynamic_count() -> None:
    root = Path(__file__).resolve().parents[1] / "config"
    for name, diagnostic in [
        ("right_confirmation_v2.tdx.txt", False),
        ("right_confirmation_v2_diagnostic.tdx.txt", True),
    ]:
        assert (root / name).read_text(encoding="utf-8") == tdx_formula(diagnostic=diagnostic)
    formula = tdx_formula()
    for forbidden in ("BACKSET", "ZIG(", "DRAWLINE", "COUNT(回撤", "BARSLAST"):
        assert forbidden not in formula
