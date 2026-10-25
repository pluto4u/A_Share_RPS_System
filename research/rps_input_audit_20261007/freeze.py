"""Freeze this finite audit, not mutable report presentation or production data."""
import importlib.metadata
import json
from audit import HERE, OUT, ROOT, sha

manifest_path = OUT / "final_manifest.json"
if manifest_path.exists():
    raise FileExistsError(manifest_path)
validation = json.loads((OUT / "validation.json").read_text(encoding="utf-8"))
assert validation["registered_inputs_unchanged"]
paths = sorted(path for folder in [HERE, OUT] for path in folder.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts)
paths.append(ROOT / "tests/test_rps_input_audit_20261007.py")
manifest = dict(user_date="2026-10-07", purpose="finite surviving-stock source audit",
    sdk_version=importlib.metadata.version("baostock"), original_db_modified=False,
    new_strategy_tested=False, files={path.relative_to(ROOT).as_posix():sha(path) for path in paths})
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
assert all(sha(ROOT / path) == expected for path, expected in manifest["files"].items())
print(f"Frozen {len(manifest['files'])} audit files; original data and strategies not overwritten")
