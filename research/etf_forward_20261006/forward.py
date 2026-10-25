"""Timestamped observation/signal recorder. No broker, fills or backdating API."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FROZEN = ROOT / "research/etf_allocation_20261006"
EVIDENCE = ROOT / "output/etf_allocation_20261006/freeze_manifest.json"
STORE = ROOT / "output/etf_forward_20261006"
LOCAL = timezone(timedelta(hours=8))
URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
MODELS = ("trend200", "momentum126", "equal_weight", "equity_hold")


def now():
    return datetime.now(timezone.utc)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)


def verify_frozen():
    manifest = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    bad = [name for name, digest in manifest["files"].items()
           if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
    if bad:
        raise ValueError(f"frozen evidence changed: {bad[:5]}")
    return sha(EVIDENCE)


def implementation_hashes():
    return {p.name: sha(p) for p in (Path(__file__), HERE / "calendar_2026.json")}


def verify_journal(store):
    previous = None
    events = []
    paths = sorted(p for p in (store / "journal").glob("*.json") if p.stem.isdigit())
    for path in paths:
        expected = json.loads(path.with_suffix(".digest.json").read_text(encoding="utf-8"))
        if sha(path) != expected["sha256"]:
            raise ValueError("journal event changed")
        event = json.loads(path.read_text(encoding="utf-8"))
        if event["sequence"] != len(events) + 1 or event["previous_sha256"] != previous:
            raise ValueError("journal chain broken")
        if events and event["recorded_at_utc"] < events[-1]["recorded_at_utc"]:
            raise ValueError("journal clock went backwards")
        previous = sha(path)
        events.append(event)
    return events, previous


def append_event(store, kind, payload):
    store.mkdir(parents=True, exist_ok=True)
    lock = store / "journal.lock"
    with lock.open("x", encoding="utf-8"):
        pass
    try:
        events, previous = verify_journal(store)
        event = dict(sequence=len(events)+1, previous_sha256=previous,
                     recorded_at_utc=now().isoformat(), kind=kind, payload=payload)
        path = store / "journal" / f"{event['sequence']:06d}.json"
        write_new(path, event)
        write_new(path.with_suffix(".digest.json"), dict(sha256=sha(path)))
        return event
    finally:
        lock.unlink()


def state(store):
    data = json.loads((store / "initialization.json").read_text(encoding="utf-8"))
    if data["evidence_sha256"] != verify_frozen():
        raise ValueError("evidence manifest changed")
    if data["implementation_hashes"] != implementation_hashes():
        raise ValueError("forward implementation changed; create a new version, do not rewrite logs")
    events, _ = verify_journal(store)
    if not events or events[0]["kind"] != "initialized" or events[0]["payload"] != data:
        raise ValueError("initialization not matched by journal")
    return data


def initialize(store):
    evidence = verify_frozen()
    data = dict(created_at_utc=now().isoformat(), experiment_id="ETF-20261006-01-FWD-v1",
                evidence_sha256=evidence, implementation_hashes=implementation_hashes(),
                mode="observation_and_targets_only_no_fills_no_broker",
                accounts={m: dict(cash=1_000_000, units={s: 0 for s in protocol()["universe"]})
                          for m in MODELS})
    write_new(store / "initialization.json", data)
    append_event(store, "initialized", data)
    return data


def protocol():
    return json.loads((FROZEN / "protocol.json").read_text(encoding="utf-8"))


def sessions(year):
    calendar = json.loads((HERE / "calendar_2026.json").read_text(encoding="utf-8"))
    if year != calendar["year"]:
        raise ValueError("unverified calendar year: new calendar needs a versioned experiment")
    days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
    for start, end in calendar["closed_ranges"]:
        days = days[(days < start) | (days > end)]
    return days


def expected_cutoff(current):
    local = current.astimezone(LOCAL)
    today = pd.Timestamp(local.date())
    allowed = sessions(local.year)
    # Delay until 16:00 instead of consuming an unfinished daily bar.
    eligible = allowed[allowed <= today] if local.hour >= 16 else allowed[allowed < today]
    if not len(eligible):
        raise ValueError("no verified completed session")
    return eligible[-1]


def monthend_gate(current, cutoff, started):
    local = current.astimezone(LOCAL)
    today = pd.Timestamp(local.date())
    if today <= pd.Timestamp(started).tz_convert(LOCAL).normalize().tz_localize(None):
        return False, "not_after_initialization_day"
    if local.hour < 16 or cutoff != today:
        return False, "not_today_completed_close"
    dates = sessions(local.year)
    month = dates[(dates.year == today.year) & (dates.month == today.month)]
    if len(month) == 0 or today != month[-1]:
        return False, "not_month_end_session"
    return True, "ready_for_targets_only"


def collect(store):
    state(store)
    started = now()
    cutoff = expected_cutoff(started)
    target = store / "snapshots" / started.strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, exist_ok=False)
    records = []
    cli = shutil.which("hithink-finance.cmd") or shutil.which("hithink-finance")
    if not cli:
        raise ValueError("hithink-finance CLI unavailable")
    try:
        for symbol in protocol()["universe"]:
            code = symbol.split(".")[0]
            params = dict(secid=f"1.{code}", klt=101, fqt=0, beg="20161001",
                          end=cutoff.strftime("%Y%m%d"), lmt=10000,
                          fields1="f1,f2,f3,f4,f5,f6",
                          fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
            response = requests.get(URL, params=params, timeout=20,
                                    headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            envelope = response.json()
            bars = (envelope.get("data") or {}).get("klines") or []
            if not bars:
                raise ValueError(f"{symbol}: empty prices")
            write_new(target / f"{code}_eastmoney_none.json", envelope)
            frame = pd.DataFrame([row.split(",") for row in bars], columns=[
                "date", "open", "close", "high", "low", "volume_lots", "amount",
                "amplitude", "pct", "change", "turnover"])
            if frame.date.max() != str(cutoff.date()) or (frame.date > str(cutoff.date())).any():
                raise ValueError(f"{symbol}: stale or future price data")
            frame.to_csv(target / f"{code}_raw.csv", index=False, encoding="utf-8")
            dividend_path = target / f"{code}_dividends.json"
            result = subprocess.run([cli, "fund", "dividends", "--fund-type", "exchange",
                                     "--thscode", symbol, "--format", "json", "--output",
                                     str(dividend_path)], capture_output=True, timeout=60)
            if result.returncode or not dividend_path.exists():
                raise ValueError(f"{symbol}: dividend fetch failed, exit={result.returncode}")
            dividends = json.loads(dividend_path.read_text(encoding="utf-8"))
            if dividends.get("ok") is not True:
                raise ValueError(f"{symbol}: unsuccessful dividend envelope")
            records.append(dict(symbol=symbol, rows=len(frame), last=frame.date.max(),
                                price_source=URL, params=params,
                                dividends_source="hithink-finance fund dividends exchange",
                                fetched_at_utc=now().isoformat()))
        manifest = dict(ok=True, started_at_utc=started.isoformat(),
                        finished_at_utc=now().isoformat(), cutoff=str(cutoff.date()), records=records,
                        files={p.name: sha(p) for p in sorted(target.iterdir()) if p.is_file()})
    except Exception as exc:
        manifest = dict(ok=False, started_at_utc=started.isoformat(),
                        finished_at_utc=now().isoformat(), error_type=type(exc).__name__, error=str(exc))
    write_new(target / "snapshot.json", manifest)
    append_event(store, "snapshot_collected" if manifest["ok"] else "snapshot_failed",
                 dict(path=str(target.relative_to(store)), snapshot_sha256=sha(target / "snapshot.json"),
                      ok=manifest["ok"]))
    if not manifest["ok"]:
        raise ValueError(manifest["error"])
    return dict(path=str(target), cutoff=manifest["cutoff"], records=records)


def signal(store, snapshot):
    init = state(store)
    current = now()
    cutoff = expected_cutoff(current)
    ready, reason = monthend_gate(current, cutoff, init["created_at_utc"])
    if not ready:
        append_event(store, "signal_blocked", dict(reason=reason, price_cutoff=str(cutoff.date())))
        return dict(status="blocked", reason=reason, orders=0)
    events, _ = verify_journal(store)
    if any(e["kind"] == "targets_recorded" and e["payload"]["signal_date"] == str(cutoff.date())
           for e in events):
        raise ValueError("signal already recorded; cannot overwrite")
    snapshot = snapshot.resolve()
    if snapshot.parent != (store / "snapshots").resolve():
        raise ValueError("snapshot must be from this observation session")
    manifest_path = snapshot / "snapshot.json"
    digest = sha(manifest_path)
    if not any(e["kind"] == "snapshot_collected" and e["payload"]["snapshot_sha256"] == digest for e in events):
        raise ValueError("snapshot is not registered in journal")
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not meta.get("ok") or meta["cutoff"] != str(cutoff.date()):
        raise ValueError("snapshot cutoff mismatch")
    fetched = datetime.fromisoformat(meta["started_at_utc"])
    finished = datetime.fromisoformat(meta["finished_at_utc"])
    if not fetched <= finished <= current or fetched.astimezone(LOCAL).date() != current.astimezone(LOCAL).date():
        raise ValueError("snapshot time mismatch")
    if fetched.astimezone(LOCAL).hour < 16:
        raise ValueError("snapshot collected before completed close")
    if any(sha(snapshot / name) != value for name, value in meta["files"].items()):
        raise ValueError("snapshot changed")
    spec = importlib.util.spec_from_file_location("frozen_etf", FROZEN / "run_study.py")
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    study.RAW = snapshot
    _, frames, _, quality = study.load_data()
    if any(f.index[-1] != cutoff for f in frames.values()):
        raise ValueError("incomplete latest prices")
    # Missing scheduled sessions are not silently forward-filled.
    required = sessions(current.astimezone(LOCAL).year)
    required = required[required <= cutoff]
    if any(not required.isin(f.index).all() for f in frames.values()):
        raise ValueError("missing current-year market sessions")
    payload = dict(signal_date=str(cutoff.date()), generated_at_utc=current.isoformat(),
                   input_sha256=digest, quality=quality, targets={
                       m: study.weights(frames, len(next(iter(frames.values()))) - 1, m) for m in MODELS},
                   execution="targets_only_no_fills; earliest_next_actual_session_open",
                   live_orders=0)
    append_event(store, "targets_recorded", payload)
    return payload


def status(store):
    init = state(store)
    current = now()
    cutoff = expected_cutoff(current)
    ready, reason = monthend_gate(current, cutoff, init["created_at_utc"])
    dates = sessions(current.astimezone(LOCAL).year)
    monthends = pd.Series(dates, index=dates).resample("ME").last().dropna()
    future = monthends[monthends > pd.Timestamp(current.astimezone(LOCAL).date())]
    events, _ = verify_journal(store)
    return dict(experiment=init["experiment_id"], frozen_evidence="verified",
                journal_events=len(events), journal_chain="verified", current_time_utc=current.isoformat(),
                expected_price_cutoff=str(cutoff.date()), signal_ready=ready, reason=reason,
                next_monthend=str(future.iloc[0].date()) if len(future) else None,
                accounts=init["accounts"], live_orders=0, fills_recorded=0,
                limitation="signals only; auction capture and execution ledger not implemented")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "collect", "status", "signal"))
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        result = initialize(STORE)
    elif args.command == "collect":
        result = collect(STORE)
    elif args.command == "status":
        result = status(STORE)
    else:
        result = signal(STORE, args.snapshot) if args.snapshot else signal(STORE, Path("missing"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
