"""Inspect the three-component investment workspace without coupling sources."""

from __future__ import annotations

import argparse
import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import WORKSPACE_OUTPUT_DIR, TDX_ROOT


LOCAL_CONFIG = PROJECT_ROOT / "config" / "workspace.local.json"
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "workspace.example.json"


def load_config() -> dict:
    path = LOCAL_CONFIG if LOCAL_CONFIG.exists() else EXAMPLE_CONFIG
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def inspect_components() -> list[dict]:
    rows = []
    for name, item in load_config()["components"].items():
        raw_path = Path(item["path"]).expanduser()
        path = (PROJECT_ROOT / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        marker = path / item["marker"]
        rows.append(
            {
                "name": name,
                "path": str(path),
                "purpose": item["purpose"],
                "exists": path.is_dir(),
                "marker": item["marker"],
                "ready": path.is_dir() and marker.is_file(),
            }
        )
    return rows


def print_status(rows: list[dict]) -> int:
    for row in rows:
        state = "READY" if row["ready"] else "MISSING"
        print(f"[{state:7}] {row['name']:<10} {row['path']}")
        print(f"          {row['purpose']}")
    return 0 if all(row["ready"] for row in rows) else 1


def write_inventory(rows: list[dict]) -> Path:
    WORKSPACE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = WORKSPACE_OUTPUT_DIR / "inventory.json"
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "integration_mode": "separate-components-read-only-contracts",
        "components": rows,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="三工程统一状态检查")
    parser.add_argument("command", choices=("status", "inventory", "breadth", "archive", "catalog", "entries", "run"), nargs="?", default="status")
    parser.add_argument("--tdx-root", type=Path, default=TDX_ROOT)
    parser.add_argument("--as-of")
    parser.add_argument("--category")
    parser.add_argument("--entrypoint")
    # Child arguments are accepted only after an explicit separator.
    argv = sys.argv[1:]
    separator = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:separator])
    child_args = argv[separator + 1:] if separator < len(argv) else []

    if args.command == "catalog":
        from organize_project import scan
        scan()
        return 0
    if args.command in ("entries", "run"):
        path = PROJECT_ROOT / "outputs/catalog/entrypoints.json"
        if not path.exists():
            parser.error("Run 'python tools/workspace.py catalog' first")
        entries = json.loads(path.read_text(encoding="utf-8"))
        if args.command == "entries":
            for item in entries:
                if not args.category or item["category"] == args.category:
                    print(f"{item['id']:<40} [{item['category']}] {item['command']}")
            return 0
        if not args.entrypoint:
            parser.error("run requires --entrypoint NAME")
        entry = next((item for item in entries if item["id"] == args.entrypoint), None)
        if entry is None:
            parser.error("Unknown entrypoint; use the entries command to inspect available names")
        source = (PROJECT_ROOT / entry["path"]).resolve()
        if not source.is_relative_to(PROJECT_ROOT) or not source.is_file():
            parser.error("Entrypoint is missing or outside this project")
        if entry["path"].startswith("research/experiments/"):
            command = [sys.executable, "-m", "research.experiments." + entry["id"]]
        else:
            command = [sys.executable, str(source)]
        return subprocess.run(command + child_args, cwd=PROJECT_ROOT).returncode

    if args.command == "breadth":
        from workflows.tdx_breadth import run
        print(run(args.tdx_root, args.as_of))
        return 0
    if args.command == "archive":
        from strategy_archive import build
        build()
        return 0

    rows = inspect_components()
    status = print_status(rows)
    if args.command == "inventory":
        print(f"\nInventory: {write_inventory(rows)}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

