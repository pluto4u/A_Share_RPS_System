"""Close this bounded phase; never approve deployment or overwrite old studies."""
import json
import re
import subprocess
import sys
from pathlib import Path

import nbformat
import pandas as pd

from common import HERE, OLD, OUT, ROOT, guard, now, save, sha, verify_manifest

INLINE = Path("C:/Users/pluto4u/.codex/visualizations/2026/10/06/01a10ebb-3124-7793-be00-c5dd5ae18bdb")


def main():
    guard()
    unchanged = {"diagnostic": verify_manifest(OLD / "v1/final_manifest.json"),
        "rps_v2": verify_manifest(ROOT / "output/rps_pv_20261006/v2/final_manifest.json")}
    frame = pd.read_csv(OUT / "reconciliation_by_code.csv")
    assert len(frame) == 126 and frame.numeric_overlap_agrees.sum() == 126
    assert not frame.accepted_for_research.any()
    sources = json.loads((OUT / "inline_additional_sources.json").read_text(encoding="utf-8"))
    assert "40条返回记录都标为停牌" in str(sources)
    quality = json.loads((OUT / "source_quality.json").read_text(encoding="utf-8"))
    assert quality["frozen_input_reproduces_raw_disagreements"] == quality["large_liquidity_disagreement_cells"] == 33
    assert sum(r["returned_rows"] for r in quality["baostock_probes"]) == 40
    assert sum(r["tradestatus_0_rows"] for r in quality["baostock_probes"]) == 40
    assert sum(r["usable_traded_rows"] for r in quality["baostock_probes"]) == 0
    notebook = nbformat.read(HERE / "review.ipynb", as_version=4)
    nbformat.validate(notebook)
    cells = [c for c in notebook.cells if c.cell_type == "code"]
    assert all(c.execution_count is not None and not any(o.output_type == "error" for o in c.outputs) for c in cells)
    pngs = sum("image/png" in o.get("data", {}) for c in cells for o in c.outputs)
    assert pngs == 1
    args = [sys.executable, "-m", "pytest", "tests/test_rps_repair_20261006.py", "tests/test_rps_diagnostic.py",
            "tests/test_rps_pv_study.py", "tests/test_etf_allocation_study.py", "tests/test_etf_forward.py", "-q"]
    tests = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=55)
    stdout = tests.stdout.decode("utf-8", errors="replace")
    stderr = tests.stderr.decode("utf-8", errors="replace")
    assert tests.returncode == 0 and re.search(r"31 passed", stdout), stdout + stderr
    (OUT / "targeted_tests.txt").write_text(stdout + stderr, encoding="utf-8")
    inline = [INLINE / name for name in ["rps-history-repair.html", "rps-history-repair-sources.html", "rps-input-quality-sources.html"]]
    for path in inline:
        assert path.is_file() and path.stat().st_size < 1_000_000
        text = path.read_text(encoding="utf-8")
        assert "data-inline-" in text and not text.lstrip().lower().startswith("<!doctype")
    db = json.loads((OUT / "hithink_export_receipt.json").read_text(encoding="utf-8"))
    assert db["db_sha256_before"] == db["db_sha256_after"] == sha(ROOT / "data/hithink/market.duckdb")
    save("validation.json", dict(validated_at_utc=now(), targeted_tests_passed=31,
        full_repository_test_acceptance=False, frozen_prior_files_verified=unchanged, db_unchanged=True,
        executed_notebook=dict(cells=len(notebook.cells), code_cells=len(cells), png_outputs=pngs,
            visual_qa="Coverage PNG inspected; labels and counts legible."),
        inline=dict(runtime="Shared prebuilt Data chart and Sources runtimes", fragment_sizes_checked=True,
            browser_qa=False, limitation="No browser bypass of prior local-page policy block attempted."),
        technical_repairs=["Date-resolution normalization preserves timestamps/order and registered numerical tolerances.",
            "Review prose initially misread two blank isST fields as blank trade status; corrected to 40 suspended rows. Raw responses, numerical QA and notebook tables retained unchanged."],
        strategy_parameters_changed=False, source_repairs_promoted=False, cleared_for_investment=False))
    files = [p for directory in [HERE, OUT] for p in directory.rglob("*") if p.is_file()
        and not any(part.startswith(".venv") or part == "__pycache__" for part in p.parts)]
    files.append(ROOT / "tests/test_rps_repair_20261006.py")
    manifest = dict(frozen_at_utc=now(), independent_oos=False, cleared_for_investment=False,
        source_repairs_promoted=False, files={p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(files))},
        external_inline={str(p): sha(p) for p in inline})
    save("final_manifest.json", manifest)
    print(json.dumps(dict(frozen_files=len(manifest["files"]), targeted_tests_passed=31,
        prior_files_verified=unchanged, cleared_for_investment=False), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
