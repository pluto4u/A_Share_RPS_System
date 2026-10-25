"""Freeze reviewed research artifacts, excluding the disposable notebook runtime."""
import json
from datetime import datetime, timezone
from pathlib import Path

from study import ROOT, sha

HERE = Path(__file__).resolve().parent
OUT = ROOT / "output/rps_pv_20261006/v2"
files = [p for p in HERE.rglob("*") if p.is_file() and
         ".venv-notebook" not in p.parts and "__pycache__" not in p.parts]
files += [p for p in OUT.iterdir() if p.is_file() and p.name != "final_manifest.json"]
files += [ROOT / "tests/test_rps_pv_study.py"]
record = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(), independent_oos=False,
              cleared_for_investment=False, files={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)})
with (OUT / "final_manifest.json").open("x", encoding="utf-8") as file:
    json.dump(record, file, ensure_ascii=False, indent=2)
print(json.dumps(dict(files=len(files), frozen=True, cleared_for_investment=False)))
