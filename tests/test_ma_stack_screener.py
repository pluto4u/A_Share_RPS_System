from __future__ import annotations

import numpy as np
import pandas as pd

from rps_analyzer.ma_stack_screener import MaStackConfig, screen_ma_stack
from run_ma_stack_screener import _format_code


def _frame(code: str, closes: np.ndarray) -> pd.DataFrame:
    count = len(closes)
    dates = pd.bdate_range("2026-01-01", periods=count)
    opens = closes * 0.998
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


def test_selects_close_above_all_moving_averages():
    rising = _frame("600001.SH", np.linspace(10.0, 20.0, 60))
    falling = _frame("600002.SH", np.linspace(20.0, 10.0, 60))
    daily = pd.concat([rising, falling], ignore_index=True)

    result = screen_ma_stack(daily, verbose=False)

    assert result["ts_code"].tolist() == ["600001.SH"]
    assert result.attrs["audit"]["above_all_ma"] == 1


def test_excludes_stock_below_ma5_only():
    closes = np.linspace(10.0, 20.0, 60)
    closes[-1] = closes[-2] - 0.60  # 收盘仍在 MA20 上，但已跌破 MA5
    daily = _frame("600003.SH", closes)

    result = screen_ma_stack(daily, verbose=False)
    only_ma20 = screen_ma_stack(daily, config=MaStackConfig(ma_periods=(20,)), verbose=False)

    assert result.empty
    assert only_ma20["ts_code"].tolist() == ["600003.SH"]


def test_flags_and_drops_ex_rights_gap():
    closes = np.linspace(10.0, 20.0, 60)
    clean = _frame("600004.SH", closes)
    split = _frame("600005.SH", closes.copy())
    # 第 55 根 K 线 10 送 10 式除权：价格腰斩，跳空远超 10% 涨跌幅限制
    index = split.index[-5:]
    for column in ("open", "high", "low", "close"):
        split.loc[index, column] = split.loc[index, column] / 2.0
    daily = pd.concat([clean, split], ignore_index=True)

    guarded = screen_ma_stack(daily, verbose=False)
    kept = screen_ma_stack(daily, config=MaStackConfig(ex_rights_guard=False), verbose=False)

    assert guarded["ts_code"].tolist() == ["600004.SH"]
    assert guarded.attrs["audit"]["ex_rights_suspect_excluded"] == 0
    assert kept.attrs["audit"]["above_all_ma"] == 1
    assert "600005.SH" not in kept["ts_code"].tolist()


def test_require_ma_order_is_stricter_than_above_all():
    # 上涨后深调再反弹：收盘已站上三线，但 MA5 尚未回到 MA10 上方
    dip = np.array([12.6, 12.2, 11.8, 11.5, 11.3, 11.2, 11.4, 12.0, 12.6])
    closes = np.concatenate([np.linspace(9.0, 13.0, 45), dip])
    daily = _frame("600006.SH", closes)

    loose = screen_ma_stack(daily, verbose=False)
    strict = screen_ma_stack(daily, config=MaStackConfig(require_ma_order=True), verbose=False)

    assert loose["ts_code"].tolist() == ["600006.SH"]
    assert loose.iloc[0]["ma_order_ok"] == strict.attrs["audit"]["ma_order_ok"] == 0
    assert strict.empty


def test_liquidity_filter_and_target_date_fallback():
    daily = pd.concat(
        [_frame(f"6000{i:02d}.SH", np.linspace(10.0, 20.0, 60)) for i in range(10, 15)],
        ignore_index=True,
    )
    last_trading_day = pd.bdate_range("2026-01-01", periods=60)[-1]
    weekend = last_trading_day + pd.Timedelta(days=1)

    result = screen_ma_stack(daily, target_date=weekend, verbose=False)
    filtered = screen_ma_stack(daily, config=MaStackConfig(min_amount20=1e9), verbose=False)

    assert result.attrs["audit"]["target_date"] == last_trading_day.strftime("%Y-%m-%d")
    assert len(result) == 5
    assert filtered.empty


def test_code_format_conversion():
    assert _format_code("600519.SH", "plain") == "600519"
    assert _format_code("000001.sz", "ts") == "000001.SZ"
    assert _format_code("600519.SH", "tdx") == "1600519"
    assert _format_code("000001.SZ", "tdx") == "0000001"
