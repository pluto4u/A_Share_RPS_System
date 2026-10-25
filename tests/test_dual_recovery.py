"""Offline parity tests for memory-only replay recovery, no strategy changes."""

from pathlib import Path

import numpy as np
import pandas as pd
from resume_dual_research import stream_book
from trend_start.dual_system import TRADE_COLUMNS, StudyConfig, book
from trend_start.panel import Panel


def test_streamed_book_matches_frozen_with_rejections_and_stale_marks(tmp_path: Path) -> None:
    dates = pd.bdate_range("2022-01-03", periods=8).to_numpy().astype("datetime64[D]")
    close = np.full((25, 8), 10.0)
    close[0, 3] = np.nan
    codes = np.array([f"{i:06d}.SZ" for i in range(25)])
    panel = Panel(dates, codes, {"close": close}, np.ones(8))
    rows = []
    for day in (1, 4):
        for i, code in enumerate(codes):
            row = dict.fromkeys(TRADE_COLUMNS, np.nan)
            row.update(
                model="B_trend",
                combo=-1,
                code=code,
                code_row=i,
                sig_bar=day - 1,
                sig_cal=day - 1,
                signal_date=str(dates[day - 1]),
                entry_cal=day,
                entry_date=str(dates[day]),
                entry_px=10.0,
                exit_bar=7,
                exit_cal=7,
                exit_date=str(dates[7]),
                exit_px=10.0,
                exit_at_close=True,
                unfinished=True,
                ret=0.0,
                rps120=100.0 - i,
                volume_bar=-1,
            )
            if day == 1 and i == 2:
                row.update(exit_cal=4, exit_px=9.0, exit_at_close=False, unfinished=False)
            if day == 1 and i == 3:
                row.update(exit_cal=4, exit_px=11.0, exit_at_close=True, unfinished=False)
            rows.append(row)
    offers = pd.DataFrame(rows).sort_values(
        ["entry_cal", "rps120", "code", "model"], ascending=[True, False, True, True]
    )
    cfg = StudyConfig()
    expected = book(offers, panel, cfg, "2022-01-01")
    path = tmp_path / "reject.csv"
    actual = stream_book(offers.to_dict("records"), panel, cfg, "2022-01-01", path)
    for attr in ("curve", "trades", "holdings", "contribution"):
        pd.testing.assert_frame_equal(getattr(actual, attr), getattr(expected, attr))
    pd.testing.assert_frame_equal(
        pd.read_csv(path), expected.rejected.reset_index(drop=True), check_dtype=False
    )


def test_empty_stream_has_constant_capital(tmp_path: Path) -> None:
    dates = pd.bdate_range("2022-01-03", periods=3).to_numpy().astype("datetime64[D]")
    panel = Panel(dates, np.array(["600000.SH"]), {"close": np.full((1, 3), 10.0)}, np.ones(3))
    cfg = StudyConfig()
    result = stream_book([], panel, cfg, "2022-01-01", tmp_path / "rejected.csv")
    assert (result.curve.equity == cfg.capital).all()
    assert result.trades.empty
