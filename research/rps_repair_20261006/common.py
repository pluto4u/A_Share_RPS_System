from project_paths import script_file
"""Read-only source reconciliation; never write to production data or signals."""
import ast
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output/rps_repair_20261006/v1"
OLD = ROOT / "output/rps_diag_20261006"
CACHE = Path("F:/88888.投资/08.选股/07.investment_research/right_side_strategy_bundle_20260924_221054/right_side_strategy/output/trend_start_cache_20260930/panel.npz")
GBBQ = Path("C:/new_tdx/T0002/hq_cache/gbbq")
PRICE = ["open", "high", "low", "close"]
FIELDS = PRICE + ["vol", "amount"]
EVENT_FIELDS = ["hongli_panqianliutong", "peigujia_qianzongguben", "songgu_qianzongguben", "peigu_houzongguben"]


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save(name, value):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def verify_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError(f"Frozen input changed: {name}")
    return len(manifest["files"])


def guard():
    if (OUT / "final_manifest.json").exists():
        raise RuntimeError("This version is frozen; create a new research version")
    protocol_path = HERE / "protocol.json"
    registration = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
    if sha(protocol_path) != registration["protocol_sha256"]:
        raise RuntimeError("Registered protocol changed")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    for name, expected in protocol["input_hashes"].items():
        if sha(Path(name)) != expected:
            raise RuntimeError(f"Registered input changed: {name}")
    return protocol


def read_raw(path):
    blob = Path(path).read_bytes()
    if not blob or len(blob) % 32:
        raise ValueError("Empty or truncated 32-byte TDX records")
    rows = list(struct.iter_unpack("<5If2I", blob))
    frame = pd.DataFrame(rows, columns=["trade_date", *PRICE, "amount", "vol", "reserved"])
    frame.trade_date = pd.to_datetime(frame.trade_date.astype(str), format="%Y%m%d", errors="raise")
    frame[PRICE] = frame[PRICE] / 100.0
    if frame.trade_date.duplicated().any() or not frame.trade_date.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered raw dates")
    return frame.drop(columns="reserved")


def load_registered_adjust(protocol):
    """Extract only the existing pure function, avoiding production module imports.

    This reproduces the old cache's algorithm. It is NOT independent adjustment
    verification or proof of announcement-time availability.
    """
    path = script_file("run_local_tdx_compare.py")
    if sha(path) != protocol["input_hashes"][str(path)]:
        raise RuntimeError("Adjustment implementation changed")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "adjust")
    namespace = dict(np=np, Any=Any, PRICE=PRICE, EVENT_FIELDS=EVENT_FIELDS)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["adjust"]


def comparison(left, right, fields, rtol, atol):
    """Aligned finite observations only; missing observations are never zero-filled."""
    left_keys, right_keys = left.index, right.index
    if isinstance(left_keys, pd.DatetimeIndex) and isinstance(right_keys, pd.DatetimeIndex):
        # Parquet/NPZ calendars and raw files may encode the same timestamps at
        # different resolution. Normalize units, never reorder or shift dates.
        left_keys, right_keys = left_keys.as_unit("ns"), right_keys.as_unit("ns")
    if not left_keys.equals(right_keys) or not len(left):
        raise ValueError("Expected nonempty identical observation keys")
    a, b = left[fields].to_numpy(float), right[fields].to_numpy(float)
    finite = np.isfinite(a) & np.isfinite(b)
    close = np.isclose(a, b, rtol=rtol, atol=atol) & finite
    relative = np.abs(a - b) / np.maximum(np.abs(b), np.finfo(float).tiny)
    return dict(compared_cells=int(a.size), nonfinite_cells=int((~finite).sum()),
                mismatched_cells=int((~close).sum()),
                max_relative_error=float(np.max(relative[finite])) if finite.any() else None)
