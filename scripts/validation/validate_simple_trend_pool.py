"""Independent checks of exported membership, descriptive states and history."""

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
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.research import sha256


def reconcile(output: Path) -> dict[str, Any]:
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    manifest_path = Path(audit["snapshot_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256(manifest_path) != audit["snapshot_sha256"]:
        raise AssertionError("Snapshot manifest changed")
    if sha256(manifest_path.parent / "panel.npz") != manifest["artifacts"]["panel.npz"]:
        raise AssertionError("Panel changed")
    with np.load(manifest_path.parent / "panel.npz", allow_pickle=False) as z:
        close, high, low, volume = (z[k] for k in ("close", "high", "low", "vol"))
        codes, dates = z["codes"], z["dates"]
    code_index = {str(code): i for i, code in enumerate(codes)}
    valid = pd.read_csv(output / "valid_universe.csv")
    pool = pd.read_csv(output / "observations.csv")
    history = pd.read_csv(output / "history.csv")
    exclusions = pd.read_csv(output / "exclusions.csv")
    for table in (valid, pool, exclusions):
        if table.code.duplicated().any():
            raise AssertionError("Duplicate security")
    if set(valid.code) & set(exclusions.code):
        raise AssertionError("Overlapping valid/excluded securities")
    if set(valid.code) | set(exclusions.code) != set(code_index):
        raise AssertionError("Unaccounted frozen security")
    expected_pool: set[str] = set()
    expected_stages: Counter[str] = Counter()
    pool_index = pool.set_index("code")
    aligned = 0
    for row in valid.itertuples():
        i = code_index[row.code]
        mask = np.isfinite(close[i])
        c, h, lo, v = (a[i, mask].astype(float) for a in (close, high, low, volume))
        current20, past20 = float(c[-20:].mean()), float(c[-25:-5].mean())
        current60 = float(c[-60:].mean())
        if not mask[-1] or len(c) < 251:
            raise AssertionError(f"Invalid eligible history: {row.code}")
        if "ST" in row.name.upper().replace(" ", "") or "退" in row.name:
            raise AssertionError(f"Invalid eligible name: {row.code}")
        up = c[-1] > current20 and current20 > past20
        np.testing.assert_allclose(
            [row.close, row.ma20, row.past_ma20, row.ma60],
            [c[-1], current20, past20, current60],
            rtol=1e-10,
        )
        if bool(row.trend) != bool(up):
            raise AssertionError(f"Membership mismatch: {row.code}")
        if not up:
            continue
        expected_pool.add(row.code)
        aligned += int(current20 > current60)
        base_volume = v[-6:-1].mean()
        warning = c[-1] < lo[-6:-1].min() and v[-1] > base_volume
        breakout = c[-1] > h[-11:-1].max()
        pullback = c[-1] < c[-2] and v[-1] < base_volume
        quiet = (
            v[-5:].mean() < v[-10:-5].mean()
            and h[-5:].max() / lo[-5:].min() < h[-10:-5].max() / lo[-10:-5].min()
        )
        stage = (
            "转弱警示"
            if warning
            else "突破扩展"
            if breakout
            else "回踩观察"
            if pullback
            else "缩量整理"
            if quiet
            else "趋势延续"
        )
        if row.stage != stage or pool_index.loc[row.code, "stage"] != stage:
            raise AssertionError(f"Stage mismatch: {row.code}")
        np.testing.assert_allclose(row.volume_ratio5, v[-1] / base_volume, rtol=1e-10)
        expected_stages[stage] += 1
    if set(pool.code) != expected_pool or len(pool) != audit["trend_stocks"]:
        raise AssertionError("Pool codes/count mismatch")
    if dict(expected_stages) != audit["stages"] or aligned != audit["aligned_stocks"]:
        raise AssertionError("Stage/structure totals mismatch")
    if len(valid) != audit["valid_stocks"] or len(exclusions) != audit["excluded_current"]:
        raise AssertionError("Universe/exclusion totals mismatch")
    checked_history = 0
    for code, rows in history.groupby("code", sort=False):
        i = code_index[str(code)]
        cols = np.flatnonzero(np.isfinite(close[i]))[-80:]
        if len(rows) != len(cols) or rows.date.tolist() != [str(d) for d in dates[cols]]:
            raise AssertionError(f"History dates mismatch: {code}")
        np.testing.assert_allclose(rows.close, close[i, cols], rtol=1e-10)
        np.testing.assert_allclose(rows.volume_wan, volume[i, cols] / 10000.0, rtol=1e-6)
        checked_history += len(rows)
    if set(history.code) != expected_pool:
        raise AssertionError("History population mismatch")
    return {
        "status": "reconciled",
        "as_of": audit["as_of"],
        "eligible_stocks_checked": len(valid),
        "trend_stocks_checked": len(pool),
        "history_rows_checked": checked_history,
        "stages": dict(expected_stages),
        "method": "Independent array slices, not the trend_state implementation",
        "visual_verification": "not performed; browser restrictions retained",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = reconcile(args.output)
    (args.output / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
