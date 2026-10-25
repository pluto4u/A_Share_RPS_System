"""Independent sample arithmetic for risk proxies and matched control means."""
import importlib.util
import json

import numpy as np
import pandas as pd

from diagnostic import BASE, ROOT, risk_features, sha, verify_base

OUT = ROOT / "output/rps_diag_20261006/v1"


def main():
    if (OUT / "verification.json").exists():
        raise FileExistsError("Already audited")
    done = json.loads((OUT / "completed.json").read_text(encoding="utf-8"))
    assert all(sha(OUT / name)==digest for name,digest in done["files"].items())
    registration = verify_base()
    samples = pd.read_csv(OUT / "match_audit_sample.csv",parse_dates=["signal_date"])
    frame = pd.read_parquet(ROOT / registration["protocol"]["input"],
        columns=["trade_date","ts_code","close","high","amount","vol"])
    spec = importlib.util.spec_from_file_location("frozen_for_audit",BASE / "study_at_registration.py")
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    panels = {c:frame.pivot(index="trade_date",columns="ts_code",values=c).astype(np.float32)
              for c in ("close","high","amount","vol")}
    masks,_ = study.features(panels["close"],panels["high"],panels["amount"],panels["vol"])
    _,_,cells = risk_features(panels["close"],panels["amount"],masks["universe"])
    codes = samples.code.unique()
    raw = {code:g.set_index("trade_date") for code,g in frame[frame.ts_code.isin(codes)].groupby("ts_code")}
    del frame
    calendar = panels["close"].index
    labels = {h:pd.read_parquet(BASE / f"future_labels_{h}d.parquet") for h in (5,20)}
    checks = []
    for r in samples.itertuples():
        i = calendar.get_loc(r.signal_date)
        j = panels["close"].columns.get_loc(r.code)
        records = raw[r.code]
        close21 = records.reindex(calendar[i-21:i]).close.to_numpy(dtype=float)
        independent_vol = np.std(np.log(close21[1:]/close21[:-1]),ddof=1)
        independent_amount = float(np.median(records.reindex(calendar[i-20:i]).amount))
        ca = masks[r.candidate].iloc[i].to_numpy()
        ba = masks[r.baseline].iloc[i].to_numpy()
        control_mask = ba & ~ca & (cells.iloc[i].to_numpy()==r.cell)
        y = labels[r.horizon].iloc[i].to_numpy(dtype=float)
        control_values = y[control_mask & np.isfinite(y)]
        control_mean = float(np.mean(control_values))
        passed = (abs(independent_vol-r.vol20)<1e-7 and
                  np.isclose(independent_amount,r.amount20,rtol=1e-6,atol=.1) and
                  int(control_mask.sum())==r.known_controls and len(control_values)==r.valid_controls and
                  abs(control_mean-r.control_mean)<1e-10 and abs(y[j]-r.candidate_label)<1e-10 and
                  abs((y[j]-control_mean)-r.difference)<1e-10)
        checks.append(dict(code=r.code,signal_date=str(r.signal_date.date()),horizon=r.horizon,
            candidate=r.candidate,baseline=r.baseline,independent_vol20=independent_vol,
            independent_amount20=independent_amount,independent_control_mean=control_mean,
            known_controls=int(control_mask.sum()),valid_controls=len(control_values),passed=bool(passed)))
    pd.DataFrame(checks).to_csv(OUT / "independent_checks.csv",index=False)
    counts = pd.read_csv(OUT / "matching_coverage.csv")
    assert (counts.selected==counts.labels+counts.unavailable).all()
    assert (counts.matched_used_labels<=counts.matched_valid_labels).all()
    assert (counts.matched_valid_labels<=counts.known_cell_matched_selected).all()
    assert (counts.known_cell_matched_selected<=counts.proxy_available_selected).all()
    assert (counts.matched_valid_labels==counts.matched_used_labels+counts.no_comparable_control_label).all()
    # Reconcile original differences with frozen v2, not merely their signs.
    new = pd.read_csv(OUT / "contrasts.csv")
    old = pd.read_csv(BASE / "contrasts.csv")
    frozen = new[new.comparison=="unmatched_frozen_difference"].merge(old,on=["candidate","baseline","period","horizon"],suffixes=("_new","_old"))
    assert len(frozen)==12
    # v2 group means use pandas float32 accumulation; this diagnostic casts
    # frozen labels to float64. Tolerance is 1e-7 in simple-return units.
    tolerance_pp = 1e-5
    np.testing.assert_allclose(frozen.mean_pp_new,frozen.mean_pp_old,rtol=0,atol=tolerance_pp)
    np.testing.assert_allclose(frozen.daily_mean_pp_new,frozen.daily_mean_pp_old,rtol=0,atol=tolerance_pp)
    assert all(c["passed"] for c in checks)
    report = dict(sampled_matched_events=len(checks),passed=sum(c["passed"] for c in checks),
        fingerprints_verified=True,frozen_base_unchanged=True,unmatched_points_reconciled_to_frozen_v2=True,
        signal_label_matching_counts_reconcile=True,
        reconciliation_tolerance_pp=tolerance_pp,
        maximum_original_point_difference_pp=float(max((frozen.mean_pp_new-frozen.mean_pp_old).abs().max(),
            (frozen.daily_mean_pp_new-frozen.daily_mean_pp_old).abs().max())),
        interpretation_note="Only matched_difference at horizon=20 is in the three-primary-comparison family. Other precomputed intervals are descriptive, not additional primary tests.",
        limitations=["Sample risk/mean arithmetic; signal selection reuses hashed frozen definitions, not an independent complete signal implementation.",
            "No complete execution audit or unbiased historic security universe; not profitability certification."])
    (OUT / "verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
