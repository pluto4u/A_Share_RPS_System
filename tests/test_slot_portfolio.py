"""Offline unit tests for trend_start.slot_portfolio — synthetic data only."""

from __future__ import annotations

import pandas as pd
from trend_start.slot_portfolio import slot_compound


def mk(rows: list[tuple[int, str, float, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["sig_cal", "code", "ret", "hold"])


def test_single_window_compounds() -> None:
    t = mk([(10, "000001.SZ", 0.10, 5), (10, "000002.SZ", -0.04, 5)])
    r = slot_compound(t, slots=2)
    # equity/2 each: 0.5*1.10 + 0.5*0.96 = 1.03
    assert abs(r.final_equity - 1.03) < 1e-9
    assert r.filled == 2 and r.skipped_locked == 0 and r.open_at_end == 0


def test_locked_slot_blocks_next_window() -> None:
    # window1 at day10 holds 5d; window2 at day12 (slots busy) must be skipped
    t = mk([(10, "000001.SZ", 0.10, 5), (12, "000002.SZ", 0.50, 1)])
    r = slot_compound(t, slots=1)
    assert r.filled == 1 and r.skipped_locked == 1
    assert abs(r.final_equity - 1.10) < 1e-9


def test_slot_frees_at_exit_day() -> None:
    # hold=2 -> free at day12; window2 at day12 enters
    t = mk([(10, "000001.SZ", 0.10, 2), (12, "000002.SZ", 0.20, 1)])
    r = slot_compound(t, slots=1)
    assert r.filled == 2
    assert abs(r.final_equity - 1.10 * 1.20) < 1e-9


def test_open_position_drains_at_realized_ret() -> None:
    t = mk([(10, "000001.SZ", 0.99, 100)])
    r = slot_compound(t, slots=2, cal_end=20)
    # every row carries its realized/marked ret, so drain closes it:
    # final = 0.5 idle cash + 0.5 * (1+0.99)
    assert r.open_at_end == 0
    assert abs(r.final_equity - (0.5 + 0.5 * 1.99)) < 1e-9


def test_empty_and_missing_columns() -> None:
    r = slot_compound(pd.DataFrame(columns=["sig_cal", "code", "ret", "hold"]))
    assert r.final_equity == 1.0 and r.filled == 0
    try:
        slot_compound(pd.DataFrame({"sig_cal": [1]}))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_priority_column_fills_first() -> None:
    # two candidates same day, 1 slot: prio=0 wins over default code order
    t = mk([(10, "000001.SZ", 0.50, 2), (10, "000002.SZ", 0.10, 2)])
    t["prio"] = [1, 0]                       # 000002 has priority
    r = slot_compound(t, slots=1)
    assert abs(r.final_equity - 1.10) < 1e-9  # picked 000002 (+10%), not +50%
