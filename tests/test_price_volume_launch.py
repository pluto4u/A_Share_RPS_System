from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trend_start.panel import Panel
from trend_start.price_volume import LaunchConfig, launch_events, launch_features
from trend_start.pv_engine import simulate_launch
from trend_start.research import StudyConfig


def stock() -> pd.DataFrame:
    c = np.r_[np.linspace(8, 12, 300), np.full(20, 12.0), 12.30, 12.34, 12.27, 12.40]
    h, lo = c + 0.25, c - 0.25
    h[310:320], lo[310:320] = c[310:320] + 0.05, c[310:320] - 0.05
    h[320:], lo[320:] = [12.32, 12.38, 12.32, 12.42], [12.22, 12.31, 12.24, 12.30]
    volume = np.full(len(c), 100.0)
    volume[310:320] = 50
    volume[320:] = [160, 90, 50, 80]
    return pd.DataFrame(
        {"open": c, "close": c, "high": h, "low": lo, "vol": volume, "amount": np.full(len(c), 2e8)}
    )


def test_positive_launch_and_frozen_later_retest():
    d, cfg = stock(), LaunchConfig(prior_advance_min=0.02)
    f = launch_features(d, cfg)
    e = launch_events(d, f, cfg)
    assert f.raw_breakout.iloc[320]
    assert e.direct.iloc[320]
    assert e.state.iloc[322] == "touched"
    assert e.retest.iloc[323]
    assert e.frozen_pivot.iloc[323] == pytest.approx(12.25)
    assert e.frozen_pocket.iloc[323] == pytest.approx(11.95)
    assert e.event_bar.iloc[323] == 320
    # Regression: pandas.pivot is a method, never use attribute access for this column.
    filled = e.frozen_pivot.fillna(f["pivot"]).to_numpy(dtype=float)
    assert filled[323] == pytest.approx(12.25)


def test_rps_never_changes_new_features_or_events():
    d, cfg = stock(), LaunchConfig(prior_advance_min=0.02)
    reference = launch_features(d, cfg)
    d["RPS50"], d["RPS120"], d["RPS250"] = 1, 2, 3
    low = launch_features(d, cfg)
    d[["RPS50", "RPS120", "RPS250"]] = 100
    high = launch_features(d, cfg)
    pd.testing.assert_frame_equal(reference, low)
    pd.testing.assert_frame_equal(reference, high)
    pd.testing.assert_frame_equal(launch_events(d, low, cfg), launch_events(d, high, cfg))


def test_no_future_in_features_or_state():
    d, cfg = stock(), LaunchConfig(prior_advance_min=0.02)
    f, e = launch_features(d, cfg), launch_events(d, launch_features(d, cfg), cfg)
    d.loc[323:, ["open", "high", "low", "close"]] *= 3
    d.loc[323:, "vol"] *= 10
    changed = launch_features(d, cfg)
    pd.testing.assert_frame_equal(f.iloc[:323], changed.iloc[:323])
    pd.testing.assert_frame_equal(e.iloc[:323], launch_events(d, changed, cfg).iloc[:323])


def test_quiet_touch_cannot_confirm_same_day_and_floor_failure():
    d, cfg = stock(), LaunchConfig(prior_advance_min=0.02)
    d.loc[322, "close"] = 12.40
    d.loc[322, "high"] = 12.42
    e = launch_events(d, launch_features(d, cfg), cfg)
    assert not e.retest.iloc[322]
    d.loc[322, "close"] = 11.8
    d.loc[322, "low"] = 11.7
    failed = launch_events(d, launch_features(d, cfg), cfg)
    assert failed.state.iloc[322] == "invalid"
    assert not failed.retest.any()


def test_location_rejects_extended_breakout_without_hiding_raw_event():
    d, cfg = stock(), LaunchConfig(prior_advance_min=0.02)
    d.loc[320, ["open", "close", "high", "low"]] = [13, 13, 13.02, 12.90]
    f = launch_features(d, cfg)
    e = launch_events(d, f, cfg)
    assert e.raw.iloc[320]
    assert not e.direct.iloc[320]


