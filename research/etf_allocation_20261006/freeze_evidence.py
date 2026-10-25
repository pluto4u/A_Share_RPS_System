"""Create a no-overwrite evidence manifest; hashes do not certify live execution."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

here = Path(__file__).resolve().parent
root = here.parents[1]
out = root / "output/etf_allocation_20261006"
target = out / "freeze_manifest.json"
files = sorted([p for p in here.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
               + [root / "tests/test_etf_allocation_study.py"]
               + [p for p in out.rglob("*") if p.is_file() and p != target])
record = {
    "experiment_id": "ETF-20261006-01",
    "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
    "independent_oos": False,
    "status": "historical_candidate_only_no_live_orders",
    "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
}
with target.open("x", encoding="utf-8") as f:
    json.dump(record, f, ensure_ascii=False, indent=2)
print(json.dumps({"files": len(files), "manifest": str(target)}, ensure_ascii=False))
