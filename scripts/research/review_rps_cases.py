"""Read-only six-case review, never optimises from the displayed 2026 winners."""

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
from run_daily_rps import names_from_tnf
from trend_start.case_review import CaseWindow, describe_daily, ma20_episodes
from trend_start.dual_system import StudyConfig, a_specs, b_specs, context, sha256
from trend_start.panel import Panel

ROOT = PROJECT_ROOT
CACHE = ROOT / "output/trend_start_cache_20260930"
OUT = ROOT / "output/case_rps_review_202606_202609_v1"
FROZEN = ROOT / "output/dual_research_20261002_v1/freeze.json"
CODES = ("688536.SH", "301041.SZ", "688498.SH", "688143.SH", "301047.SZ", "301080.SZ")


def save(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def main() -> None:
    window, cfg = CaseWindow(), StudyConfig()
    freeze = json.loads(FROZEN.read_text(encoding="utf-8"))
    frozen_inputs = {
        name: sha256(ROOT / name)
        for name in ("trend_start/engine.py", "trend_start/dual_system.py", "run_dual_research.py")
    }
    for name, value in frozen_inputs.items():
        if value != freeze["hashes"][name.replace("/", "\\")]:
            raise ValueError(f"frozen source changed: {name}")
    if freeze["a_combo"] != 1276:
        raise ValueError("unexpected frozen A combo")
    paths = [
        Path(__file__),
        ROOT / "trend_start/case_review.py",
        ROOT / "tests/test_case_review.py",
        ROOT / "docs/CASE_RPS_REVIEW_20261003.md",
        CACHE / "panel.npz",
        CACHE / "rps.npz",
        FROZEN,
    ]
    hashes = {str(p.relative_to(ROOT)): sha256(p) for p in paths}
    OUT.mkdir(parents=True, exist_ok=False)
    save(
        OUT / "registered.json",
        {
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "window": window.model_dump(),
            "hashes": hashes,
            "purpose": "post_selected_case_review_only_not_validation_or_parameter_selection",
            "frozen_entry_code_hashes": frozen_inputs,
        },
    )
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        dates, codes = z["dates"], z["codes"]
        keep = int(np.searchsorted(dates, np.datetime64(window.end), side="right"))
        if str(dates[keep - 1]) != window.end:
            raise ValueError("review end not in cached market calendar")
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate cached stock code")
        rows = []
        for code in CODES:
            hits = np.flatnonzero(codes == code)
            if len(hits) != 1:
                raise ValueError(f"missing or ambiguous cached code: {code}")
            rows.append(int(hits[0]))
        panel = Panel(
            dates[:keep].copy(),
            codes[rows].copy(),
            {
                k: z[k][rows, :keep].copy()
                for k in ("open", "high", "low", "close", "vol", "amount")
            },
            z["index_close"][:keep].copy(),
        )
    with np.load(CACHE / "rps.npz", allow_pickle=False) as z:
        ranks = {p: z[f"RPS{p}"][rows, :keep].copy() for p in (50, 120, 250)}
    names = names_from_tnf(Path("C:/new_tdx/T0002/hq_cache"))
    all_daily, all_episodes, all_signals, snapshots, months, checks = [], [], [], [], [], []
    for row, code in enumerate(CODES):
        if code not in names:
            raise ValueError(f"missing local name: {code}")
        ctx = context(panel, ranks[120], row)
        if ctx is None:
            raise ValueError(f"insufficient case history: {code}")
        cols = ctx.bars.cal
        frame = pd.DataFrame(
            {
                "date": panel.dates[cols].astype(str),
                **{
                    k: panel.field(k)[row, cols].astype(float)
                    for k in ("open", "high", "low", "close", "vol", "amount")
                },
                **{f"RPS{p}": ranks[p][row, cols].astype(float) for p in ranks},
            }
        )
        daily = describe_daily(frame)
        daily["code"], daily["name"] = code, names[code]
        daily["market_cal"] = cols
        review = daily.loc[daily.date.between(window.start, window.end)].copy()
        if review.empty:
            raise ValueError(f"no case bars in window: {code}")
        all_daily.append(review)
        episodes = ma20_episodes(daily, window)
        episodes["code"], episodes["name"] = code, names[code]
        all_episodes.append(episodes)
        last = review.iloc[-1]
        signals = []
        for model, specs in (
            ("A_retest", a_specs(ctx, freeze["a_combo"])),
            ("B_trend", b_specs(ctx, cfg)),
        ):
            for spec in specs:
                bar = int(spec["sig_bar"])
                day = str(ctx.bars.dates[bar])
                if window.start <= day <= window.end:
                    signals.append(
                        {
                            "code": code,
                            "name": names[code],
                            "model": model,
                            "signal_date": day,
                            "signal_close": float(ctx.bars.c[bar]),
                            "RPS120": float(ctx.rps[bar]),
                            "not_portfolio_execution": True,
                        }
                    )
        all_signals.extend(signals)
        snapshots.append(
            {
                "code": code,
                "name": names[code],
                "date": last.date,
                **{
                    key: float(last[key])
                    for key in (
                        "close",
                        "MA20",
                        "MA30",
                        "price_vs_ma20_pct",
                        "RPS50",
                        "RPS120",
                        "RPS250",
                    )
                },
                "below_ma20": bool(last.below_ma20),
                "review_bars": len(review),
                "review_bars_below_ma20": int(review.below_ma20.sum()),
                "review_max_high": float(review.high.max()),
                "original_A_signal_count": sum(r["model"] == "A_retest" for r in signals),
                "original_B_signal_count": sum(r["model"] == "B_trend" for r in signals),
            }
        )
        for _, group in review.groupby(review.date.str[:7]):
            r = group.iloc[-1]
            months.append(
                {
                    "code": code,
                    "name": names[code],
                    "date": r.date,
                    **{
                        key: float(r[key]) for key in ("close", "MA20", "RPS50", "RPS120", "RPS250")
                    },
                }
            )
        checks.append(
            {
                "check": f"original_MA20:{code}",
                "passed": np.allclose(
                    daily.MA20, ctx.bars.ma20, equal_nan=True, rtol=0, atol=1e-12
                ),
            }
        )
    pd.concat(all_daily, ignore_index=True).to_csv(
        OUT / "daily.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    pd.concat(all_episodes, ignore_index=True).to_csv(
        OUT / "ma20_episodes.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    pd.DataFrame(snapshots).to_csv(
        OUT / "latest.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    pd.DataFrame(months).to_csv(
        OUT / "month_end.csv", index=False, encoding="utf-8-sig", na_rep="缺"
    )
    pd.DataFrame(
        all_signals,
        columns=[
            "code",
            "name",
            "model",
            "signal_date",
            "signal_close",
            "RPS120",
            "not_portfolio_execution",
        ],
    ).to_csv(OUT / "original_entry_signals.csv", index=False, encoding="utf-8-sig", na_rep="缺")
    checks.extend(
        {"check": f"hash:{name}", "passed": sha256(ROOT / name) == value}
        for name, value in hashes.items()
    )
    pd.DataFrame(checks).to_csv(OUT / "checks.csv", index=False, encoding="utf-8-sig")
    if not all(r["passed"] for r in checks):
        raise AssertionError("case review checks failed")
    print(pd.DataFrame(snapshots).to_string(index=False), flush=True)
    print(pd.DataFrame(months).to_string(index=False), flush=True)
    print(pd.concat(all_episodes, ignore_index=True).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
