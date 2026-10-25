"""Offline independent checks for full-universe RPS and binary daily parsing."""

import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from run_daily_rps import Settings, daily_features, discover, main, rank_then_filter, read_day


def history(n: int = 320) -> pd.DataFrame:
    c = np.linspace(10, 40, n)
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-01", periods=n),
            "open": c,
            "high": c + 0.2,
            "low": c - 0.2,
            "close": c,
            "vol": np.full(n, 1000.0),
            "amount": c * 1000,
        }
    )


def test_binary_price_amount_volume_and_reserved(tmp_path: Path) -> None:
    path = tmp_path / "sh600001.day"
    path.write_bytes(struct.pack("<IIIIIfII", 20260924, 1000, 1200, 900, 1100, 123450.0, 12345, 99))
    row = read_day(path).iloc[0]
    assert row.close == 11
    assert row.vol == 12345  # shares, never multiplied by 100
    assert row.amount == 123450


@pytest.mark.parametrize("kind", ["partial", "date", "duplicate", "ohlc", "empty"])
def test_corruption_is_error(tmp_path: Path, kind: str) -> None:
    b = struct.pack("<IIIIIfII", 20260924, 1000, 1200, 900, 1100, 11000.0, 1000, 0)
    if kind == "partial":
        b += b"x"
    elif kind == "date":
        b = struct.pack("<I", 20261399) + b[4:]
    elif kind == "duplicate":
        b += b
    elif kind == "ohlc":
        b = b[:8] + struct.pack("<I", 800) + b[12:]
    else:
        b = b""
    path = tmp_path / "broken.day"
    path.write_bytes(b)
    with pytest.raises(ValueError):
        read_day(path)


def test_return250_requires_251_prices() -> None:
    with pytest.raises(ValueError):
        daily_features(history(250), Settings())
    data = history(251)
    row = daily_features(data, Settings())
    assert row["return250"] == pytest.approx(data.close.iloc[-1] / data.close.iloc[0] - 1)


def test_drawdown_uses_chronological_peaks_and_volume_proxy() -> None:
    d = history()
    d.loc[d.index[-20:], "close"] = [40, 38, 36, 39, 42] + [42] * 15
    d["low"] = d.close - 0.2
    d["high"] = d.close + 0.2
    d.loc[d.close.diff() < 0, "vol"] = 500
    result = daily_features(d, Settings())
    assert result["20日最大回撤%"] == pytest.approx(10)  # NOT (42-36)/42
    assert result["回调段日均量"] == 500
    assert result["上涨段日均量"] == 1000
    assert result["口袋低点"] == 35.8
    assert result["缩量回调"] is True


def test_no_pullback_is_missing_not_an_invented_low() -> None:
    result = daily_features(history(), Settings())
    assert result["口袋低点"] is None
    assert result["回调段日均量"] is None
    assert result["缩量回调"] is False
    assert result["最近一次跌破MA20距今天数"] is None


def test_downcross_age_is_last_cross_not_last_below_day() -> None:
    d = history()
    d.loc[d.index[-3:], "close"] = [10, 11, 12]
    assert daily_features(d, Settings())["最近一次跌破MA20距今天数"] == 2


def test_rank_denominator_precedes_all_trend_filters_and_ties_average() -> None:
    base = {
        "均线多头": True,
        "MA60向上": True,
        "距52周高点%": 5,
        "距52周低点%": 50,
        "20日最大回撤%": 5,
        "缩量回调": True,
    }
    d = pd.DataFrame(
        [
            {**base, "代码": str(i), "return50": v, "return120": v, "return250": v}
            for i, v in enumerate([0.1, 0.2, 0.2, 0.4])
        ]
    )
    d.loc[3, "均线多头"] = False
    ranked = rank_then_filter(d, Settings(rps120_min=60, rps250_min=60)).set_index("代码")
    assert ranked.loc["1", "RPS250"] == 62.5  # rank 2.5 / N=4; NOT reranked in 3 trend names
    assert ranked.loc["3", "RPS250"] == 100
    assert not ranked.loc["3", "入选"]
    assert ranked.loc["1", "入选"]
    assert ranked.RPS分母.eq(4).all()


def test_duplicate_universe_is_not_ranked() -> None:
    with pytest.raises(ValueError):
        rank_then_filter(pd.DataFrame({"代码": ["a", "a"]}), Settings())


def test_discover_nested_vipdoc(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "tdx"
    for market in ("sh", "sz"):
        (root / "vipdoc" / market / "lday").mkdir(parents=True)
    found, errors = discover(tmp_path)
    assert found == [root]
    assert errors == []


def write_fixture_day(path: Path, data: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(
            struct.pack(
                "<IIIIIfII",
                int(r.trade_date.strftime("%Y%m%d")),
                round(r.open * 100),
                round(r.high * 100),
                round(r.low * 100),
                round(r.close * 100),
                float(r.amount),
                int(r.vol),
                0,
            )
            for r in data.itertuples()
        )
    )


@pytest.mark.parametrize("broken", [False, True])
def test_cli_raw_has_no_gbbq_dependency_and_blocks_corrupt_universe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: bool,
) -> None:
    import json

    tdx = tmp_path / "tdx"
    cache = tdx / "T0002/hq_cache"
    cache.mkdir(parents=True)
    rec = bytearray(360)
    rec[:6] = b"600001"
    name = "测试股票".encode("gbk")
    rec[31 : 31 + len(name)] = name
    (cache / "shs.tnf").write_bytes(bytes(50) + rec)
    (cache / "szs.tnf").write_bytes(bytes(410))
    (tdx / "vipdoc/sz/lday").mkdir(parents=True)
    data = history()
    write_fixture_day(tdx / "vipdoc/sh/lday/sh000001.day", data)
    write_fixture_day(tdx / "vipdoc/sh/lday/sh600001.day", data)
    # An old, unnamed legacy security is an explicit stale exclusion, not an ST guess.
    write_fixture_day(tdx / "vipdoc/sh/lday/sh600002.day", data.head(20))
    if broken:
        (tdx / "vipdoc/sh/lday/sh600003.day").write_bytes(b"corrupt")
    output = tmp_path / "results"
    monkeypatch.setattr(
        "sys.argv", ["run_daily_rps.py", "--tdx-root", str(tdx), "--output", str(output)]
    )
    assert main() == (2 if broken else 0)
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    assert audit["valid_universe"] == 1
    assert audit["price_basis"] == "raw"
    assert audit["errors"] == int(broken)
    assert (output / "selected.csv").exists() is not broken
    inventory = pd.read_csv(output / "inventory.csv")
    assert inventory.set_index("代码").loc["600002.SH", "status"] == "EXCLUDED"
    if not broken:
        ranks = pd.read_csv(output / "all_valid.csv")
        assert ranks.RPS250.iloc[0] == 100
        assert ranks.RPS分母.iloc[0] == 1
