"""Targeted tests of parsing, missingness and the reused adjustment contract."""
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repair_common", ROOT / "research/rps_repair_20261006/common.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_raw_units_and_dates(tmp_path):
    path = tmp_path / "x.day"
    path.write_bytes(struct.pack("<5If2I", 20230103, 1000, 1200, 900, 1100, 123456.0, 78900, 0))
    row = M.read_raw(path).iloc[0]
    assert row.close == 11 and row.vol == 78900 and row.amount == 123456


def test_duplicate_dates_rejected(tmp_path):
    path = tmp_path / "x.day"
    row = struct.pack("<5If2I", 20230103, 1000, 1200, 900, 1100, 123456.0, 78900, 0)
    path.write_bytes(row + row)
    with pytest.raises(ValueError, match="Duplicate"):
        M.read_raw(path)


def test_missing_prices_not_equal():
    a = pd.DataFrame({"close": [1.0, np.nan]})
    b = pd.DataFrame({"close": [1.0, np.nan]})
    result = M.comparison(a, b, ["close"], rtol=1e-5, atol=1e-5)
    assert result["nonfinite_cells"] == result["mismatched_cells"] == 1


def test_unaligned_dates_rejected():
    with pytest.raises(ValueError, match="identical"):
        M.comparison(pd.DataFrame({"close": [1]}, index=[1]), pd.DataFrame({"close": [1]}, index=[2]), ["close"], rtol=1e-5, atol=1e-5)


def test_calendar_resolution_is_not_date_mismatch():
    dates = pd.DatetimeIndex(["2023-01-03", "2023-01-04"])
    a = pd.DataFrame({"close": [1., 2.]}, index=dates.as_unit("s"))
    b = pd.DataFrame({"close": [1., 2.]}, index=dates.as_unit("ns"))
    assert M.comparison(a, b, ["close"], rtol=1e-5, atol=1e-5)["mismatched_cells"] == 0


def adjustment_fixture():
    protocol = json.loads((M.HERE / "protocol.json").read_text(encoding="utf-8"))
    adjust = M.load_registered_adjust(protocol)
    raw = pd.DataFrame({"trade_date": pd.to_datetime(["2023-01-03", "2023-01-04", "2023-01-05"]),
                        **{f: [10.0, 9.0, 9.5] for f in M.PRICE}})
    events = pd.DataFrame({"date": pd.to_datetime(["2023-01-04"]), "category": [1],
                          **{f: [10.0 if i == 0 else 0.0] for i, f in enumerate(M.EVENT_FIELDS)}})
    return adjust, raw, events


def test_cash_dividend_adjustment_keeps_last_anchor():
    adjust, raw, events = adjustment_fixture()
    adjusted, n = adjust(raw, events, pd.Timestamp("2023-01-05"), bars=None)
    assert n == 1 and adjusted.close.tolist() == [9.0, 9.0, 9.5]


def test_unsupported_action_is_not_guessed():
    adjust, raw, events = adjustment_fixture()
    events.category = 11
    with pytest.raises(ValueError, match="unsupported"):
        adjust(raw, events, pd.Timestamp("2023-01-05"), bars=None)


def test_orphan_action_rejected():
    adjust, raw, events = adjustment_fixture()
    events.date = pd.to_datetime(["2023-01-06"])
    with pytest.raises(ValueError, match="following bar"):
        adjust(raw, events, pd.Timestamp("2023-01-06"), bars=None)


def test_registered_protocol_drift_rejected(tmp_path, monkeypatch):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    path = source / "protocol.json"
    path.write_text(json.dumps({"input_hashes": {}}), encoding="utf-8")
    (output / "registration.json").write_text(json.dumps({"protocol_sha256": M.sha(path)}), encoding="utf-8")
    monkeypatch.setattr(M, "HERE", source)
    monkeypatch.setattr(M, "OUT", output)
    assert M.guard()["input_hashes"] == {}
    path.write_text(json.dumps({"input_hashes": {}, "changed": True}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="protocol changed"):
        M.guard()
