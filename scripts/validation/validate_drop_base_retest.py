"""Independently reconcile current pattern observations with the frozen daily bars.

This validator does not call the event detector and is not a return backtest.
"""

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
from run_drop_base_retest import Candidate
from trend_start.drop_base_retest import PatternConfig
from trend_start.panel import load_panel
from trend_start.research import sha256, source_manifest, validate_panel

ROOT = PROJECT_ROOT


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def same(actual: float, expected: float, label: str) -> None:
    require(bool(np.isclose(actual, expected, rtol=1e-6, atol=1e-6)), label)


def validate_candidate(row: Candidate, d: pd.DataFrame, cfg: PatternConfig) -> None:
    """Recompute the reported anchors and replay only this frozen event from raw bars."""
    dates = d.date.astype(str).tolist()
    p, tr, s, end, event, review = (
        dates.index(value)
        for value in (
            row.peak_date,
            row.trough_date,
            row.base_start,
            row.base_end,
            row.event_date,
            row.review_date,
        )
    )
    current = len(d) - 1
    require(p < tr < s <= end < event <= review <= current, f"{row.code}: event order")
    require(end == event - 1, f"{row.code}: base must end before breakout")
    require(row.as_of == dates[-1], f"{row.code}: as_of")
    n = end - s + 1
    require(cfg.base_min <= n <= cfg.base_max and n == row.base_bars, "base length")
    left = max(0, s - cfg.drop_lookback)
    old = d.close.iloc[left:s].to_numpy(dtype=float)
    dd = 1 - old / np.maximum.accumulate(old)
    require(left + int(dd.argmax()) == tr, "drop trough anchor")
    require(left + int(old[: tr - left + 1].argmax()) == p, "drop peak anchor")
    drop = 1 - float(d.close.iloc[tr] / d.close.iloc[p])
    same(row.drop_pct, drop * 100, "drop percentage")
    require(drop >= cfg.drop_min and s - tr <= cfg.trough_gap_max, "completed drop")
    base = d.iloc[s:event]
    pivot, floor = float(base.high.max()), float(base.low.min())
    quiet = float(base.vol.iloc[-5:].mean())
    dry = quiet / float(base.vol.iloc[:5].mean())
    same(row.pivot, pivot, "frozen upper edge")
    same(row.floor, floor, "frozen lower edge")
    same(row.width_pct, (pivot / floor - 1) * 100, "base width")
    same(row.dry_ratio, dry, "base dry ratio")
    require(pivot / floor - 1 <= cfg.base_width_max, "base width threshold")
    require(
        abs(float(base.close.iloc[-1] / base.close.iloc[0]) - 1) <= cfg.base_drift_max,
        "base price drift",
    )
    require(dry <= cfg.base_dry_max and base.amount.mean() >= cfg.amount_min, "base volume/amount")
    require(floor >= float(d.low.iloc[tr]) * 0.98, "base undercut")
    break_bar = d.iloc[event]
    break_volume = float(break_bar.vol)
    clv = float((break_bar.close - break_bar.low) / (break_bar.high - break_bar.low))
    same(row.breakout_volume_ratio, break_volume / quiet, "breakout volume ratio")
    require(
        break_bar.close > pivot
        and break_volume >= quiet * cfg.breakout_volume_min
        and clv >= cfg.breakout_clv_min,
        "breakout conditions",
    )
    require(current - event <= cfg.event_days, "expired event")
    last_review, touch, below = -1, -1, 0
    kind = touch_kind = stage = ""
    for t in range(event + 1, current + 1):
        bar = d.iloc[t]
        below = below + 1 if bar.close < pivot * (1 - cfg.close_below) else 0
        require(bar.close >= floor and below < 2, f"{row.code}: invalidated event")
        near = (
            bar.low <= pivot * (1 + cfg.touch_above)
            and bar.low >= floor
            and pivot * (1 - cfg.close_below) <= bar.close <= pivot * (1 + cfg.extension_max)
        )
        if (
            touch >= 0
            and t > touch
            and bar.close > d.high.iloc[t - 1]
            and bar.close > pivot
            and bar.vol >= d.vol.iloc[t - 1]
        ):
            last_review, kind, stage = t, touch_kind, "回踩后再转强"
            touch = -1
        elif near:
            ratio5 = float(bar.vol / d.vol.iloc[t - 5 : t].mean())
            quiet_touch = bar.vol <= break_volume * cfg.quiet_vs_break and ratio5 <= 1
            touch_clv = (
                float((bar.close - bar.low) / (bar.high - bar.low)) if bar.high > bar.low else 0
            )
            loud_touch = ratio5 >= cfg.loud_vs_prior5 and bar.close >= pivot and touch_clv >= 0.5
            if quiet_touch or loud_touch:
                last_review = touch = t
                touch_kind = kind = "quiet" if quiet_touch else "loud"
                stage = "缩量回踩待确认" if quiet_touch else "放量回踩守位待确认"
    require(
        last_review == review and stage == row.stage and kind == row.review_kind,
        f"{row.code}: retest/confirmation anchor",
    )
    require(current - review <= cfg.review_fresh_days, "stale review")
    require(
        pivot * (1 - cfg.close_below) <= row.close <= pivot * (1 + cfg.extension_max),
        "current price band",
    )
    same(row.close, float(d.close.iloc[-1]), "current close")
    same(row.ma20, float(d.close.iloc[-20:].mean()), "MA20")
    same(row.ma60, float(d.close.iloc[-60:].mean()), "MA60")
    same(row.extension_pct, (row.close / pivot - 1) * 100, "extension")
    same(row.volume_ratio5, float(d.vol.iloc[-1] / d.vol.iloc[-6:-1].mean()), "latest volume ratio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder = args.output
    audit = json.loads((folder / "audit.json").read_text(encoding="utf-8"))
    cfg = PatternConfig.model_validate(audit["config"])
    manifest_path = Path(audit["snapshot_manifest"])
    require(sha256(manifest_path) == audit["snapshot_sha256"], "snapshot manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, digest in manifest["artifacts"].items():
        require(sha256(manifest_path.parent / name) == digest, f"artifact changed: {name}")
    for name, digest in audit["source_code_hashes"].items():
        require(sha256(ROOT / name) == digest, f"detector source changed: {name}")
    panel_audit = json.loads(
        (manifest_path.parent / "panel_audit.json").read_text(encoding="utf-8")
    )
    require(
        source_manifest(Path(panel_audit["tdx_root"])) == manifest["signature"]["sources"],
        "local daily source changed",
    )
    panel = load_panel(manifest_path.parent)
    validate_panel(panel)
    codes = {str(code): i for i, code in enumerate(panel.codes)}
    snapshot = json.loads((folder / "reviewed_snapshot.json").read_text(encoding="utf-8"))
    queries = snapshot["queries"]
    counts: dict[str, int] = {}
    for stem, query in (("observations", "trend_pool"), ("loud_retests", "pattern_loud")):
        frame = pd.read_csv(folder / f"{stem}.csv", keep_default_na=False)
        csv_rows = [Candidate.model_validate(r).model_dump() for r in frame.to_dict("records")]
        json_rows = [Candidate.model_validate(r).model_dump() for r in queries[query]["rows"]]
        require(len(csv_rows) == len(json_rows), "CSV/snapshot count")
        require(frame.code.is_unique, f"{stem}: duplicate codes")
        for a, b in zip(csv_rows, json_rows, strict=True):
            for key, value in a.items():
                if isinstance(value, float):
                    same(value, b[key], f"CSV/snapshot {key}")
                else:
                    require(value == b[key], f"CSV/snapshot {key}")
            row = Candidate.model_validate(a)
            require(
                row.review_kind == (audit["retest_mode"] if stem == "observations" else "loud")
                or (stem == "observations" and audit["retest_mode"] == "both"),
                "mode",
            )
            i = codes[row.code]
            cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
            require(cols[-1] == len(panel.dates) - 1 and len(cols) >= 251, "eligibility")
            d = pd.DataFrame({k: panel.field(k)[i, cols].astype(float) for k in panel.data})
            d["date"] = panel.dates[cols].astype(str)
            validate_candidate(row, d, cfg)
        counts[stem] = len(csv_rows)
    primary = queries["trend_pool"]["rows"]
    require(counts["observations"] == audit["trend_stocks"], "main total")
    require(counts["loud_retests"] == audit["aligned_stocks"], "loud total")
    require(dict(Counter(r["stage"] for r in primary)) == audit["stages"], "stage totals")
    diagnostics = pd.read_csv(folder / "diagnostics.csv")
    require(
        len(diagnostics) == len(panel.codes) and diagnostics.code.is_unique, "diagnostic coverage"
    )
    require(
        set(diagnostics.loc[diagnostics.selected, "code"]) == {r["code"] for r in primary},
        "selected diagnostic codes",
    )
    excluded = diagnostics.reason.isin(
        [
            "insufficient_history",
            "no_latest_traded_bar",
            "current_ST_or_delisting_name",
        ]
    )
    require(int((~excluded).sum()) == audit["valid_stocks"], "eligible denominator")
    events = pd.read_csv(folder / "event_chain.csv")
    for _, group in events.groupby(["code", "event_date"]):
        require(
            group["pivot"].nunique() == 1 and group["floor"].nunique() == 1, "moving frozen levels"
        )
        require(group.date.min() == group.event_date.iloc[0], "missing breakout event")
    require(
        int((events.date == events.event_date).sum()) == audit["breakout_events_65bars"],
        "breakout total",
    )
    history = pd.read_csv(folder / "history.csv")
    require(set(history.code) == {r["code"] for r in primary} | {"688536.SH"}, "history code scope")
    require(not history.duplicated(["code", "date"]).any(), "duplicate history grain")
    waiting = pd.read_csv(folder / "waiting.csv")
    require(len(waiting) == audit["waiting_stocks"], "waiting total")
    require(not (set(waiting.code) & {r["code"] for r in primary}), "waiting overlaps primary")
    result: dict[str, Any] = {
        "status": "passed",
        "as_of": audit["as_of"],
        "independent_candidates": sum(counts.values()),
        "main": counts["observations"],
        "loud": counts["loud_retests"],
        "diagnostic_rows": len(diagnostics),
        "event_rows": len(events),
        "history_rows": len(history),
        "checks": [
            "source hashes",
            "raw-bar anchors and thresholds",
            "frozen-event replay",
            "CSV/snapshot reconciliation",
            "denominator",
            "event identity",
            "history scope",
        ],
        "not_checked": ["strategy returns", "browser rendered layout"],
        "validator_sha256": sha256(Path(__file__).resolve()),
    }
    (folder / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
