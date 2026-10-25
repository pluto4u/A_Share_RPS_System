"""Load a newly computed read-only snapshot into the existing authored app."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
from pathlib import Path


def refresh(app: Path, snapshot: Path) -> None:
    destination = app / "src/data.json"
    current = json.loads(destination.read_text(encoding="utf-8"))
    replacement = json.loads(snapshot.read_text(encoding="utf-8"))
    if not current.get("id") or not isinstance(replacement.get("queries"), dict):
        raise ValueError("Expected existing app identity and reviewed queries")
    replacement["id"] = current["id"]
    replacement["surface"] = current["surface"]
    replacement["buildStatus"] = "complete"
    destination.write_text(
        json.dumps(replacement, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    refresh(args.app, args.snapshot)
    print(f"Updated reviewed rows only: {args.app}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
