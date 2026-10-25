"""Offline tests for IS-only explanatory calculations, not strategy tuning."""

import numpy as np
import pandas as pd
import pytest
from diagnose_dual_is import exact_close_return, paired_admission, path_details, read_is, rps_band
from trend_start.dual_system import Context, StudyConfig, mean, trade_rows
from trend_start.engine import StockBars


def fixture_context() -> Context:
    c = np.linspace(10.0, 13.0, 300)
    dates = pd.bdate_range("2020-01-02", periods=300).to_numpy().astype("datetime64[D]")
    bars = StockBars(
        "600000.SH", np.arange(300), dates, c - 0.01, c + 0.02, c - 0.02, c, mean(c, 20)
    )
    return Context(bars, np.ones(300), np.full(300, 2e8), np.full(300, 90.0), 0)


def test_rps_bands_do_not_recompute_candidate_percentiles() -> None:
    actual = rps_band(pd.Series([0.0, 20.0, 20.01, 40.0, 80.0, 100.0])).astype(str).tolist()
    assert actual == ["0-20", "0-20", "20-40", "20-40", "60-80", "80-100"]
    with pytest.raises(ValueError):
        rps_band(pd.Series([np.nan]))


def test_history_csv_path_is_forbidden() -> None:
    with pytest.raises(ValueError, match="only registered IS"):
        read_is("../history/metrics.csv")


def test_calendar_horizon_preserves_suspended_day_missing() -> None:
    ctx = fixture_context()
    ctx.bars.cal[281:] += 1
    assert np.isnan(exact_close_return(ctx, 280, 1, 10.0))
    assert exact_close_return(ctx, 280, 5, 10.0) == pytest.approx((ctx.bars.c[284] / 10 - 1) * 100)


def test_exit_path_matches_engine_and_requires_full_post_window() -> None:
    ctx = fixture_context()
    ctx.bars.ma20[:] = 10.0
    ctx.bars.c[275] = 9.9
    specs = [{"model": "B_trend", "combo": -1, "sig_bar": 270, "pocket_low": 9.0, "volume_bar": -1}]
    frame, _ = trade_rows(ctx, specs, "2020-01-01")
    path = path_details(ctx, frame, 299)
    assert path.trigger_reason.iloc[0] == "ma20_only"
    assert path.post_exit_20_full_price_window.iloc[0]
    assert path.post_exit_20_reclaims_entry.iloc[0]
    for horizon in (1, 5, 20):
        assert f"signal_d{horizon}_close_return_pct" in path
        assert f"post_exit_d{horizon}_close_return_pct" in path
    assert "post_exit_d275_close_return_pct" not in path
    path = path_details(ctx, frame, 280)
    assert not path.post_exit_20_full_price_window.iloc[0]
    assert pd.isna(path.post_exit_20_reclaims_entry.iloc[0])


def test_paired_comparison_uses_same_dates_and_equal_date_weight() -> None:
    rows = pd.DataFrame(
        {
            "model": ["B_trend"] * 5,
            "sig_cal": [1, 1, 2, 2, 3],
            "signal_date": ["2020-01-02"] * 2 + ["2020-02-03"] * 2 + ["2020-03-03"],
            "disposition": ["admitted", "slots_full", "admitted", "insufficient_cash", "admitted"],
            "ret": [0.1, 0.0, 0.0, -0.1, 0.7],
        }
    )
    pairs, summary = paired_admission(rows, StudyConfig())
    assert pairs.sig_cal.tolist() == [1, 2]
    assert summary.paired_dates.iloc[0] == 2
    assert summary.mean_delta_pct.iloc[0] == pytest.approx(10.0)


def test_empty_paired_comparison_is_missing_not_zero() -> None:
    rows = pd.DataFrame(
        {
            "model": ["A_retest"],
            "sig_cal": [1],
            "signal_date": ["2020-01-02"],
            "disposition": ["admitted"],
            "ret": [0.1],
        }
    )
    pairs, summary = paired_admission(rows, StudyConfig())
    assert pairs.empty and summary.empty


def test_horizon_schema_is_stable_for_different_exit_trigger_indices() -> None:
    ctx = fixture_context()
    ctx.bars.ma20[:] = 10.0
    ctx.bars.c[[270, 280]] = 9.9
    specs = [
        {"model": "B_trend", "combo": -1, "sig_bar": j, "pocket_low": 9.0, "volume_bar": -1}
        for j in (265, 275)
    ]
    frame, _ = trade_rows(ctx, specs, "2020-01-01")
    path = path_details(ctx, frame, 299)
    cols = [c for c in path if c.startswith("post_exit_d")]
    assert cols == [
        "post_exit_d1_close_return_pct",
        "post_exit_d5_close_return_pct",
        "post_exit_d20_close_return_pct",
    ]
    assert len(path) == 2
