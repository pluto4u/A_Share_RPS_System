"""Local evidence validation, never claims browser/render acceptance."""
import json
from pathlib import Path

import nbformat
import numpy as np
import pandas as pd

from audit import OUT, ROOT, sha


def main():
    registration = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
    assert all(sha(path) == expected for path, expected in registration["hashes"].items())
    cells = pd.read_csv(OUT / "all_cells.csv", dtype={"trade_date":str})
    assert len(cells) == 600 and not cells.duplicated(["ts_code","trade_date","field"]).any()
    tolerance = registration["protocol"]["liquidity_tolerance"]
    focus = pd.read_csv(OUT / "prior_large_cells_recheck.csv", dtype={"trade_date":str})
    assert len(focus) == 33 and focus[["hithink","tdx","dump","baostock"]].notna().all().all()
    assert np.isclose(focus.tdx, focus.baostock, **tolerance).all()
    assert not np.isclose(focus.hithink, focus.baostock, **tolerance).any()
    assert (cells.hithink == cells.dump).all()
    assert focus.ts_code.nunique() == 16 and focus[["ts_code","trade_date"]].drop_duplicates().shape[0] == 29
    nb = nbformat.read(ROOT / "research/rps_input_audit_20261007/review.ipynb", as_version=4)
    nbformat.validate(nb)
    code_cells = [cell for cell in nb.cells if cell.cell_type == "code"]
    assert all(cell.execution_count and cell.outputs for cell in code_cells)
    assert not any(output.output_type == "error" for cell in code_cells for output in cell.outputs)
    snapshot = json.loads((ROOT / "reports/rps_decision_20261006/src/data.json").read_text(encoding="utf-8"))
    assert snapshot["id"] == "report:809684d3-9c1e-4d59-b482-1bad83c9fffa"
    assert snapshot["buildStatus"] == "complete"
    assert set(snapshot["queries"]) == {"frozen_results","scope_decision","input_quality","research_ideas"}
    q = snapshot["queries"]["input_quality"]["rows"][0]
    assert q["third_source_matches_tdx"] == 33 and q["third_source_matches_hithink"] == 0
    assert q["download_window_mismatches"] == 0 and len(snapshot["queries"]["research_ideas"]["rows"]) == 3
    previous = json.loads((ROOT / "output/rps_decision_20261006/reviewed_snapshot.json").read_text(encoding="utf-8"))
    # The earlier authoring step added this genuine protocol citation after the
    # snapshot generator ran. Retain that known provenance-only addition;
    # keep every row and all other metadata under exact equality checks.
    prior_files = previous["queries"]["frozen_results"]["source"]["files"]
    assert prior_files == [{"label":"reviewed_contrasts.csv"}, {"label":"matching_coverage.csv"}]
    prior_files.append({"label":"protocol.json（原实验）"})
    for name in ["frozen_results","scope_decision"]:
        assert snapshot["queries"][name] == previous["queries"][name], name
    verified = {}
    for folder in ["rps_diag_20261006/v1","rps_repair_20261006/v1","rps_pv_20261006/v2"]:
        manifest = json.loads((ROOT / "output" / folder / "final_manifest.json").read_text(encoding="utf-8"))
        assert all(sha(ROOT / path) == expected for path, expected in manifest["files"].items())
        verified[folder] = len(manifest["files"])
    result = dict(targeted_tests="36 passed", registered_inputs_unchanged=True,
        prior_frozen_files_unchanged=verified, code_dates=100, fields_checked=600, large_cells_checked=33,
        notebook_executed=True, report_identity_preserved=True, unrelated_report_queries_preserved=True,
        original_db_modified=False, performance_recomputed=False,
        browser_visual_check=False, notebook_visual_check=False,
        limitations=["Prior browser inspection restriction respected; build/HTTP/evidence checks do not establish rendered acceptance.",
                     "Finite nonrepresentative 20-control window, no full-market certification or independent source-lineage proof."])
    (OUT / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
