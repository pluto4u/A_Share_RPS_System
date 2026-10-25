"""Independent arithmetic checks of RPS phase descriptions, IS only."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.dual_system import sha256

ROOT = PROJECT_ROOT
OUT = ROOT / "output/rps_phase_research_20261003_v1"
CACHE = ROOT / "output/trend_start_cache_20260930"


def main() -> None:
    registration = json.loads((OUT / "registered.json").read_text(encoding="utf-8"))
    features = pd.read_csv(
        OUT / "event_features.csv", na_values=["缺"], float_precision="round_trip"
    )
    original = pd.read_csv(
        ROOT / "output/dual_research_is_diagnostics_20261002_v2/is_event_diagnostics.csv",
        float_precision="round_trip",
    )
    cohorts = pd.read_csv(OUT / "cohorts.csv", na_values=["缺"], float_precision="round_trip")
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    for name, fingerprint in registration["hashes"].items():
        check(f"hash:{name}", sha256(ROOT / name) == fingerprint)
    check("unique_keys", not features.duplicated(["model", "code", "signal_date"]).any())
    linked = features.merge(
        original[["model", "code", "signal_date", "ret", "rps120"]],
        on=["model", "code", "signal_date"],
        validate="one_to_one",
    )
    check("all_events_linked", len(linked) == len(features))
    check("all_frozen_net_returns", np.array_equal(linked.frozen_net_return_pct, linked.ret * 100))
    check("all_cached_signal_rps120", np.array_equal(linked.rps120_signal, linked.rps120))
    check("no_post_cutoff_signals", (features.signal_date <= "2021-12-31").all())
    populations = {
        "A_all_executable": features.loc[features.model == "A_retest"],
        "A_portfolio_admitted": features.loc[
            (features.model == "A_retest") & (features.disposition == "admitted")
        ],
        "B_portfolio_admitted": features.loc[features.model == "B_trend"],
    }
    for row in cohorts.itertuples(index=False):
        frame = populations[row.population]
        subset = frame.loc[frame[row.observation] == row.state]
        check(
            f"cohort:{row.population}:{row.observation}:{row.state}",
            len(subset) == row.events
            and np.isclose(
                subset.frozen_net_return_pct.mean(),
                row.mean_frozen_net_return_pct,
                atol=1e-12,
                rtol=0,
            ),
        )
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        all_dates = z["dates"]
        keep = int(np.searchsorted(all_dates, np.datetime64("2021-12-31"), side="right"))
        dates, codes, close = all_dates[:keep], z["codes"], z["close"][:, :keep]
    with np.load(CACHE / "rps.npz", allow_pickle=False) as z:
        ranks = {p: z[f"RPS{p}"][:, :keep] for p in (50, 120, 250)}
    locations = {str(code): row for row, code in enumerate(codes)}
    sample = features.sample(n=min(60, len(features)), random_state=7)
    for record in sample.itertuples(index=False):
        row = locations[record.code]
        cal = np.flatnonzero(np.isfinite(close[row]))
        sig = int(record.signal_bar)
        passed = str(dates[cal[sig]]) == record.signal_date
        for period in (50, 120, 250):
            for stage, index in (
                ("reference_start", int(record.reference_start_bar)),
                ("reference_end", int(record.reference_end_bar)),
                ("signal", sig),
            ):
                reported = getattr(record, f"rps{period}_{stage}")
                passed &= np.isclose(
                    reported, ranks[period][row, cal[index]], equal_nan=True, atol=0, rtol=0
                )
            change = (
                ranks[period][row, cal[int(record.reference_end_bar)]].item()
                - ranks[period][row, cal[int(record.reference_start_bar)]].item()
            )
            passed &= np.isclose(
                change,
                getattr(record, f"rps{period}_reference_change_pp"),
                equal_nan=True,
                atol=0,
                rtol=0,
            )
            if record.model == "A_retest":
                launch_cal = int(np.searchsorted(dates, np.datetime64(record.launch_date)))
                passed &= launch_cal < cal[sig]
                passed &= np.isclose(
                    getattr(record, f"rps{period}_launch"),
                    ranks[period][row, launch_cal],
                    equal_nan=True,
                    atol=0,
                    rtol=0,
                )
        check(f"sample_snapshot:{record.model}:{record.code}:{record.signal_date}", passed)
    rank_checks = []
    for day in ("2018-12-14", "2021-12-31"):
        t = int(np.searchsorted(dates, np.datetime64(day)))
        for period in (50, 120, 250):
            returns = np.full(len(codes), np.nan, dtype=np.float32)
            for row in range(len(codes)):
                cal = np.flatnonzero(np.isfinite(close[row, : t + 1]))
                if len(cal) < 251 or cal[-1] != t:
                    continue
                c = close[row, cal].astype(float)
                returns[row] = c[-1] / c[-1 - period] - 1
            n = int(np.isfinite(returns).sum())
            expected = (pd.Series(returns).rank(method="average") / n * 100).to_numpy(np.float32)
            actual = ranks[period][:, t]
            error = float(np.nanmax(np.abs(expected.astype(float) - actual)))
            matched = np.array_equal(np.isnan(expected), np.isnan(actual)) and error == 0
            check(f"full_universe_rank:{day}:RPS{period}", matched, f"N={n}, max_error={error}")
            rank_checks.append(
                {
                    "date": day,
                    "period": period,
                    "effective_stocks": n,
                    "max_abs_error": error,
                    "missingness_matches": np.array_equal(np.isnan(expected), np.isnan(actual)),
                }
            )
    pd.DataFrame(rank_checks).to_csv(OUT / "rps_rank_checks.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(checks).to_csv(OUT / "independent_checks.csv", index=False, encoding="utf-8-sig")
    if not all(row["passed"] for row in checks):
        raise AssertionError("RPS phase audit failed; see independent_checks.csv")
    print(f"{len(checks)} checks passed; sampled {len(sample)} event paths", flush=True)


if __name__ == "__main__":
    main()