def fixtures(c: list[float]) -> tuple[Panel, np.ndarray, np.ndarray]:
    close = np.array([c], dtype=float)
    dates = pd.bdate_range("2024-01-02", periods=len(c)).to_numpy().astype("datetime64[D]")
    p = Panel(
        dates,
        np.array(["600000.SH"]),
        {
            "open": close.copy(),
            "close": close,
            "high": close + 0.01,
            "low": close - 0.01,
            "vol": np.full(close.shape, 100),
            "amount": np.full(close.shape, 1e8),
        },
        np.full(len(c), 100.0),
    )
    sig = np.zeros(close.shape, dtype=bool)
    sig[0, 5] = True
    return p, sig, np.ones(close.shape)


def simulation(p: Panel, sig: np.ndarray, mode="staged", guard=False, launch=None, start=0):
    shape = sig.shape
    m20 = np.array([np.linspace(9.5, 10, shape[1])])
    return simulate_launch(
        p,
        sig,
        np.ones(shape),
        np.full(shape, 10),
        np.full(shape, 9.5),
        np.full(shape, 1.0),
        m20,
        np.full(shape, 9.9),
        StudyConfig(
            capital=1000, slots=1, slippage=0, commission=0, main_limit=0.1, max_entry_gap=0.2
        ),
        launch or LaunchConfig(),
        mode,
        guard,
        start,
        len(p.dates) - 1,
    )


def test_trial_pivot_failure_is_not_generic_ma20_exit():
    p, sig, _ = fixtures([10.1] * 7 + [9.98, 9.96, 9.99])
    r = simulation(p, sig)
    assert r.trades.reason.iloc[0] == "trial_pivot_failed"
    assert r.trades.exit_date.iloc[0] == str(p.dates[9])
    assert r.trades.exit_stage.iloc[0] == "trial"


def test_proven_trend_survives_pivot_loss_until_ma60():
    p, sig, _ = fixtures([10.1] * 11 + [9.98, 9.95, 9.8, 9.85])
    r = simulation(p, sig)
    assert (r.orders.side == "phase").sum() == 1
    assert r.trades.exit_stage.iloc[0] == "trend"
    assert r.trades.reason.iloc[0] == "close_below_ma60"
    assert r.trades.exit_date.iloc[0] == str(p.dates[14])


def test_next_open_location_guard_and_no_rps_priority():
    p, sig, _ = fixtures([10.1] * 10)
    p.data["open"][0, 6] = 10.6
    r = simulation(p, sig, guard=True)
    assert r.open_positions.empty
    assert "next_open_structure_location" in r.orders.reason.to_list()


def test_t_plus_one_floor_failure_and_pending_down_limit():
    p, sig, _ = fixtures([10.1] * 6 + [9.4, 9.45, 9.5])
    p.data["open"][0, 6] = 10.1
    p.data["open"][0, 7] = 8.46
    r = simulation(p, sig)
    assert r.trades.exit_date.iloc[0] == str(p.dates[8])
    assert (r.orders.status == "deferred").sum() == 1


def test_lifecycle_execution_prefix_and_cash_invariant():
    p, sig, _ = fixtures([10.1] * 14)
    first = simulation(p, sig)
    for key in ("open", "high", "low", "close"):
        p.data[key][:, 12:] *= 3
    changed = simulation(p, sig)
    pd.testing.assert_frame_equal(first.daily.iloc[:12], changed.daily.iloc[:12])
    np.testing.assert_allclose(
        changed.daily.equity, changed.daily.cash + changed.daily.market_value
    )


def test_trial_timeout():
    p, sig, _ = fixtures([10.1] * 12)
    r = simulation(p, sig, launch=LaunchConfig(proven_days=20, retest_days=3))
    assert r.trades.reason.iloc[0] == "trial_timeout"
    assert r.trades.exit_date.iloc[0] == str(p.dates[9])


def test_new_window_starts_in_cash_and_ignores_prior_signals():
    p, sig, _ = fixtures([10.1] * 12)
    sig[0, 8] = True
    r = simulation(p, sig, start=7)
    assert r.daily.date.iloc[0] == str(p.dates[7])
    assert r.daily.cash.iloc[0] == 1000
    assert r.daily.positions.iloc[0] == 0
    filled = r.orders.loc[(r.orders.side == "entry") & (r.orders.status == "filled")]
    assert len(filled) == 1
    assert r.open_positions.entry_date.iloc[0] == str(p.dates[9])
