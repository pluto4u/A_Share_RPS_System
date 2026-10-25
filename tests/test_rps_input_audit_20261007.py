import importlib.util
from pathlib import Path

import numpy as np

PATH = Path(__file__).resolve().parents[1] / "research/rps_input_audit_20261007/audit.py"
spec = importlib.util.spec_from_file_location("input_audit_20261007", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_null_is_not_agreement():
    assert audit.compare(None, 1, "amount", audit.protocol()) is None
    assert audit.compare(np.nan, np.nan, "vol", audit.protocol()) is None


def test_multiplier_is_not_rounding():
    assert audit.compare(10118471, 101184710, "vol", audit.protocol()) is False


def test_float32_amount_rounding_is_allowed():
    assert audit.compare(1178308992, 1178308953.13, "amount", audit.protocol()) is True


def test_prices_have_separate_tolerance():
    assert audit.compare(11.71, 11.71, "close", audit.protocol()) is True
    assert audit.compare(11.71, 11.72, "close", audit.protocol()) is False


def test_registered_scope_is_finite():
    p = audit.protocol()
    assert len(p["codes"]) == len(set(p["codes"])) == 20
    assert p["window"] == ["2025-11-26", "2025-12-02"]
    assert p["third_source"]["adjustflag"] == "3"
    assert p["third_source"]["retries"] == 0
