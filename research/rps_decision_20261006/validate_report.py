"""Artifact-local evidence checks, not browser/visual or strategy acceptance."""
import json
from datetime import datetime, timezone

import pandas as pd

from prepare_snapshot import HERE, OUT, ROOT, sha


def main():
    project = ROOT / "reports/rps_decision_20261006"
    snapshot = json.loads((project / "src/data.json").read_text(encoding="utf-8"))
    assert snapshot["surface"] == "report" and snapshot["buildStatus"] == "complete"
    assert len(snapshot["queries"]) == 3 and snapshot["id"].startswith("report:")
    old = pd.read_csv(ROOT / "output/rps_diag_20261006/v1/reviewed_contrasts.csv")
    old = old[(old.horizon == 20) & (old.comparison == "matched_difference")]
    names = {"rps_pv":"RPS＋量价 vs 仅RPS", "low_turn":"低位转强 vs 仅低位", "oversold_confirm":"超跌确认 vs 仅超跌"}
    checked = 0
    for row in snapshot["queries"]["frozen_results"]["rows"]:
        candidate = next(k for k,v in names.items() if v == row["signal"])
        period = "early" if row["period"] == "2018—2021" else "late"
        source = old[(old.candidate == candidate) & (old.period == period)].iloc[0]
        assert row["mean_pp"] == source.mean_pp and row["lower_pp"] == source.lo_pp and row["upper_pp"] == source.hi_pp
        assert row["estimate"] == f"{source.mean_pp:+.3f}"
        assert row["interval"] == f"[{source.lo_pp:+.3f}, {source.hi_pp:+.3f}]"
        checked += 1
    assert checked == 6
    quality = snapshot["queries"]["input_quality"]["rows"][0]
    assert quality["large_difference_cells"] == quality["frozen_reproduced_cells"] == 33
    assert quality["affected_code_dates"] == 29 and quality["affected_codes"] == 16
    code = (project / "src/content/report/ReportContent.jsx").read_text(encoding="utf-8")
    assert "冻结规则" in code and "未来持仓若终止上市" in code
    assert "usage_summary" not in code and "Synthetic example" not in code
    assert "停止专项补数" in code and "独立样本外" in code
    for query in snapshot["queries"].values():
        encoded = json.dumps(query, ensure_ascii=False)
        assert "F:\\" not in encoded and "C:\\Users" not in encoded
    verified = {}
    for name, folder in [("diagnostic", "rps_diag_20261006/v1"), ("repair", "rps_repair_20261006/v1")]:
        manifest = json.loads((ROOT / "output" / folder / "final_manifest.json").read_text(encoding="utf-8"))
        assert all(sha(ROOT / f) == expected for f,expected in manifest["files"].items())
        verified[name] = len(manifest["files"])
    result = dict(validated_at_utc=datetime.now(timezone.utc).isoformat(), report_id=snapshot["id"],
        result_estimates_and_intervals_reconciled=checked, previous_frozen_files_unchanged=verified,
        strategy_recomputed=False, terminated_stock_research_stopped=True,
        browser_visual_check=False, limitation="Prior local browser inspection policy block respected; build/evidence/HTTP checks are not rendered acceptance.",
        files={p.relative_to(ROOT).as_posix():sha(p) for p in [HERE / "RESEARCH_SCOPE.md", project / "src/data.json", project / "src/content/report/ReportContent.jsx"]})
    (OUT / "report_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
