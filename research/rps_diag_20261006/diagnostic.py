"""Registered post-hoc diagnostics of immutable RPS signals; not a new strategy."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / "output/rps_pv_20261006/v2"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024*1024):
            h.update(chunk)
    return h.hexdigest()


def write_new(path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)


def verify_base():
    manifest = json.loads((BASE / "final_manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError(f"Frozen file changed: {relative}")
    registration = json.loads((BASE / "registered.json").read_text(encoding="utf-8"))
    if sha(ROOT / registration["protocol"]["input"]) != registration["source_sha256"]:
        raise ValueError("Frozen input changed")
    return registration


def risk_features(close, amount, eligible):
    """Only sessions <= s-1; amount is a liquidity proxy, NEVER market cap."""
    logret = np.log(close.astype(float) / close.astype(float).shift(1))
    volatility = logret.shift(1).rolling(20, min_periods=20).std(ddof=1)
    liquidity = amount.shift(1).rolling(20, min_periods=20).median()
    # Rank only known same-day eligible stocks. Average ties stay in one cell.
    vrank = volatility.where(eligible).rank(axis=1, method="average", pct=True)
    arank = liquidity.where(eligible).rank(axis=1, method="average", pct=True)
    vbin = (np.ceil(vrank*5)-1).clip(0, 4)
    abin = (np.ceil(arank*5)-1).clip(0, 4)
    cells = (vbin*5+abin).fillna(-1).astype(np.int8)
    return volatility, liquidity, cells


def mean_valid(x, mask):
    values = x[mask & np.isfinite(x)]
    return float(values.mean()) if len(values) else np.nan


def match_day(candidate, baseline, cell, label):
    """Overlap removed. Known cell coverage is invariant to future labels."""
    candidate = np.asarray(candidate, dtype=bool)
    baseline = np.asarray(baseline, dtype=bool)
    cell = np.asarray(cell, dtype=int)
    label = np.asarray(label, dtype=float)
    controls = baseline & ~candidate & (cell >= 0)
    candidates = candidate & (cell >= 0)
    known_control_n = np.bincount(cell[controls], minlength=25)
    known_matched = candidates.copy()
    known_matched[candidates] &= known_control_n[cell[candidates]] >= 5
    available = np.isfinite(label)
    control_valid = controls & available
    count = np.bincount(cell[control_valid], minlength=25)
    total = np.bincount(cell[control_valid], weights=label[control_valid], minlength=25)
    control_mean = np.divide(total, count, out=np.full(25, np.nan), where=count > 0)
    matched_labels = known_matched & available
    usable = matched_labels.copy()
    usable[matched_labels] &= np.isfinite(control_mean[cell[matched_labels]])
    residual = label[usable] - control_mean[cell[usable]]
    frozen = mean_valid(label, candidate) - mean_valid(label, baseline)
    excl = mean_valid(label, candidate) - mean_valid(label, baseline & ~candidate)
    return dict(selected=int(candidate.sum()), labels=int((candidate & available).sum()),
        unavailable=int((candidate & ~available).sum()),
        proxy_available_selected=int(candidates.sum()), known_cell_matched_selected=int(known_matched.sum()),
        matched_valid_labels=int(matched_labels.sum()),
        no_comparable_control_label=int((matched_labels & ~usable).sum()),
        matched_used_labels=int(usable.sum()), matched_cells=int(len(np.unique(cell[usable]))),
        unmatched_frozen_difference=frozen, unmatched_excluding_candidate_difference=excl,
        same_matched_candidates_unmatched_control_difference=mean_valid(label, usable)-mean_valid(label, baseline & ~candidate),
        matched_difference=float(residual.mean()) if len(residual) else np.nan), usable, control_mean


def block_interval(values):
    monthly = values.resample("ME").mean().dropna()
    if len(monthly) < 12:
        return dict(months=len(monthly), mean_pp=float(monthly.mean()*100),
                    daily_mean_pp=float(values.mean()*100), lo_pp=None, hi_pp=None)
    x = monthly.to_numpy()
    rng = np.random.default_rng(20261006)
    starts = rng.integers(0, len(x), size=(5000, int(np.ceil(len(x)/3))))
    indices = (starts[:, :, None] + np.arange(3)) % len(x)
    draws = x[indices.reshape(5000, -1)[:, :len(x)]].mean(axis=1)
    # Three registered MAIN comparisons per historical period: 98.3333%.
    lo, hi = np.quantile(draws, [.05/6, 1-.05/6])
    return dict(months=len(x), mean_pp=float(monthly.mean()*100), daily_mean_pp=float(values.mean()*100),
                lo_pp=float(lo*100), hi_pp=float(hi*100))


def period_label_mask(index, start, end, horizon):
    dates = pd.Series(index, index=index)
    exit_date = dates.shift(-(horizon+1))
    return (dates.between(start, end) & exit_date.notna() & (exit_date <= pd.Timestamp(end))).to_numpy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    p = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    registration = verify_base()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    own = dict(registered_at_utc=datetime.now(timezone.utc).isoformat(), protocol=p,
        protocol_sha256=sha(HERE / "protocol.json"), code_sha256=sha(Path(__file__)),
        base_manifest_sha256=sha(BASE / "final_manifest.json"), input_sha256=registration["source_sha256"],
        post_hoc_diagnostic=True, independent_oos=False, cleared_for_investment=False)
    write_new(out / "registered.json", own)
    (out / "diagnostic_at_registration.py").write_bytes(Path(__file__).read_bytes())
    print("registered diagnostics; frozen base/input hashes verified", flush=True)
    spec = importlib.util.spec_from_file_location("frozen_rps_study", BASE / "study_at_registration.py")
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    # Exact original float32 panels and masks. Frozen source is never edited.
    prices, quality = study.load_inputs(ROOT / p["input"], "2026-09-30", out)
    masks, descriptors = study.features(prices["close"], prices["high"], prices["amount"], prices["vol"])
    old_counts = pd.read_csv(BASE / "signal_counts.csv", index_col=0, parse_dates=True)
    new_counts = pd.DataFrame({m:mask.sum(axis=1) for m,mask in masks.items()})
    np.testing.assert_array_equal(new_counts.to_numpy(), old_counts.to_numpy())
    new_counts.to_csv(out / "signal_counts.csv", index_label="signal_date")
    del descriptors
    volatility, liquidity, cells = risk_features(prices["close"], prices["amount"], masks["universe"])
    print("frozen signal counts identical; past-only risk cells ready", flush=True)
    contrasts = p["contrasts"]
    models = list(dict.fromkeys(model for pair in contrasts for model in pair))
    idx = prices["close"].index
    daily_return = prices["close"] / prices["close"].shift(1) - 1
    overnight = prices["open"].shift(-1) / prices["close"] - 1
    combined = prices["open"].shift(-1) / prices["close"].shift(1) - 1
    confirmation_rows, risk_rows = [], []
    for period, (start,end) in p["periods"].items():
        in_period = np.flatnonzero((idx >= start) & (idx <= end))
        for model in models:
            for i in in_period:
                mask = masks[model].iloc[i].to_numpy()
                if not mask.any():
                    continue
                next_in_period = i+1 < len(idx) and idx[i+1] <= pd.Timestamp(end)
                confirmation_rows.append(dict(signal_date=idx[i], period=period, model=model,
                    selected=int(mask.sum()), signal_day_pct=mean_valid(daily_return.iloc[i].to_numpy(),mask)*100,
                    next_open_gap_pct=mean_valid(overnight.iloc[i].to_numpy(),mask)*100 if next_in_period else np.nan,
                    total_to_next_open_pct=mean_valid(combined.iloc[i].to_numpy(),mask)*100 if next_in_period else np.nan,
                    next_open_available=int((mask & np.isfinite(overnight.iloc[i].to_numpy())).sum()) if next_in_period else 0))
                risk_rows.append(dict(signal_date=idx[i], period=period, model=model, selected=int(mask.sum()),
                    previous20_daily_vol_pct=mean_valid(volatility.iloc[i].to_numpy(),mask)*100,
                    previous20_median_amount_million=mean_valid(liquidity.iloc[i].to_numpy(),mask)/1e6))
    confirm_daily = pd.DataFrame(confirmation_rows)
    confirm_daily.to_parquet(out / "confirmation_daily.parquet", index=False)
    confirm_summary = confirm_daily.groupby(["period","model"]).agg(
        signals=("selected","sum"), signal_days=("selected","size"),
        signal_day_pct=("signal_day_pct","mean"), next_open_gap_pct=("next_open_gap_pct","mean"),
        total_to_next_open_pct=("total_to_next_open_pct","mean"), next_open_available=("next_open_available","sum"))
    confirm_summary.to_csv(out / "confirmation_summary.csv")
    risk_daily = pd.DataFrame(risk_rows)
    risk_daily.to_parquet(out / "risk_daily.parquet", index=False)
    risk_daily.groupby(["period","model"]).agg(signals=("selected","sum"),
        previous20_daily_vol_pct=("previous20_daily_vol_pct","mean"),
        previous20_median_amount_million=("previous20_median_amount_million","mean")).to_csv(out / "risk_summary.csv")
    del daily_return, overnight, combined
    match_rows, audit_rows = [], []
    cell_array = cells.to_numpy()
    mask_arrays = {k:v.to_numpy() for k,v in masks.items()}
    for horizon in (20,5):
        labels = pd.read_parquet(BASE / f"future_labels_{horizon}d.parquet")
        assert labels.index.equals(idx) and labels.columns.equals(prices["close"].columns)
        for period, (start,end) in p["periods"].items():
            allowed = period_label_mask(idx,start,end,horizon)
            for candidate,baseline in contrasts:
                for i in np.flatnonzero((idx >= start) & (idx <= end)):
                    ca, ba, cell = mask_arrays[candidate][i], mask_arrays[baseline][i], cell_array[i]
                    y = labels.iloc[i].to_numpy(dtype=float) if allowed[i] else np.full(len(cell), np.nan)
                    stats, usable, means = match_day(ca, ba, cell, y)
                    match_rows.append(dict(signal_date=idx[i], candidate=candidate, baseline=baseline,
                        horizon=horizon, period=period, **stats))
                    # Deterministic audit sample, no selection by outcome sign.
                    if allowed[i] and i % 41 == 0 and usable.any():
                        j = int(np.flatnonzero(usable)[0])
                        controls = ba & ~ca & (cell == cell[j])
                        audit_rows.append(dict(signal_date=idx[i], candidate=candidate, baseline=baseline,
                            period=period,horizon=horizon,code=prices["close"].columns[j],cell=int(cell[j]),
                            known_controls=int(controls.sum()),valid_controls=int((controls & np.isfinite(y)).sum()),
                            candidate_label=float(y[j]), control_mean=float(means[cell[j]]),
                            difference=float(y[j]-means[cell[j]]), vol20=float(volatility.iloc[i,j]),
                            amount20=float(liquidity.iloc[i,j])))
        print(f"matched frozen horizon={horizon}", flush=True)
    daily = pd.DataFrame(match_rows)
    daily.to_parquet(out / "matched_daily.parquet", index=False)
    pd.DataFrame(audit_rows).to_csv(out / "match_audit_sample.csv", index=False)
    summaries, intervals = [], []
    for (period,candidate,baseline,horizon),g in daily.groupby(["period","candidate","baseline","horizon"]):
        counts = {key:int(g[key].sum()) for key in ["selected","labels","unavailable",
            "proxy_available_selected","known_cell_matched_selected","matched_valid_labels",
            "no_comparable_control_label","matched_used_labels"]}
        summaries.append(dict(period=period,candidate=candidate,baseline=baseline,horizon=int(horizon),
            **counts, known_match_coverage_pct=100*counts["known_cell_matched_selected"]/counts["selected"],
            valid_match_coverage_pct=100*counts["matched_used_labels"]/counts["labels"] if counts["labels"] else 0,
            valid_match_days=int(g.matched_difference.notna().sum())))
        for name in ["unmatched_frozen_difference", "unmatched_excluding_candidate_difference",
                     "same_matched_candidates_unmatched_control_difference", "matched_difference"]:
            s = g.set_index("signal_date")[name]
            intervals.append(dict(period=period,candidate=candidate,baseline=baseline,horizon=int(horizon),
                comparison=name, paired_days=int(s.notna().sum()),
                interval_kind="main_98.3333pct" if horizon == 20 else "descriptive_sensitivity_only",
                **block_interval(s)))
    pd.DataFrame(summaries).to_csv(out / "matching_coverage.csv",index=False)
    result = pd.DataFrame(intervals)
    result.to_csv(out / "contrasts.csv",index=False)
    verify_base()
    if sha(Path(__file__)) != own["code_sha256"] or sha(HERE / "protocol.json") != own["protocol_sha256"]:
        raise ValueError("Diagnostic code/protocol changed during run")
    write_new(out / "completed.json", dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),
        frozen_base_unchanged=True, input_unchanged=True, signal_counts_identical=True,
        cleared_for_investment=False, independent_oos=False,
        files={f.name:sha(f) for f in sorted(out.iterdir()) if f.is_file()}))
    print(result[(result.horizon==20)&(result.comparison=="matched_difference")].to_string(index=False),flush=True)
    print(confirm_summary.to_string(),flush=True)


if __name__ == "__main__":
    main()
