"""Correct interval-family annotations, preserving original numerical results."""
import csv
import json
import sys
from datetime import datetime,timezone

from diagnostic import ROOT,sha

OUT = ROOT / "output/rps_diag_20261006/v1"


def main():
    if (OUT / "final_manifest.json").exists():
        raise RuntimeError("Frozen annotations cannot be refreshed")
    if (OUT / "reviewed_contrasts.csv").exists() and "--refresh" not in sys.argv:
        raise FileExistsError("Reviewed results already exist")
    # Preserve numeric field strings exactly: parsing/re-serializing floats
    # during an annotation-only correction can otherwise alter the last ULP.
    with (OUT / "contrasts.csv").open(newline="",encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        original = list(reader)
    reviewed = [dict(r) for r in original]
    changed = 0
    for r in reviewed:
        if r["horizon"]=="20" and r["comparison"]!="matched_difference":
            r["interval_kind"] = "descriptive_secondary_not_primary_family"
            changed += 1
    assert all({k:v for k,v in a.items() if k!="interval_kind"} ==
               {k:v for k,v in b.items() if k!="interval_kind"} for a,b in zip(original,reviewed))
    with (OUT / "reviewed_contrasts.csv").open("w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(reviewed)
    report = dict(annotated_at_utc=datetime.now(timezone.utc).isoformat(),
        correction="Original interval_kind labeled all horizon20 helper comparisons as main; only three matched_difference contrasts per period are primary. Helper bounds are descriptive.",
        original_file="contrasts.csv",original_sha256=sha(OUT / "contrasts.csv"),
        reviewed_file="reviewed_contrasts.csv",reviewed_sha256=sha(OUT / "reviewed_contrasts.csv"),
        changed_rows=changed,changed_columns=["interval_kind"],numeric_field_strings_unchanged=True,
        numerical_values_unchanged=True,signal_thresholds_changed=False,primary_tests_changed=False,
        original_output_and_registration_preserved=True)
    (OUT / "annotation_notes.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
