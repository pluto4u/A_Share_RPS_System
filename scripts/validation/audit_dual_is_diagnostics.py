"""Independent IS CSV arithmetic, path spot-checks and causal reference distances."""

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
OUT = ROOT / "output/dual_research_is_diagnostics_20261002_v2"
BASE = ROOT / "output/dual_research_20261002_v1"


def main() -> None:
    registered = json.loads((OUT / "registered.json").read_text(encoding="utf-8"))
    cfg = registered["scope"]["cfg"]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    for path, value in registered["hashes"].items():
        check(f"hash:{path}", sha256(ROOT / path) == value, "registered diagnostic input unchanged")
    events = pd.read_csv(OUT / "is_event_diagnostics.csv", float_precision="round_trip")
    keys = ["model", "combo", "code", "sig_cal"]
    check("unique_events", not events.duplicated(keys).any(), "one causal event per key")
    check(
        "IS_signals",
        events.signal_date.between(cfg["is_start"], cfg["is_end"]).all(),
        "signal dates bounded",
    )
    check("IS_exits", events.exit_date.le(cfg["is_end"]).all(), "all evaluated exits bounded")
    horizons = [
        f"{prefix}_d{h}_close_return_pct" for prefix in ("signal", "post_exit") for h in (1, 5, 20)
    ]
    actual = [
        col
        for col in events
        if col.startswith(("signal_d", "post_exit_d")) and col.endswith("_close_return_pct")
    ]
    check(
        "horizon_schema",
        set(actual) == set(horizons),
        "exact six horizon fields, no trigger-index expansion",
    )
    populations = pd.read_csv(OUT / "population_summary.csv")
    cost = pd.read_csv(OUT / "cost_cash_diagnostics.csv")
    paths = pd.read_csv(OUT / "exit_path_diagnostics.csv")
    references = []
    for model in ("A_retest", "A_volume", "B_trend"):
        frame = events.loc[events.model == model]
        admitted = frame.loc[frame.disposition == "admitted"]
        accepted = pd.read_csv(
            BASE / f"is/{model}/portfolio_trades.csv", float_precision="round_trip"
        )
        rejected = pd.read_csv(BASE / f"is/{model}/portfolio_rejected.csv")
        check(
            f"{model}:offers_reconcile",
            len(frame) == len(accepted) + len(rejected),
            "all executable offers assigned",
        )
        check(f"{model}:admitted_count", len(admitted) == len(accepted), "not stock-chain count")
        p = populations.loc[
            (populations.model == model) & (populations.population == "portfolio_admitted")
        ].iloc[0]
        check(
            f"{model}:mean_return",
            abs(p.mean_net_return_pct - accepted.ret.mean() * 100) < 1e-10,
            "independent original trade CSV mean",
        )
        c = cost.loc[cost.model == model].iloc[0]
        price_pnl = float((accepted.units * (accepted.book_exit_px - accepted.entry_px)).sum())
        total_cost = float(
            (
                accepted.cost
                - accepted.units * accepted.entry_px
                + accepted.units * accepted.book_exit_px
                - accepted.net_proceeds
            ).sum()
        )
        check(
            f"{model}:price_pnl",
            abs(c.gross_price_pnl - price_pnl) < 1e-5,
            "original accepted units and prices",
        )
        check(
            f"{model}:cost",
            abs(c.modeled_cost - total_cost) < 1e-5,
            "original accepted cost/proceeds",
        )
        check(
            f"{model}:net", abs(c.net_pnl - accepted.pnl.sum()) < 1e-5, "sum original accepted PnL"
        )
        check(
            f"{model}:reason_total",
            paths.loc[paths.model == model, "events"].sum() == len(accepted),
            "disjoint future exit descriptions, not causal filters",
        )
        for label, series, present in (
            (
                "frozen_floor",
                (admitted.entry_px - admitted.pocket_low) / admitted.entry_px * 100,
                admitted.pocket_low.notna(),
            ),
            (
                "prior_known_MA20",
                (admitted.entry_px - admitted.ma20) / admitted.entry_px * 100,
                admitted.ma20.notna(),
            ),
        ):
            s = series.loc[present]
            references.append(
                {
                    "model": model,
                    "reference": label,
                    "admitted_events": len(admitted),
                    "reference_present": len(s),
                    "open_at_or_below_reference_count": int((s <= 0).sum()),
                    "open_at_or_below_reference_pct": float((s <= 0).mean() * 100)
                    if len(s)
                    else None,
                    "distance_p25_pct": float(s.quantile(0.25)) if len(s) else None,
                    "distance_median_pct": float(s.median()) if len(s) else None,
                    "distance_p75_pct": float(s.quantile(0.75)) if len(s) else None,
                    "definition": "signed (entry open - prior-known reference) / entry open;"
                    " no signal/exit change",
                }
            )
    # Independent fixed sample, direct market-calendar close prices, no engine return helper.
    cache = ROOT / "output/trend_start_cache_20260930/panel.npz"
    with np.load(cache, allow_pickle=False) as z:
        dates = z["dates"]
        end = int(np.searchsorted(dates, np.datetime64(cfg["is_end"]), side="right"))
        close = z["close"][:, :end].copy()
    sample = events.sample(n=min(60, len(events)), random_state=7)
    window_checks = []
    for r in sample.itertuples(index=False):
        for prefix, start, price in (
            ("signal", r.sig_cal, r.signal_close),
            ("post_exit", r.exit_cal, r.exit_px),
        ):
            for h in (1, 5, 20):
                target = int(start) + h
                px = (
                    float(close[int(r.code_row), target])
                    if target < end and (prefix == "signal" or not r.unfinished)
                    else np.nan
                )
                expected = (px / price - 1) * 100
                actual_value = getattr(r, f"{prefix}_d{h}_close_return_pct")
                window_checks.append(
                    np.isclose(expected, actual_value, rtol=1e-10, atol=1e-10, equal_nan=True)
                )
    check(
        "market_horizon_sample",
        all(window_checks),
        f"{len(window_checks)} checks over {len(sample)} fixed sample events; not full coverage",
    )
    result = pd.DataFrame(checks)
    result.to_csv(OUT / "independent_checks.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(references).to_csv(
        OUT / "entry_reference_distances.csv", index=False, encoding="utf-8-sig"
    )
    failed = result.loc[~result.passed]
    print(f"IS diagnostic checks: {len(result)}; failed: {len(failed)}")
    print(pd.DataFrame(references).to_string(index=False))
    if not failed.empty:
        print(failed.to_string(index=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
