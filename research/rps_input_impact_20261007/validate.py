"""Inspect saved evidence and freeze this finite diagnostic, not production data."""
import json
from pathlib import Path
import nbformat
import numpy as np
import pandas as pd
from impact import HERE, OUT, ROOT, study

registration = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
assert all(study.sha(Path(path)) == expected for path, expected in registration["hashes"].items())
events = pd.read_csv(OUT / "changed_signals.csv")
expected = [
    {"model":"pv_only","signal_date":"2025-12-02","ts_code":"000019.SZ","change":"removed"},
    {"model":"pv_only","signal_date":"2025-12-15","ts_code":"000001.SZ","change":"added"},
    {"model":"oversold_confirm","signal_date":"2025-11-28","ts_code":"000019.SZ","change":"added"}
]
assert events.to_dict("records") == expected
original = pd.read_parquet(OUT / "amount_original_20_controls.parquet")
updated = pd.read_parquet(OUT / "amount_sensitivity_20_controls.parquet")
assert original.index.equals(updated.index) and original.columns.equals(updated.columns)
finite = original.notna() & updated.notna()
assert ((original != updated) & finite).values.sum() == 29
assert np.array_equal(original.isna().values, updated.isna().values)
for path in [HERE / "review.ipynb"]:
    nb = nbformat.read(path, as_version=4)
    nbformat.validate(nb)
    cells = [cell for cell in nb.cells if cell.cell_type == "code"]
    assert all(cell.execution_count and cell.outputs for cell in cells)
    assert not any(o.output_type == "error" for cell in cells for o in cell.outputs)
for payload in ["reviewed-chart.json","reviewed-sources.json","source-anomaly-receipt.json"]:
    text = (OUT / payload).read_text(encoding="utf-8")
    assert "C:\\Users" not in text and "F:\\" not in text
    json.loads(text)
frozen = {}
for folder in ["rps_pv_20261006/v2","rps_diag_20261006/v1","rps_repair_20261006/v1","rps_input_audit_20261007/v1"]:
    manifest = json.loads((ROOT / "output" / folder / "final_manifest.json").read_text(encoding="utf-8"))
    assert all(study.sha(ROOT / p) == expected_sha for p,expected_sha in manifest["files"].items())
    frozen[folder] = len(manifest["files"])
result = dict(targeted_tests="12 passed", prior_frozen_files_unchanged=frozen,
    changed_stock_dates=3, amount_cells_changed=29, notebook_executed=True, notebook_figure_inspected=True,
    inline_browser_check=False, original_data_modified=False, existing_report_modified=False,
    matching_and_return_effects_quantified=False,
    limitation="Inline browser render not inspected due to prior browser restriction; local figure inspection is not live insertion acceptance.")
(OUT / "validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
paths = sorted(p for folder in [HERE, OUT] for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
paths.append(ROOT / "tests/test_rps_input_impact_20261007.py")
manifest = dict(user_date="2026-10-07", purpose="frozen local sensitivity only",
                files={p.relative_to(ROOT).as_posix():study.sha(p) for p in paths})
dest = OUT / "final_manifest.json"
if dest.exists():
    raise FileExistsError(dest)
dest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
print(f"Frozen {len(manifest['files'])} diagnostic files")
