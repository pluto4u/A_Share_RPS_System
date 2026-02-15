from datetime import datetime

import market_gambler_watch as mgw


def test_is_trading_session_during_morning():
    now = datetime(2026, 2, 13, 10, 0)  # Friday
    assert mgw.is_trading_session(now) is True


def test_is_trading_session_lunch_break():
    now = datetime(2026, 2, 13, 12, 0)  # Friday lunch break
    assert mgw.is_trading_session(now) is False


def test_is_trading_session_weekend():
    now = datetime(2026, 2, 14, 10, 0)  # Saturday
    assert mgw.is_trading_session(now) is False


def test_run_loop_skips_non_session(monkeypatch):
    calls = {"run_once": 0, "sleep": 0}

    monkeypatch.setattr(mgw, "is_trading_session", lambda _now=None: False)
    monkeypatch.setattr(
        mgw,
        "run_once",
        lambda full=False, feishu=False: calls.__setitem__("run_once", calls["run_once"] + 1),
    )
    monkeypatch.setattr(
        mgw.time,
        "sleep",
        lambda _sec: calls.__setitem__("sleep", calls["sleep"] + 1),
    )

    mgw.run_loop(full=False, feishu=False, interval_minutes=15, session_only=True, max_loops=1)
    assert calls["run_once"] == 0
    assert calls["sleep"] == 0


def test_run_loop_runs_in_session(monkeypatch):
    calls = {"run_once": 0}

    monkeypatch.setattr(mgw, "is_trading_session", lambda _now=None: True)
    monkeypatch.setattr(
        mgw,
        "run_once",
        lambda full=False, feishu=False: calls.__setitem__("run_once", calls["run_once"] + 1),
    )
    monkeypatch.setattr(mgw.time, "sleep", lambda _sec: None)

    mgw.run_loop(full=True, feishu=True, interval_minutes=15, session_only=True, max_loops=1)
    assert calls["run_once"] == 1
