"""Offline synthetic invariants; no quotes, network, or broker connections."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from trend_start.dual_system import (
    GRID,
    Context,
    StudyConfig,
    a_layout,
    a_mask,
    b_features,
    book,
    calendar_return,
    load_prefix,
    mean,
    nonoverlap,
    retest,
    trade_rows,
)
from trend_start.engine import BUY_COST, StockBars, sell_cost
from trend_start.panel import Panel


def stock(n=300, code="600000.SH"):
    c = np.linspace(10, 13, n)
    dates = pd.bdate_range("2020-01-02", periods=n).to_numpy().astype("datetime64[D]")
    b = StockBars(code, np.arange(n), dates, c - 0.01, c + 0.02, c - 0.02, c, mean(c, 20))
    return Context(b, np.full(n, 100.0), np.full(n, 2e8), np.full(n, 90.0), 0)


def test_grid_has_only_allowed_axes():
    assert len(GRID) == 1296
    expected = [
        (20, 40, 60),
        (0.1, 0.15, 0.2),
        (10, 20, 40),
        (0.08, 0.12, 0.15),
        (0.6, 0.8),
        (1.5, 2.0),
        (3, 5),
        (0.03, 0.05),
    ]
    for i, allowed in enumerate(expected):
        assert set(p[i] for p in GRID) == set(allowed)


def test_config_extra_knobs_forbidden():
    with pytest.raises(ValidationError):
        StudyConfig(rps_min=95)


def test_relative_narrowing_is_an_explicit_gate():
    la = {
        "mature": np.array([True]),
        "decline_order": np.array([True]),
        "drop": np.array([0.1]),
        "base_range": np.array([0.12]),
        "decline_range": np.array([0.11]),
        "volume_ratio": np.array([0.5]),
        "expansion": np.array([2.1]),
        "right": np.array([True]),
    }
    assert not a_mask(la, (20, 0.1, 10, 0.12, 0.6, 2.0, 3, 0.03)).any()


def test_retest_is_later_actual_price_drop_and_floor_is_frozen():
    ctx = stock()
    b = ctx.bars
    b.ma20[:] = 10.0
    b.c[270:275] = [11.0, 11.0, 10.2, 10.1, 10.3]
    b.lo[270:275] = [10.9, 10.9, 10.05, 9.8, 1.0]
    ctx.vol[270:275] = [200.0, 100.0, 80.0, 70.0, 60.0]
    hit = retest(ctx, 270, 3, 0.03)
    assert hit == pytest.approx((272, 10.05))
    assert hit[0] > 270
    # The later low must not rewrite the confirmed floor.
    b.lo[273:] = 0.5
    assert retest(ctx, 270, 3, 0.03) == hit
    b.c[272:] = 11.0
    b.ma20[272:] = 11.0
    assert retest(ctx, 270, 3, 0.03) is None


def test_retest_close_below_ma20_invalidates_before_later_reclaim():
    ctx = stock()
    ctx.bars.ma20[:] = 10.0
    ctx.bars.c[270:274] = [11.0, 9.9, 10.1, 10.2]
    assert retest(ctx, 270, 3, 0.03) is None


def test_a_layout_and_b_features_do_not_read_future():
    ctx = stock()
    end = 280
    b0 = b_features(ctx, StudyConfig())
    a0 = a_layout(ctx, 60, 40)
    for field in ("c", "o", "h", "lo"):
        getattr(ctx.bars, field)[end + 1 :] *= 100
    ctx.vol[end + 1 :] = 1e9
    ctx.bars.ma20 = mean(ctx.bars.c, 20)
    b1 = b_features(ctx, StudyConfig())
    a1 = a_layout(ctx, 60, 40)
    pd.testing.assert_frame_equal(b0.iloc[: end + 1], b1.iloc[: end + 1])
    keep = a0["t"] <= end
    for field in a0:
        np.testing.assert_equal(a0[field][keep], a1[field][keep])


def test_b_signal_requires_quiet_actual_pullback_not_a_new_high():
    ctx = stock()
    t = 280
    c = ctx.bars.c
    c[t - 2] = c[t - 3] - 0.01
    c[t - 1] = c[t - 2] - 0.01
    c[t] = c[t - 1] + 0.04
    ctx.vol[t - 3 : t] = 50.0
    ctx.bars.o = c - 0.01
    ctx.bars.h = c + 0.02
    ctx.bars.lo = c - 0.02
    ctx.bars.ma20 = mean(c, 20)
    assert b_features(ctx, StudyConfig()).signal.iloc[t]
    ctx.vol[t - 3 : t] = 150.0
    assert not b_features(ctx, StudyConfig()).signal.iloc[t]


def test_market_calendar_return_does_not_use_next_traded_bar():
    ctx = stock(10)
    ctx.bars.cal = np.array([0, 1, 3, 4, 5, 6, 7, 8, 9, 10])
    assert np.isnan(calendar_return(ctx.bars, 1, 1))
    assert calendar_return(ctx.bars, 1, 5) == pytest.approx(ctx.bars.c[5] / ctx.bars.c[1] - 1)


def test_limit_up_entry_is_rejected_without_a_fill():
    ctx = stock(10)
    ctx.bars.o[2] = ctx.bars.c[1] * 1.1
    frame, rejected = trade_rows(
        ctx,
        [{"model": "A_retest", "combo": 0, "sig_bar": 1, "pocket_low": 9.0, "volume_bar": 0}],
        "2020-01-01",
    )
    assert frame.empty
    assert rejected.reason.iloc[0] == "limit_up_or_invalid_entry"


def test_pending_next_session_is_not_a_trade():
    ctx = stock(10)
    frame, rejected = trade_rows(
        ctx,
        [{"model": "B_trend", "combo": -1, "sig_bar": 9, "pocket_low": 9.0, "volume_bar": -1}],
        "2020-01-01",
    )
    assert frame.empty
    assert rejected.reason.iloc[0] == "pending_next_session"


def test_ma20_break_on_entry_closes_only_at_next_open():
    ctx = stock(10)
    ctx.bars.ma20[:] = 10.5
    ctx.bars.c[2] = 10.4
    frame, _ = trade_rows(
        ctx,
        [{"model": "A_retest", "combo": 0, "sig_bar": 1, "pocket_low": 9.0, "volume_bar": 0}],
        "2020-01-01",
    )
    assert frame.entry_cal.iloc[0] == 2
    assert frame.exit_cal.iloc[0] == 3


def test_prefix_loader_never_passes_future_prices_to_consumers(tmp_path):
    dates = np.array(["2021-12-30", "2021-12-31", "2022-01-04"], dtype="datetime64[D]")
    fields = {
        f: np.array([[10.0, 11.0, 999999.0]])
        for f in ("open", "high", "low", "close", "vol", "amount")
    }
    np.savez(
        tmp_path / "panel.npz",
        dates=dates,
        codes=np.array(["600000.SH"]),
        index_close=np.ones(3),
        **fields,
    )
    np.savez(tmp_path / "rps.npz", RPS120=np.array([[10.0, 20.0, 999999.0]]))
    panel, rps = load_prefix(tmp_path, "2021-12-31")
    assert panel.field("close").shape == (1, 2)
    assert panel.dates[-1] == dates[1]
    assert rps.max() == 20


def test_nonoverlap_does_not_count_multiple_signals_in_one_position():
    frame = pd.DataFrame(
        {
            "model": ["A_retest"] * 3,
            "code": ["A"] * 3,
            "combo": [0] * 3,
            "sig_bar": [1, 2, 6],
            "volume_bar": [0, 0, 5],
            "sig_cal": [1, 2, 6],
            "exit_bar": [5, 5, 8],
            "unfinished": [False, False, True],
        }
    )
    assert list(nonoverlap(frame).sig_bar) == [1, 6]


def test_book_cap_priority_cash_and_contribution_conservation():
    n = 25
    dates = pd.bdate_range("2020-01-02", periods=5).to_numpy().astype("datetime64[D]")
    data = {f: np.full((n, 5), 10.0) for f in ("open", "high", "low", "close", "vol", "amount")}
    panel = Panel(dates, np.array([f"{i:06d}.SZ" for i in range(n)]), data, np.ones(5))
    frame = pd.DataFrame(
        [
            {
                "code": str(panel.codes[i]),
                "code_row": i,
                "model": "B_trend",
                "combo": -1,
                "sig_cal": 0,
                "entry_cal": 1,
                "entry_px": 10.0,
                "exit_cal": 3,
                "exit_px": 10.0,
                "exit_at_close": False,
                "unfinished": False,
                "rps120": i,
            }
            for i in range(n)
        ]
    )
    result = book(frame, panel, StudyConfig(), "2020-01-02")
    assert len(result.trades) == 20
    assert set(result.trades.code) == set(panel.codes[5:])
    assert result.curve.holding_count.max() == 20
    assert (result.curve.cash_before_boundary_mark >= -1e-6).all()
    assert len(result.rejected) == 5
    assert result.contribution.pnl.sum() == pytest.approx(result.curve.equity.iloc[-1] - 1e6)
    expected = 1e6 * (1 - sell_cost(dates)[3]) / (1 + BUY_COST)
    assert result.curve.equity.iloc[-1] == pytest.approx(expected)


def test_terminal_fee_is_in_curve_and_tagged_as_boundary_mark():
    dates = pd.bdate_range("2020-01-02", periods=3).to_numpy().astype("datetime64[D]")
    data = {f: np.full((1, 3), 10.0) for f in ("open", "high", "low", "close", "vol", "amount")}
    panel = Panel(dates, np.array(["600000.SH"]), data, np.ones(3))
    frame = pd.DataFrame(
        [
            {
                "code": "600000.SH",
                "code_row": 0,
                "model": "A_retest",
                "sig_cal": 0,
                "entry_cal": 1,
                "entry_px": 10.0,
                "exit_cal": 2,
                "exit_px": 10.0,
                "exit_at_close": True,
                "unfinished": True,
                "rps120": 90.0,
            }
        ]
    )
    result = book(frame, panel, StudyConfig(), "2020-01-02")
    assert result.trades.boundary_mark.iloc[0]
    assert result.curve.boundary_valuation_cost.iloc[-1] > 0
    assert result.curve.equity.iloc[-1] < result.curve.gross_equity.iloc[-1]
    assert result.trades.pnl.sum() == pytest.approx(result.curve.equity.iloc[-1] - 1e6)


def test_cli_period_exports_empty_models_with_headers_and_real_control(tmp_path, monkeypatch):
    import run_dual_research as cli

    cache = tmp_path / "cache"
    cache.mkdir()
    ctx = stock()
    b = ctx.bars
    fields = {
        "open": b.o[None, :],
        "high": b.h[None, :],
        "low": b.lo[None, :],
        "close": b.c[None, :],
        "vol": ctx.vol[None, :],
        "amount": ctx.amount[None, :],
    }
    np.savez(
        cache / "panel.npz",
        dates=b.dates,
        codes=np.array([b.code]),
        index_close=np.ones(len(b.c)),
        **fields,
    )
    np.savez(cache / "rps.npz", RPS120=ctx.rps[None, :])
    monkeypatch.setattr(cli, "CACHE", cache)
    summary = cli.period(tmp_path / "result", str(b.dates[-1]), "2020-01-02", None, StudyConfig())
    assert len(summary) == 5
    empty = pd.read_csv(tmp_path / "result/A_retest/portfolio_trades.csv")
    assert empty.empty and "pnl" in empty.columns
    assert (tmp_path / "result/latest_candidates.csv").exists()
    assert summary.loc[summary.model == "control", "portfolio_trades"].iloc[0] > 0


def test_old_audit_uses_open_suffix_exit_column(tmp_path, monkeypatch):
    import run_dual_research as cli

    old = tmp_path / "output/daily_system"
    old.mkdir(parents=True)
    pd.DataFrame({"code": ["A", "B"], "sig_cal": [1, 2], "exit_cal_open": [3, 2]}).to_csv(
        old / "trades_strategy.csv", index=False
    )
    pd.DataFrame({"代码": ["A", "B"], "信号日": ["2021-12-30", "2021-12-31"]}).to_csv(
        old / "历史对照.csv", index=False
    )
    out = tmp_path / "audit"
    out.mkdir()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    cli.audit_old(out)
    leaked = pd.read_csv(out / "old_boundary_leaks.csv")
    assert list(leaked.code) == ["A"]
