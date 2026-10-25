"""Check collected prices/distributions without emitting historical signals."""
import argparse
import importlib.util
import json
from pathlib import Path

import forward


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    forward.state(forward.STORE)
    if snapshot.parent != (forward.STORE / "snapshots").resolve():
        raise ValueError("snapshot outside current observation session")
    meta = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if not meta.get("ok") or any(forward.sha(snapshot / name) != digest for name, digest in meta["files"].items()):
        raise ValueError("unsuccessful or modified snapshot")
    events, _ = forward.verify_journal(forward.STORE)
    if not any(e["kind"] == "snapshot_collected" and
               e["payload"]["snapshot_sha256"] == forward.sha(snapshot / "snapshot.json") for e in events):
        raise ValueError("snapshot not in journal")
    spec = importlib.util.spec_from_file_location("frozen_check", forward.FROZEN / "run_study.py")
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    study.RAW = snapshot
    _, frames, _, quality = study.load_data()
    result = dict(checked_at_utc=forward.now().isoformat(), quality=quality,
                  snapshot_sha256=forward.sha(snapshot / "snapshot.json"),
                  frozen_raw_price_hash_matches={s: forward.sha(snapshot / f"{s.split('.')[0]}_raw.csv") ==
                      forward.sha(forward.ROOT / "output/etf_allocation_20261006/raw" / f"{s.split('.')[0]}_raw.csv")
                      for s in frames},
                  scope="OHLC, uniqueness, dates, raw VWAP consistency, dividend dates/duplicates; not fund-announcement completeness",
                  signals_generated=0, fills_generated=0)
    forward.append_event(forward.STORE, "snapshot_quality_checked", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
