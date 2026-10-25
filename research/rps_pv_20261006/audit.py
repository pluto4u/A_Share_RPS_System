"""Independent sampled label arithmetic, boundaries and output fingerprints."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from study import ROOT, sha

OUT = ROOT / "output/rps_pv_20261006/v2"


def main():
    reg = json.loads((OUT / "registered.json").read_text(encoding="utf-8"))
    done = json.loads((OUT / "completed.json").read_text(encoding="utf-8"))
    assert all(sha(OUT / name) == digest for name, digest in done["files"].items())
    assert sha(Path(reg["source_path"])) == reg["source_sha256"]
    assert sha(OUT / "study_at_registration.py") == reg["code_sha256"]
    source = Path(reg["source_path"])
    preview = pd.read_csv(OUT / "signal_feature_preview.csv")
    sampled = preview.sample(min(80, len(preview)), random_state=20261006)
    codes = sampled.code.unique().tolist()
    frame = pd.read_parquet(source, columns=["trade_date", "ts_code", "open", "vol"],
                            filters=[("ts_code", "in", codes)])
    by_code = {code: group.set_index("trade_date") for code, group in frame.groupby("ts_code")}
    checks = []
    for h in (5, 20):
        labels = pd.read_parquet(OUT / f"future_labels_{h}d.parquet", columns=codes)
        calendar = labels.index
        for row in sampled.itertuples(index=False):
            signal = pd.Timestamp(row.signal_date)
            i = calendar.get_loc(signal)
            observed = labels.loc[signal, row.code]
            expected = np.nan
            if i+h+1 < len(calendar):
                entry, exit_ = calendar[i+1], calendar[i+h+1]
                records = by_code[row.code]
                if entry in records.index and exit_ in records.index:
                    a, b = records.loc[entry], records.loc[exit_]
                    if a.open > 0 and b.open > 0 and a.vol > 0 and b.vol > 0:
                        expected = b.open/a.open-1
            passed = ((pd.isna(observed) and pd.isna(expected)) or
                      (pd.notna(observed) and pd.notna(expected) and abs(observed-expected) < 1e-6))
            checks.append(dict(code=row.code, signal_date=row.signal_date, horizon=h,
                               observed=observed, independently_recomputed=expected, passed=bool(passed)))
    summary = pd.read_csv(OUT / "summary.csv")
    assert (summary.signals == summary.labels+summary.unavailable).all()
    assert (summary.valid_days <= summary.signal_days).all()
    pivot = summary.pivot(index=["model", "period", "horizon"], columns="friction_pct",
                          values="cost_proxy_daily_mean_pct")
    assert np.allclose(pivot[.5]-pivot[1.], .5, atol=1e-5)
    pd.DataFrame(checks).to_csv(OUT / "independent_label_checks.csv", index=False)
    report = dict(sampled_labels=len(checks), passed=sum(c["passed"] for c in checks),
                  output_fingerprints_match=True, signal_label_counts_reconcile=True,
                  costs_reconcile=True, code_snapshot_verified=True,
                  limitation="sampled labels only; no independent complete signal or execution audit; source coverage remains biased")
    (OUT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    assert all(c["passed"] for c in checks)


if __name__ == "__main__":
    main()
