from project_paths import script_file
"""Register reconciliation criteria and control codes BEFORE examining differences."""
import json

import numpy as np
import pandas as pd

from common import CACHE, GBBQ, HERE, OLD, OUT, ROOT, now, save, sha, verify_manifest


def main():
    if (HERE / "protocol.json").exists():
        raise FileExistsError("Protocol already registered")
    verified = {"diagnostic": verify_manifest(OLD / "v1/final_manifest.json"),
                "rps_v2": verify_manifest(ROOT / "output/rps_pv_20261006/v2/final_manifest.json")}
    backlog = pd.read_csv(OLD / "exchange_evidence/repair_backlog.csv", dtype={"ticker": str})
    raw = pd.read_csv(OLD / "exchange_evidence/configured_raw_code_coverage.csv")
    hashes = {str(p): sha(p) for p in [CACHE, GBBQ, script_file("run_local_tdx_compare.py"),
              ROOT / "output/tdx_stock_daily_with_rps.parquet",
              OLD / "v1/final_manifest.json", OLD / "exchange_evidence/repair_backlog.csv",
              OLD / "exchange_evidence/configured_raw_code_coverage.csv"]}
    for row in raw[raw.exists].itertuples():
        if sha(row.path) != row.sha256:
            raise RuntimeError(f"Raw file changed since frozen diagnostic: {row.ts_code}")
        hashes[str(row.path)] = row.sha256
    with np.load(CACHE, allow_pickle=False) as z:
        cache_codes = set(map(str, z["codes"]))
    present_codes = set(pd.read_parquet(ROOT / "output/tdx_stock_daily_with_rps.parquet", columns=["ts_code"]).ts_code.astype(str))
    controls = []
    for code in sorted(cache_codes & present_codes):
        ticker, market = code.split(".")
        path = ROOT.__class__(f"C:/new_tdx/vipdoc/{market.lower()}/lday/{market.lower()}{ticker}.day")
        if path.is_file():
            controls.append(dict(ts_code=code, path=str(path), sha256=sha(path)))
        if len(controls) == 20:
            break
    assert len(controls) == 20
    for row in controls:
        hashes[row["path"]] = row["sha256"]
    protocol = dict(registered_at_utc=now(), purpose="data-source reconciliation only; no strategy or performance search",
        input_hashes=hashes, verified_frozen_file_counts=verified,
        study_window=["2018-01-01", "2026-09-30"], adjustment_target="2026-09-30",
        price_tolerance=dict(rtol=1e-5, atol=1e-5), volume_tolerance=dict(rtol=1e-6, atol=1.0),
        amount_tolerance=dict(rtol=1e-6, atol=1.0), controls=controls,
        control_selection="First 20 lexicographic cache/frozen-panel/raw-file intersection, before comparison outcomes",
        alternative_probe=dict(source="BaoStock official Python SDK", codes=["601258.SH", "000540.SZ"],
            window=["2023-06-01", "2023-06-30"], raw_adjustflag="3", overall_timeout_seconds=55,
            maximum_requests="one login; two stock-basic reads and two raw-history reads; logout; no automatic retries"),
        acceptance=dict(automatic_promotion=False, cleared_for_investment=False,
            all_required_security_status_available=False, profit_tests_permitted=False),
        qualifications=["Code-level matching only: official current termination snapshot is not a nonreused historical security ID.",
            "TDX raw prices are cents / 100; raw volume retained as shares, amount as CNY; cross-vendor units must be independently checked.",
            "The adjustment algorithm is reused, not an independent vendor or announcement-date audit.",
            "No calibration of tolerance, no normalized rescaling to hide basis differences; all errors remain visible.",
            "Raw/cache-only dates and post-termination bars are separately reported; missing future prices are not zero returns.",
            "Twenty active controls cannot establish whole-market vendor equivalence.",
            "No existing dataset, signal, experiment, account, or order is modified."])
    OUT.mkdir(parents=True, exist_ok=True)
    (HERE / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    save("registration.json", dict(registered_at_utc=now(), protocol_sha256=sha(HERE / "protocol.json"),
         target_codes=len(backlog), local_raw_codes=int(raw.exists.sum()), controls=controls,
         frozen_inputs_verified=verified))
    print(json.dumps(dict(registered=True, target_codes=len(backlog), controls=len(controls), verified=verified)), flush=True)


if __name__ == "__main__":
    main()
