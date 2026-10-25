"""IS-only cached RPS descriptions; no selection, execution or OOS reads."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from trend_start.dual_system import StudyConfig, sha256
from trend_start.engine import block_bootstrap_mean
from trend_start.rps_trajectory import PERIODS, Phase, phase_features

ROOT = PROJECT_ROOT
CACHE = ROOT / "output/trend_start_cache_20260930"
FROZEN = ROOT / "output/dual_research_20261002_v1/freeze.json"
EVENTS = ROOT / "output/dual_research_is_diagnostics_20261002_v2/is_event_diagnostics.csv"
OUT = ROOT / "output/rps_phase_research_20261003_v1"
SPEC = ROOT / "docs/RPS_PHASE_RESEARCH_20261003.md"


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def main() -> None:
    cfg = StudyConfig()
    freeze = json.loads(FROZEN.read_text(encoding="utf-8"))
    params = freeze["a_params"]
    if freeze["a_combo"] != 1276 or params != [60, 0.2, 40, 0.12, 0.8, 2.0, 3, 0.03]:
        raise ValueError("unexpected frozen A definition")
    paths = [
        Path(__file__),
        SPEC,
        ROOT / "trend_start/rps_trajectory.py",
        ROOT / "tests/test_rps_trajectory.py",
        FROZEN,
        EVENTS,
        CACHE / "panel.npz",
        CACHE / "rps.npz",
    ]
    for name in ("trend_start/engine.py", "trend_start/dual_system.py", "run_dual_research.py"):
        if sha256(ROOT / name) != freeze["hashes"][name.replace("/", "\\")]:
            raise ValueError(f"frozen source changed: {name}")
    fingerprints = {str(p.relative_to(ROOT)): sha256(p) for p in paths}
    OUT.mkdir(parents=True, exist_ok=False)
    write_json(
        OUT / "registered.json",
        {
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "input_end": cfg.is_end,
            "hashes": fingerprints,
            "status": "exploratory_is_description_not_pristine_validation",
            "rules_changed": False,
            "parameter_search": False,
        },
    )
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        all_dates = z["dates"]
        if np.any(np.diff(all_dates) <= np.timedelta64(0, "D")):
            raise ValueError("invalid market calendar")
        keep = int(np.searchsorted(all_dates, np.datetime64(cfg.is_end), side="right"))
        dates, codes = all_dates[:keep].copy(), z["codes"].copy()
        close = z["close"][:, :keep].copy()
    with np.load(CACHE / "rps.npz", allow_pickle=False) as z:
        ranks = {p: z[f"RPS{p}"][:, :keep].copy() for p in PERIODS}
    if len(set(codes)) != len(codes) or any(a.shape != close.shape for a in ranks.values()):
        raise ValueError("invalid cached RPS alignment")
    source = pd.read_csv(EVENTS, float_precision="round_trip")
    if source.sig_cal.max() >= keep or source.exit_cal.max() >= keep:
        raise ValueError("post-IS record in event source")
    source = source.loc[
        (source.model == "A_retest")
        | ((source.model == "B_trend") & (source.disposition == "admitted"))
    ].copy()
    if source.duplicated(["model", "combo", "code", "sig_cal"]).any():
        raise ValueError("duplicate event")
    records = []
    for row, group in source.groupby("code_row", sort=True):
        i = int(row)
        cal = np.flatnonzero(np.isfinite(close[i]))
        c = close[i, cal].astype(float)
        rps = {p: a[i, cal].astype(float) for p, a in ranks.items()}
        for event in group.itertuples(index=False):
            sig = int(event.sig_bar)
            if event.code != codes[i] or cal[sig] != event.sig_cal or c[sig] != event.signal_close:
                raise AssertionError("event/panel coordinate mismatch")
            if event.rps120 != rps[120][sig]:
                raise AssertionError("cached full-universe RPS120 mismatch")
            if event.model == "A_retest":
                launch = int(event.volume_bar)
                phase = Phase(
                    start=launch - int(params[2]), end=launch - 1, launch=launch, signal=sig
                )
            else:
                phase = Phase(start=sig - cfg.b_pullback, end=sig - 1, signal=sig)
            features = phase_features(c, rps, phase)
            records.append(
                {
                    "model": event.model,
                    "code": event.code,
                    "signal_date": event.signal_date,
                    "disposition": event.disposition,
                    "frozen_net_return_pct": float(event.ret * 100),
                    "reference_start_date": str(dates[cal[phase.start]]),
                    "reference_end_date": str(dates[cal[phase.end]]),
                    "launch_date": str(dates[cal[phase.launch]])
                    if phase.launch is not None
                    else None,
                    **features,
                }
            )
    events = pd.DataFrame(records)
    events.to_csv(OUT / "event_features.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    populations = {
        "A_all_executable": events.loc[events.model == "A_retest"],
        "A_portfolio_admitted": events.loc[
            (events.model == "A_retest") & (events.disposition == "admitted")
        ],
        "B_portfolio_admitted": events.loc[events.model == "B_trend"],
    }
    groups, stages = [], []
    observations = (
        "rps50_reference_direction",
        "rps50_retest_direction",
        "rps120_retest_direction",
    )
    for population, frame in populations.items():
        for period in PERIODS:
            for stage in ("reference_start", "reference_end", "launch", "signal"):
                values = frame[f"rps{period}_{stage}"]
                stages.append(
                    {
                        "population": population,
                        "period": period,
                        "stage": stage,
                        "events": len(frame),
                        "available": int(values.notna().sum()),
                        "mean_rps": float(values.mean()) if values.notna().any() else None,
                    }
                )
        for field in observations:
            if population.startswith("B_") and "retest" in field:
                continue  # B has no identified expansion phase, not missing pseudo-events
            for state, subset in frame.groupby(field, dropna=False):
                ret = subset.frozen_net_return_pct
                lo, hi = block_bootstrap_mean(
                    ret, subset.signal_date.str[:7], cfg.bootstrap_n, cfg.bootstrap_seed
                )
                groups.append(
                    {
                        "population": population,
                        "observation": field,
                        "state": state,
                        "events": len(subset),
                        "mean_frozen_net_return_pct": float(ret.mean()),
                        "ci_lo_pct": lo,
                        "ci_hi_pct": hi,
                        "win_pct": float((ret > 0).mean() * 100),
                        "interpretation": "descriptive_frozen_events_not_filtered_portfolio",
                    }
                )
    pd.DataFrame(groups).to_csv(OUT / "cohorts.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    pd.DataFrame(stages).to_csv(
        OUT / "stage_means.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    for population, frame in populations.items():
        if not len(frame):
            raise ValueError(f"empty registered population: {population}")
    if any(sha256(ROOT / name) != expected for name, expected in fingerprints.items()):
        raise AssertionError("input/source mutation during run")
    write_json(
        OUT / "verification.json",
        {
            "input_end": str(dates[-1]),
            "future_result_tables_read": False,
            "original_entry_exit_cost_rules_changed": False,
            "candidate_rps_reranked": False,
            "original_rps120_snapshots_all_match": True,
            "fingerprints_unchanged": True,
            "population_counts": {k: len(v) for k, v in populations.items()},
            "no_independent_oos_validation": True,
        },
    )
    print(OUT, flush=True)
    print(pd.DataFrame(groups).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
