"""Verify and freeze this finite diagnostic, never approve deployment."""
import json
import re
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path

import nbformat
import pandas as pd

from diagnostic import HERE,ROOT,sha,verify_base

OUT = ROOT / "output/rps_diag_20261006/v1"
EXCHANGE = OUT.parent / "exchange_evidence"
INLINE = Path("C:/Users/pluto4u/.codex/visualizations/2026/10/06/01a10ebb-3124-7793-be00-c5dd5ae18bdb/rps-diagnostic-sources.html")


def main():
    if (OUT / "final_manifest.json").exists():
        raise FileExistsError("Diagnostic is already frozen")
    verify_base()
    done = json.loads((OUT / "completed.json").read_text(encoding="utf-8"))
    assert all(sha(OUT / name)==value for name,value in done["files"].items())
    reg = json.loads((OUT / "registered.json").read_text(encoding="utf-8"))
    assert sha(HERE / "diagnostic.py")==reg["code_sha256"]
    assert sha(HERE / "protocol.json")==reg["protocol_sha256"]
    audits = json.loads((OUT / "verification.json").read_text(encoding="utf-8"))
    assert audits["passed"]==audits["sampled_matched_events"]==224
    reviewed = pd.read_csv(OUT / "reviewed_contrasts.csv")
    old = pd.read_csv(OUT / "contrasts.csv")
    numeric_and_identity = [c for c in old.columns if c!="interval_kind"]
    pd.testing.assert_frame_equal(old[numeric_and_identity],reviewed[numeric_and_identity],check_exact=True)
    assert ((reviewed.interval_kind=="main_98.3333pct")==((reviewed.horizon==20)&(reviewed.comparison=="matched_difference"))).all()
    notebook = nbformat.read(HERE / "review.ipynb",as_version=4)
    nbformat.validate(notebook)
    code_cells = [c for c in notebook.cells if c.cell_type=="code"]
    assert all(c.execution_count is not None and not any(o.output_type=="error" for o in c.outputs) for c in code_cells)
    figures = sum("image/png" in o.get("data",{}) for c in code_cells for o in c.outputs)
    assert figures==2
    raw = pd.read_csv(EXCHANGE / "configured_raw_code_coverage.csv")
    checked_raw = 0
    for r in raw[raw.exists].itertuples():
        assert sha(Path(r.path))==r.sha256
        checked_raw += 1
    cache = json.loads((EXCHANGE / "alternative_coverage.json").read_text(encoding="utf-8"))
    assert sha(Path(cache["retained_cache"])/"panel.npz")==cache["cache_sha256"]
    assert INLINE.is_file() and INLINE.stat().st_size<1_000_000
    fragment = INLINE.read_text(encoding="utf-8")
    assert not fragment.lstrip().lower().startswith("<!doctype")
    assert "data-inline-" in fragment
    args = [sys.executable,"-m","pytest","tests/test_rps_diagnostic.py","tests/test_rps_pv_study.py",
            "tests/test_etf_allocation_study.py","tests/test_etf_forward.py","-q"]
    test = subprocess.run(args,cwd=ROOT,capture_output=True,timeout=60)
    stdout = test.stdout.decode("utf-8",errors="replace")
    stderr = test.stderr.decode("utf-8",errors="replace")
    assert test.returncode==0 and re.search(r"22 passed",stdout), stdout+stderr
    (OUT / "targeted_tests.txt").write_text(stdout+stderr,encoding="utf-8")
    validation = dict(validated_at_utc=datetime.now(timezone.utc).isoformat(),
        targeted_tests_passed=22,test_command=args,
        earlier_wider_unittest_attempt="Unrelated pre-existing test_rps_trajectory could not import pydantic; unittest does not collect pytest-style ETF tests. No claim of full repository test acceptance; scoped pytest suite used.",
        independent_sample_checks_passed=224,frozen_base_unchanged=True,
        notebook=dict(executed=True,cells=len(notebook.cells),code_cells=len(code_cells),png_outputs=figures,
            visual_qa="Both PNG outputs inspected at reading size; units, signs, labels and coverage readable. Refreshed notebook changes only reviewed annotation input, not numeric plotting definitions."),
        sources_receipt=dict(path=str(INLINE),bytes=INLINE.stat().st_size,sha256=sha(INLINE),
            runtime="shared pinned Data renderer",html_fragment_checked=True,
            browser_interaction_qa=False,limitation="Not browser-verified; earlier local browser access was policy-blocked, no bypass attempted."),
        raw_source_hashes_unchanged=checked_raw,retained_cache_hash_unchanged=True,
        universe_gaps_open=True,independent_oos=False,cleared_for_investment=False,
        fixes=["Windows CLI SQL uses normalized whitespace to avoid .cmd newline argument loss; saved SQL preserved.",
            "SSE A+B companies occur twice with different listing dates; retain stock types 1/8, exclude B type 2 before A-code checks.",
            "float32/64 mean accumulation reconciliation tolerance documented; maximum change about 1e-7 percentage points.",
            "Helper interval family labels corrected in reviewed_contrasts.csv with numerical values and original output unchanged."])
    with (OUT / "validation.json").open("x",encoding="utf-8") as f:
        json.dump(validation,f,ensure_ascii=False,indent=2)
    files = []
    for directory in [HERE,OUT.parent]:
        files.extend(f for f in directory.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
    files.append(ROOT / "tests/test_rps_diagnostic.py")
    manifest = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        independent_oos=False,cleared_for_investment=False,source_repairs_promoted=False,
        files={f.relative_to(ROOT).as_posix():sha(f) for f in sorted(set(files))},
        external_inline_source=dict(path=str(INLINE),sha256=sha(INLINE)))
    with (OUT / "final_manifest.json").open("x",encoding="utf-8") as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(frozen_files=len(manifest["files"]),targeted_tests=22,sampled_checks=224,
        raw_hashes_verified=checked_raw,independent_oos=False,cleared_for_investment=False),ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
