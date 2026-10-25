"""Offline combined-pool boundaries, causal events, and daily reference checks."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from rps_analyzer.right_confirmation_v2 import LayeredConfig, shape_series
from run_combined_daily import (
    CombinedConfig,
    assign_pool,
    current_turnover,
    daily_plan,
    recent_event,
    report,
    sha,
    validate_snapshot,
)
from run_daily_rps import Settings, daily_features, rank_then_filter


def bars() -> pd.DataFrame:
    c = np.linspace(10, 40, 320)
    c[-16:] = 40
    d = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-01", periods=320),
            "open": c,
            "close": c,
            "high": c + 1,
            "low": c - 1,
            "vol": np.full(320, 1e7),
            "amount": np.full(320, 2e8),
        }
    )
    d.loc[314:318, ["high", "low", "vol"]] = [40.4, 39.6, 5e6]
    d.loc[319, ["close", "high", "low", "vol"]] = [42, 42.1, 40, 1.5e7]
    return d


@pytest.mark.parametrize(
    "strict,v2,event,expected",
    [
        (True, True, True, "趋势刚启动"),
        (False, True, True, "趋势刚启动"),
        (True, None, True, "趋势进行中"),
        (False, None, True, "未入选"),
        (True, True, False, "趋势进行中"),
        (False, True, False, "未入选"),
    ],
)
def test_pools_are_exclusive_and_setup_is_not_start(
    strict: bool,
    v2: bool | None,
    event: bool,
    expected: str,
) -> None:
    assert assign_pool(strict, v2, event) == expected


def test_event_uses_frozen_pre_event_pivot_not_today_high() -> None:
    d = bars()
    event = recent_event(d, shape_series(d, LayeredConfig()), CombinedConfig())
    assert event["近5日突破尚有效"] is True
    assert event["冻结突破位"] == 41
    assert event["冻结整理低点"] == 39.6
    assert event["距突破交易日"] == 0
    assert event["突破日量比"] == pytest.approx(1.5e7 / d.vol.iloc[-21:-1].mean())


def test_breakout_failed_then_recovered_not_persisted_as_valid() -> None:
    d = bars()
    extra = d.tail(2).copy()
    extra["trade_date"] = pd.bdate_range(d.trade_date.iloc[-1] + pd.Timedelta(days=1), periods=2)
    extra["close"] = [40.8, 42]
    extra["high"] = [42, 43]
    extra["low"] = [40, 40]
    d = pd.concat([d, extra], ignore_index=True)
    event = recent_event(d, shape_series(d, LayeredConfig()), CombinedConfig())
    assert event["近5日突破尚有效"] is False


def test_event_age_five_is_not_in_latest_five_bars() -> None:
    d = bars()
    s = shape_series(d, LayeredConfig())
    s["breakout"][:] = False
    s["breakout"].iloc[-6] = True
    assert recent_event(d, s, CombinedConfig())["近5日突破尚有效"] is False


def test_next_day_ma20_price_is_algebraically_exact_and_missing_low_is_not_zero() -> None:
    d = bars()
    row = {"分组": "趋势进行中", "口袋低点": None, "ATR20昨日": 2, "价格口径": "raw"}
    plan = daily_plan(d, row, CombinedConfig())
    boundary = plan["下一日MA20穿越临界收盘"]
    for close in [boundary - 0.01, boundary, boundary + 0.01]:
        new_ma = (d.close.tail(19).sum() + close) / 20
        assert (close < new_ma) == (close < boundary)
    assert "低点缺失" in plan["形态失效条件"]
    assert plan["MA20观察带上沿"] == pytest.approx(d.close.tail(20).mean() * 1.02)
    assert plan["当前形态失效"] is False


def test_starting_invalidation_is_its_own_event_pivot() -> None:
    d = bars()
    row = {
        "分组": "趋势刚启动",
        "口袋低点": 38.0,
        "ATR20昨日": 2,
        "价格口径": "raw",
        "冻结突破位": 41.0,
        "冻结整理低点": 39.6,
    }
    plan = daily_plan(d, row, CombinedConfig())
    assert "收盘<41.00" in plan["形态失效条件"]
    assert "收盘<39.60" in plan["形态失效条件"]
    for forbidden in ("买入", "卖出", "加仓", "清仓", "首仓"):
        assert forbidden not in plan["日线观察方案"] + plan["形态失效条件"]


def make_snapshot(tmp_path: Path) -> Path:
    source = tmp_path / "run"
    source.mkdir()
    root = tmp_path / "tdx"
    cache = root / "T0002/hq_cache"
    cache.mkdir(parents=True)
    file = root / "sample.day"
    file.write_bytes(b"fixture only, validate_snapshot does not parse this file")
    (cache / "shs.tnf").write_bytes(b"names")
    d = bars()
    values = {
        "代码": "600001.SH",
        "数据日期": str(d.trade_date.iloc[-1].date()),
        "价格口径": "raw",
        **daily_features(d, Settings()),
    }
    rows = rank_then_filter(pd.DataFrame([values]), Settings())
    rows.to_csv(source / "all_valid.csv", index=False)
    pd.DataFrame(
        [{"代码": "600001.SH", "status": "VALID", "path": str(file), "sha256": sha(file)}]
    ).to_csv(source / "inventory.csv", index=False)
    audit: dict[str, Any] = {
        "status": "OK",
        "errors": 0,
        "price_basis": "raw",
        "valid_universe": 1,
        "as_of": values["数据日期"],
        "parameters": Settings().model_dump(),
        "root": str(root),
        "source_hashes": {"shs.tnf": sha(cache / "shs.tnf")},
    }
    (source / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return source


@pytest.mark.parametrize("issue", ["none", "denominator", "rank", "date", "basis", "file"])
def test_snapshot_validation_detects_changed_inputs_and_mixed_basis(
    tmp_path: Path,
    issue: str,
) -> None:
    source = make_snapshot(tmp_path)
    d = pd.read_csv(source / "all_valid.csv")
    if issue == "none":
        _, rows, _ = validate_snapshot(source)
        assert len(rows) == 1
        return
    if issue == "file":
        (tmp_path / "tdx/sample.day").write_bytes(b"changed")
    else:
        field, value = {
            "denominator": ("RPS分母", 2),
            "rank": ("RPS250", 1),
            "date": ("数据日期", "2025-01-01"),
            "basis": ("价格口径", "qfq"),
        }[issue]
        d.loc[0, field] = value
        d.to_csv(source / "all_valid.csv", index=False)
    with pytest.raises(ValueError):
        validate_snapshot(source)


def test_empty_start_report_does_not_invent_names() -> None:
    text = report(
        "趋势刚启动",
        pd.DataFrame(),
        {"as_of": "2026-09-29", "denominator": 4923, "price_basis": "raw", "coverage_gaps": []},
    )
    assert "共 **0只**" in text
    assert "未放宽规则" in text


@pytest.mark.parametrize("bonus,rights,missing", [(0, 0, False), (1, 0, True), (0, 1, True)])
def test_turnover_not_trusted_after_bonus_or_rights(bonus: int, rights: int, missing: bool) -> None:
    events = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-09-28")],
            "category": [1],
            "songgu_qianzongguben": [bonus],
            "peigu_houzongguben": [rights],
        }
    )
    value, reason = current_turnover(
        1000, {"shares": 100000, "record_date": 20260901}, events, pd.Timestamp("2026-09-29")
    )
    assert (value is None) is missing
    assert bool(reason) is missing
    if not missing:
        assert value == 1.0
