from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "a-share-rotation-trader" / "scripts" / "run_integrated_decision.py"
SPEC = importlib.util.spec_from_file_location("integrated_local_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_integrated_gate_accepts_audited_local_operational_report(tmp_path: Path) -> None:
    report = {
        "verified_at": "2026-08-30 23:39:04",
        "price_adjustment": "verified_qfq",
        "gate_type": "local_operational_consistency",
        "verification_scope": "本地运营一致性验证（非独立外部复权证明）",
        "independent_external_check": "not_required_by_local_mode",
        "verified": 64,
        "sample_size": 64,
        "blocker": None,
        "convention_note": "local gate",
        "full_market_checks": {
            "formula": {
                "formula_mismatch": "0",
                "volume_mismatch": "0",
                "amount_mismatch": "0",
            },
            "latest_anchor": {"anchor_mismatch": "0"},
        },
    }
    (tmp_path / "qfq_verification.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")
    gate = MODULE.gate_qfq(tmp_path, "20260828")
    assert gate["pass"] is True
    assert gate["gate_type"] == "local_operational_consistency"
    assert gate["verification_scope"].startswith("本地运营一致性验证")
    assert "通达信原始锚 64/64" in gate["detail"]
    assert "全库公式错配=0" in gate["detail"]


def test_current_local_gate_report_has_zero_mismatches() -> None:
    path = ROOT / "output" / "qfq_verification.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["price_adjustment"] == "verified_qfq"
    assert report["gate_type"] == "local_operational_consistency"
    assert report["verified"] == report["sample_size"]
    assert report["pass_rate"] == 1.0
    checks = report["full_market_checks"]
    assert int(checks["formula"]["formula_mismatch"]) == 0
    assert int(checks["formula"]["volume_mismatch"]) == 0
    assert int(checks["formula"]["amount_mismatch"]) == 0
    assert int(checks["latest_anchor"]["anchor_mismatch"]) == 0
    assert int(checks["factor"]["invalid_factor"]) == 0
