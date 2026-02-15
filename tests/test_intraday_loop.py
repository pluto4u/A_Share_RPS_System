from datetime import datetime

import market_gambler_watch as mgw


# ── trading_minutes_elapsed 测试 ──

def test_minutes_elapsed_morning_open():
    """09:30 刚开盘 → 0 分钟（clamped 到 1）"""
    now = datetime(2026, 2, 16, 9, 30)
    assert mgw.trading_minutes_elapsed(now) == 1


def test_minutes_elapsed_morning_mid():
    """10:30 → 开盘后 60 分钟"""
    now = datetime(2026, 2, 16, 10, 30)
    assert mgw.trading_minutes_elapsed(now) == 60


def test_minutes_elapsed_lunch():
    """12:00 午休 → 上午满额 120 分钟"""
    now = datetime(2026, 2, 16, 12, 0)
    assert mgw.trading_minutes_elapsed(now) == 120


def test_minutes_elapsed_afternoon_mid():
    """14:00 → 上午120 + 下午60 = 180 分钟"""
    now = datetime(2026, 2, 16, 14, 0)
    assert mgw.trading_minutes_elapsed(now) == 180


def test_minutes_elapsed_close():
    """15:00 收盘 → 满额 240 分钟"""
    now = datetime(2026, 2, 16, 15, 0)
    assert mgw.trading_minutes_elapsed(now) == 240


def test_minutes_elapsed_after_close():
    """17:00 盘后 → 上限 240"""
    now = datetime(2026, 2, 16, 17, 0)
    assert mgw.trading_minutes_elapsed(now) == 240


# ── fetch_spot RuntimeError 测试 ──

def test_fetch_spot_raises_on_empty(monkeypatch):
    """fetch_spot 数据为空时应抛 RuntimeError 而非 sys.exit"""
    import pandas as pd
    monkeypatch.setattr(mgw, "safe_call", lambda *a, **kw: pd.DataFrame())
    try:
        mgw.fetch_spot()
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "无法获取" in str(e)
    except SystemExit:
        assert False, "Should NOT call sys.exit anymore"


# ── is_trading_session 测试 ──

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
