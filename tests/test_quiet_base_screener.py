import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rps_analyzer.quiet_base_screener import QuietBaseConfig, screen_quiet_base


# 默认参数 (base_window=42, recent_gap=20, drop_lookback=250) 下 n=320 的窗口切分:
#   下跌段 [8, 258)   基底期 [258, 300)   近期段 [300, 320)
N_BARS = 320
BASE_START, BASE_END = 258, 300
LIFT_START = 308            # 近期段内开始放量拉升的位置


def _daily_frame(code: str, closes: np.ndarray, vols: np.ndarray,
                 rps: np.ndarray) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": code,
            "open": closes * 0.998,
            "high": closes * 1.03,
            "low": closes * 0.99,
            "close": closes,
            "vol": vols,
            "amount": np.full(len(closes), 2e8),
            "RPS_120": rps,
        }
    )


def _base_oscillation(level: float) -> np.ndarray:
    """基底期: 围绕 level 窄幅震荡 ±1, 首尾基本齐平"""
    return level + np.sin(np.arange(BASE_END - BASE_START) * 0.5)


def _rps_turning_up() -> np.ndarray:
    """RPS_120 长期趴在 30, 近 20 日抬升到 55 (拐头)"""
    rps = np.full(N_BARS, 30.0)
    rps[BASE_END:] = np.linspace(32.0, 55.0, N_BARS - BASE_END)
    return rps


def _relaunch_closes(level: float = 60.0, top: float = 66.0) -> np.ndarray:
    """
    完整形态: 100 高位 -> 深跌到 level -> 基底期窄幅横盘
    -> 近期段先跌破基底(最后一跌, 明确处于 MA20 之下) -> 放量拉升到 top
    """
    closes = np.empty(N_BARS)
    closes[:100] = 100.0
    closes[100:BASE_START] = np.linspace(100.0, level, BASE_START - 100)
    closes[BASE_START:BASE_END] = _base_oscillation(level)
    closes[BASE_END:LIFT_START] = level - 1.0
    closes[LIFT_START:] = np.linspace(level + 0.5, top, N_BARS - LIFT_START)
    return closes


def _relaunch_vols(base_vol: float = 5e6, lift_vol: float = 12e6) -> np.ndarray:
    """下跌段大量 -> 基底期地量 -> 拉升段放量"""
    vols = np.empty(N_BARS)
    vols[:BASE_START] = 10e6
    vols[BASE_START:LIFT_START] = base_vol
    vols[LIFT_START:] = lift_vol
    return vols


def test_buy_when_relaunch_above_ma20_with_volume():
    daily = _daily_frame("600001.SH", _relaunch_closes(), _relaunch_vols(), _rps_turning_up())
    result = screen_quiet_base(daily, verbose=False)

    assert not result.empty
    row = result.iloc[0]
    assert row["SIGNAL"] == "可买入"
    assert bool(row["BUY"])
    assert row["vol_dry_x"] <= 0.70
    assert row["drawdown_pct"] >= 30.0
    assert row["rps_chg20"] >= 10.0
    assert 2 <= row["ma20_streak"] <= 25


def test_watch_when_still_inside_box_without_volume():
    closes = _relaunch_closes()
    # 近期段只在箱体上沿附近缓慢抬头, 既未放量也未真正启动
    closes[BASE_END:] = np.linspace(60.5, 62.0, N_BARS - BASE_END)

    vols = _relaunch_vols()
    vols[BASE_END:] = 4e6

    daily = _daily_frame("600002.SH", closes, vols, _rps_turning_up())
    result = screen_quiet_base(daily, verbose=False)

    assert not result.empty
    row = result.iloc[0]
    assert row["SIGNAL"] == "观察"
    assert not bool(row["BUY"])
    assert bool(row["WATCH"])
    assert row["vol_expand_x"] < 1.8


def test_excludes_high_level_consolidation_without_deep_drawdown():
    # 全程在 100 附近横盘, 从未经历 A 杀
    closes = _relaunch_closes(level=100.0, top=106.0)
    closes[:BASE_START] = 100.0

    daily = _daily_frame("600003.SH", closes, _relaunch_vols(), _rps_turning_up())
    result = screen_quiet_base(daily, verbose=False)

    assert result.empty


def test_excludes_base_that_is_not_volume_dry():
    # 基底期成交量并未萎缩 -> 筹码未清洗干净
    daily = _daily_frame(
        "600004.SH", _relaunch_closes(), _relaunch_vols(base_vol=9e6), _rps_turning_up()
    )
    result = screen_quiet_base(daily, verbose=False)

    assert result.empty


def test_excludes_when_rps_not_turning_up():
    flat_rps = np.full(N_BARS, 30.0)
    daily = _daily_frame("600005.SH", _relaunch_closes(), _relaunch_vols(), flat_rps)
    result = screen_quiet_base(daily, verbose=False)

    assert result.empty


def test_config_override_tightens_drawdown_gate():
    daily = _daily_frame("600006.SH", _relaunch_closes(), _relaunch_vols(), _rps_turning_up())

    # 该票回撤约 43%, 把门槛拉到 60% 后应被剔除
    result = screen_quiet_base(daily, config=QuietBaseConfig(min_drawdown=0.60), verbose=False)
    assert result.empty
