from __future__ import annotations

import numpy as np
import pandas as pd

from rps_analyzer.ma20_screener import Ma20Config, resolve_target_date, screen_ma20


def _frame(code: str, closes: np.ndarray, vols: np.ndarray | None = None) -> pd.DataFrame:
    count = len(closes)
    dates = pd.bdate_range("2026-01-01", periods=count)
    opens = closes * 0.998
    if vols is None:
        vols = np.full(count, 1_000_000.0)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": code,
            "open": opens,
            "high": np.maximum(opens, closes) * 1.01,
            "low": np.minimum(opens, closes) * 0.99,
            "close": closes,
            "vol": vols,
            "amount": closes * vols,
        }
    )


def _loose_config(**kwargs) -> Ma20Config:
    values = {
        "min_amount20": 1e6,
        "min_bars": 80,
        "max_ret1": 0.10,
        "max_ret5": 0.25,
        "max_ret60": 2.0,
    }
    values.update(kwargs)
    return Ma20Config(**values)


def test_selects_volume_reclaim_of_ma20():
    closes = np.linspace(10.0, 20.0, 100)
    closes[-2] = 18.85
    closes[-1] = 19.60
    vols = np.full(100, 1_000_000.0)
    vols[-1] = 1_700_000.0
    daily = _frame("600001.SH", closes, vols)
    daily.loc[daily.index[-1], "open"] = 19.40

    result = screen_ma20(daily, config=_loose_config(), verbose=False)

    assert len(result) == 1
    assert result.iloc[0]["signal"] == "放量收复MA20"
    assert result.iloc[0]["action"] == "候选"


def test_selects_low_volume_pullback_to_ma20():
    closes = np.linspace(10.0, 20.0, 100)
    closes[-10:-2] = np.array([20.10, 20.30, 20.50, 20.35, 20.20, 20.05, 19.95, 19.85])
    closes[-2] = 19.45
    closes[-1] = 19.55
    vols = np.full(100, 1_000_000.0)
    vols[-1] = 600_000.0
    daily = _frame("600002.SH", closes, vols)
    daily.loc[daily.index[-1], ["open", "low", "high"]] = [19.40, 19.25, 19.70]

    result = screen_ma20(daily, config=_loose_config(), verbose=False)

    assert len(result) == 1
    assert result.iloc[0]["signal"] == "缩量回踩MA20"


def test_excludes_falling_ma20_and_ma20_below_ma60():
    closes = np.linspace(20.0, 10.0, 100)
    daily = _frame("600003.SH", closes)

    result = screen_ma20(daily, config=_loose_config(), verbose=False)

    assert result.empty


def test_excludes_stale_and_invalid_codes_but_keeps_good_data():
    good_frames = [
        _frame(f"6000{i:02d}.SH", np.linspace(10.0, 20.0, 100)) for i in range(10, 20)
    ]
    stale = _frame("600005.SH", np.linspace(10.0, 20.0, 99))
    bad = _frame("600006.SH", np.linspace(10.0, 20.0, 100))
    bad.loc[bad.index[-5], "high"] = bad.loc[bad.index[-5], "low"] - 1.0
    daily = pd.concat([*good_frames, stale, bad], ignore_index=True)

    result = screen_ma20(daily, config=_loose_config(), verbose=False)
    audit = result.attrs["audit"]

    assert audit["stale_codes_excluded"] == 1
    assert audit["invalid_codes_excluded"] == 1
    assert audit["quality_pass_stocks"] == 10


def test_target_date_ignores_isolated_future_record():
    daily = pd.concat(
        [
            _frame("600010.SH", np.linspace(10.0, 20.0, 100)),
            _frame("600011.SH", np.linspace(10.0, 20.0, 100)),
            _frame("600012.SH", np.linspace(10.0, 20.0, 100)),
        ],
        ignore_index=True,
    )
    isolated = daily.iloc[[-1]].copy()
    isolated["ts_code"] = "600999.SH"
    isolated["trade_date"] = pd.Timestamp("2030-01-01")
    daily = pd.concat([daily, isolated], ignore_index=True)

    resolved = resolve_target_date(daily, coverage_ratio=0.90, lookback=10)

    assert resolved == pd.bdate_range("2026-01-01", periods=100)[-1]


def test_accepts_string_trade_dates():
    daily = _frame("600020.SH", np.linspace(10.0, 20.0, 100))
    daily["trade_date"] = daily["trade_date"].dt.strftime("%Y%m%d")

    result = screen_ma20(daily, config=_loose_config(), verbose=False)

    assert result.attrs["audit"]["quality_pass_stocks"] == 1
