from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trend_start.drop_base_retest import PatternConfig, pattern_events


def sample() -> pd.DataFrame:
    c = np.r_[
        np.full(30, 20.0), np.linspace(20, 10, 30), np.full(15, 10.0), [10.3, 10.4, 10.12, 10.42]
    ]
    h, lo, v = c + 0.05, c - 0.05, np.full(len(c), 100.0)
    v[60:65], v[65:75] = 80, 40
    v[75:] = [100, 70, 35, 60]
    h[75], lo[75] = 10.32, 10.20
    h[77], lo[77] = 10.2, 10.04
    return pd.DataFrame(
        {"open": c, "close": c, "high": h, "low": lo, "vol": v, "amount": np.full(len(c), 2e8)}
    )


def test_drop_base_break_and_later_quiet_retest() -> None:
    d = sample()
    e = pattern_events(d, PatternConfig())
    assert e.breakout.iloc[75]
    assert not e.review.iloc[75]
    assert e.review.iloc[77]
    assert e.stage.iloc[77] == "缩量回踩待确认"
    assert e.confirmed.iloc[78]
    assert e["pivot"].iloc[78] == e["pivot"].iloc[75]
    assert e.start.iloc[78] == e.start.iloc[75]
    assert e.peak.iloc[75] < e.trough.iloc[75] < e.start.iloc[75] < e.event_bar.iloc[75]


def test_no_large_fall_no_admission() -> None:
    d = sample()
    d.loc[:59, ["open", "close", "high", "low"]] = [10, 10, 10.05, 9.95]
    assert not pattern_events(d, PatternConfig()).breakout.any()


def test_volume_must_expand_against_quiet_base() -> None:
    d = sample()
    d.loc[75:, "vol"] = 20
    assert not pattern_events(d, PatternConfig()).breakout.any()


def test_future_edits_do_not_change_prefix_and_rps_ignored() -> None:
    d = sample()
    e = pattern_events(d, PatternConfig())
    d["RPS120"], d["RPS250"] = 0, 0
    pd.testing.assert_frame_equal(e, pattern_events(d, PatternConfig()))
    d.loc[78:, ["open", "close", "high", "low"]] *= 2
    pd.testing.assert_frame_equal(e.iloc[:78], pattern_events(d, PatternConfig()).iloc[:78])


def test_failed_base_cannot_remain_review_candidate() -> None:
    d = sample()
    d.loc[78, ["open", "close", "high", "low"]] = [9, 9, 9.1, 8.9]
    e = pattern_events(d, PatternConfig())
    assert e.stage.iloc[78] == "形态失效"
    assert not e.review.iloc[78]


def test_loud_defended_touch_is_distinct_from_quiet_touch() -> None:
    d = sample()
    d.loc[77, ["open", "close", "high", "low"]] = [10.07, 10.12, 10.15, 10.04]
    d.loc[77, "vol"] = 160
    e = pattern_events(d, PatternConfig())
    assert e.loud_touch.iloc[77]
    assert not e.quiet_touch.iloc[77]
    assert e.stage.iloc[77] == "放量回踩守位待确认"


def test_bad_windows_and_missing_data_raise() -> None:
    with pytest.raises(ValueError, match="base_max"):
        PatternConfig(base_min=30, base_max=10)
    d = sample()
    d.loc[78, "close"] = np.nan
    with pytest.raises(ValueError, match="Missing"):
        pattern_events(d, PatternConfig())


def test_no_same_day_confirmation_or_moving_breakout() -> None:
    e = pattern_events(sample(), PatternConfig())
    assert not e.confirmed.iloc[77]
    assert e.breakout.sum() == 1
    assert (e.event_bar.iloc[75:] == 75).all()


def test_two_closes_below_pivot_tolerance_invalidate() -> None:
    d = sample()
    d.loc[60:74, "low"] = 9.8
    d.loc[77:78, ["open", "close", "high", "low"]] = [9.82, 9.82, 9.9, 9.75]
    e = pattern_events(d, PatternConfig())
    assert e.stage.iloc[77] != "形态失效"
    assert e.stage.iloc[78] == "形态失效"
    assert not e.review.iloc[78]


def test_event_expires_and_scan_prefix_is_causal() -> None:
    d = sample()
    extra = d.iloc[[-1]].copy()
    d = pd.concat([d, extra], ignore_index=True)
    e = pattern_events(d, PatternConfig(event_days=3), scan_start=50)
    assert e.stage.iloc[-1] == "事件过期"
    assert not e.review.iloc[-1]
    pd.testing.assert_frame_equal(
        e.iloc[:-1], pattern_events(d.iloc[:-1], PatternConfig(event_days=3), scan_start=50)
    )


@pytest.mark.parametrize("corrupt", ["pivot", "review_date", "close"])
def test_independent_validator_rejects_corrupt_output(corrupt: str) -> None:
    from run_drop_base_retest import Candidate
    from validate_drop_base_retest import validate_candidate

    d = sample()
    d["date"] = pd.date_range("2020-01-01", periods=len(d)).astype(str)
    e = pattern_events(d, PatternConfig()).iloc[-1]
    dates = d.date.tolist()
    row = Candidate(
        code="000001.SZ",
        name="fixture",
        as_of=dates[-1],
        stage=str(e.stage),
        review_kind=str(e.review_kind),
        close=float(d.close.iloc[-1]),
        ma20=float(d.close.iloc[-20:].mean()),
        ma60=float(d.close.iloc[-60:].mean()),
        drop_pct=float(e.drop_pct),
        base_start=dates[int(e.start)],
        base_end=dates[int(e.end)],
        base_bars=int(e.end - e.start + 1),
        peak_date=dates[int(e.peak)],
        trough_date=dates[int(e.trough)],
        event_date=dates[int(e.event_bar)],
        review_date=dates[int(e.last_review_bar)],
        pivot=float(e["pivot"]),
        floor=float(e.floor),
        width_pct=float(e.width_pct),
        dry_ratio=float(e.dry_ratio),
        breakout_volume_ratio=float(e.break_volume / e.quiet_volume),
        volume_ratio5=float(e.volume_ratio5),
        extension_pct=float((d.close.iloc[-1] / e["pivot"] - 1) * 100),
        RPS50=None,
        RPS120=None,
        RPS250=None,
        source_file="fixture.day",
    )
    validate_candidate(row, d, PatternConfig())
    damaged = row.model_copy(update={corrupt: dates[-2] if corrupt == "review_date" else 999})
    with pytest.raises(ValueError):
        validate_candidate(damaged, d, PatternConfig())
