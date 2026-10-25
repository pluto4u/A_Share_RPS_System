import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "research/etf_forward_20261006/forward.py"
SPEC = importlib.util.spec_from_file_location("etf_forward", SCRIPT)
forward = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forward)


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def test_holiday_cutoff_and_first_monthend():
    current = utc("2026-10-06T11:00:00+08:00")
    assert forward.expected_cutoff(current) == pd.Timestamp("2026-09-30")
    started = "2026-10-06T03:00:00+00:00"
    assert not forward.monthend_gate(current, pd.Timestamp("2026-09-30"), started)[0]
    close = utc("2026-10-30T16:00:00+08:00")
    assert forward.monthend_gate(close, pd.Timestamp("2026-10-30"), started)[0]


def test_incomplete_bar_and_late_backfill_blocked():
    started = "2026-10-06T03:00:00+00:00"
    before = utc("2026-10-30T14:59:00+08:00")
    assert forward.expected_cutoff(before) == pd.Timestamp("2026-10-29")
    assert not forward.monthend_gate(before, pd.Timestamp("2026-10-30"), started)[0]
    nextday = utc("2026-10-31T16:00:00+08:00")
    assert not forward.monthend_gate(nextday, pd.Timestamp("2026-10-30"), started)[0]


def test_unsupported_calendar_stops():
    with pytest.raises(ValueError, match="unverified calendar"):
        forward.sessions(2027)


def test_journal_chain_and_no_overwrite(tmp_path):
    forward.append_event(tmp_path, "first", dict(real=False))
    forward.append_event(tmp_path, "second", dict(real=False))
    records, digest = forward.verify_journal(tmp_path)
    assert len(records) == 2 and digest
    with pytest.raises(FileExistsError):
        forward.write_new(tmp_path / "journal/000001.json", dict(overwritten=True))
    path = tmp_path / "journal/000002.json"
    event = json.loads(path.read_text(encoding="utf-8"))
    event["payload"]["real"] = True
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ValueError, match="event changed"):
        forward.verify_journal(tmp_path)


def test_forward_code_change_and_reinitialization_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "verify_frozen", lambda: "frozen-test")
    monkeypatch.setattr(forward, "implementation_hashes", lambda: {"test": "one"})
    forward.initialize(tmp_path)
    assert forward.state(tmp_path)["mode"].startswith("observation")
    with pytest.raises(FileExistsError):
        forward.initialize(tmp_path)
    monkeypatch.setattr(forward, "implementation_hashes", lambda: {"test": "changed"})
    with pytest.raises(ValueError, match="implementation changed"):
        forward.state(tmp_path)


def test_nonmonthend_cannot_emit_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "verify_frozen", lambda: "frozen-test")
    monkeypatch.setattr(forward, "now", lambda: utc("2026-10-06T11:00:00+08:00"))
    forward.initialize(tmp_path)
    result = forward.signal(tmp_path, tmp_path / "nonexistent")
    assert result["status"] == "blocked" and result["orders"] == 0
    assert forward.verify_journal(tmp_path)[0][-1]["kind"] == "signal_blocked"


def setup_monthend_snapshot(tmp_path, monkeypatch, cutoff="2026-10-30"):
    # Synthetic fixtures test the recorder only, never reported as market evidence.
    monkeypatch.setattr(forward, "verify_frozen", lambda: "frozen-test")
    monkeypatch.setattr(forward, "now", lambda: utc("2026-10-06T11:00:00+08:00"))
    forward.initialize(tmp_path)
    monkeypatch.setattr(forward, "now", lambda: utc("2026-10-30T16:30:00+08:00"))
    snapshot = tmp_path / "snapshots/test"
    snapshot.mkdir(parents=True)
    raw = snapshot / "test_raw.json"
    forward.write_new(raw, dict(synthetic_test=True))
    forward.write_new(snapshot / "snapshot.json", dict(
        ok=True, cutoff=cutoff, started_at_utc="2026-10-30T08:01:00+00:00",
        finished_at_utc="2026-10-30T08:02:00+00:00", files={raw.name: forward.sha(raw)}))
    forward.append_event(tmp_path, "snapshot_collected", dict(
        snapshot_sha256=forward.sha(snapshot / "snapshot.json")))
    return snapshot


def test_monthend_records_all_models_once_without_fills(tmp_path, monkeypatch):
    snapshot = setup_monthend_snapshot(tmp_path, monkeypatch)
    dates = forward.sessions(2026)
    dates = dates[dates <= "2026-10-30"]
    frames = {s: pd.DataFrame(dict(close=1.), index=dates) for s in forward.protocol()["universe"]}
    fake = SimpleNamespace(load_data=lambda: ({}, frames, {}, []),
                           weights=lambda data, pos, model: dict.fromkeys(data, .25))
    monkeypatch.setattr(forward.importlib.util, "spec_from_file_location", lambda *args:
                        SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None)))
    monkeypatch.setattr(forward.importlib.util, "module_from_spec", lambda spec: fake)
    result = forward.signal(tmp_path, snapshot)
    assert set(result["targets"]) == set(forward.MODELS)
    assert result["signal_date"] == "2026-10-30" and result["live_orders"] == 0
    with pytest.raises(ValueError, match="already recorded"):
        forward.signal(tmp_path, snapshot)


def test_stale_and_tampered_inputs_blocked(tmp_path, monkeypatch):
    snapshot = setup_monthend_snapshot(tmp_path, monkeypatch, "2026-10-29")
    with pytest.raises(ValueError, match="cutoff mismatch"):
        forward.signal(tmp_path, snapshot)
    # A separate valid snapshot remains registered but modifying its data is rejected.
    other = tmp_path / "snapshots/other"
    other.mkdir()
    raw = other / "test.json"
    forward.write_new(raw, dict(synthetic_test=True))
    forward.write_new(other / "snapshot.json", dict(
        ok=True, cutoff="2026-10-30", started_at_utc="2026-10-30T08:01:00+00:00",
        finished_at_utc="2026-10-30T08:02:00+00:00", files={raw.name: forward.sha(raw)}))
    forward.append_event(tmp_path, "snapshot_collected", dict(
        snapshot_sha256=forward.sha(other / "snapshot.json")))
    raw.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        forward.signal(tmp_path, other)
