"""Offline W1 replay from explicitly dated JSON inputs; no broker connection."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter
from rps_analyzer.tail_watch import MinuteBar, TailPlan, replay, tdx_formula


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan_bytes, bar_bytes = args.plan.read_bytes(), args.bars.read_bytes()
    plan = TailPlan.model_validate_json(plan_bytes)
    adapter = TypeAdapter(list[MinuteBar])
    bars = (
        adapter.validate_python(list(csv.DictReader(io.StringIO(bar_bytes.decode("utf-8-sig")))))
        if args.bars.suffix.lower() == ".csv"
        else adapter.validate_json(bar_bytes)
    )
    result = replay(plan, bars, args.as_of)
    result["source_hashes"] = {
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "bars_sha256": hashlib.sha256(bar_bytes).hexdigest(),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "state.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    (args.output / "indicator.tdx.txt").write_text(tdx_formula(plan), encoding="utf-8")
    print(json.dumps({"state": result["state"], "ready": result["ready"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
