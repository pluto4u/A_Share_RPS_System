import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "archive_review", Path(__file__).resolve().parents[1] / "tools/review_strategy_archive.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_audit_detects_drift_and_missing_without_mutation(tmp_path):
    source = tmp_path / "signal.py"
    source.write_text("x = 1\n", encoding="utf-8")
    original = review.source_hash(source)
    registry = {"strategies": [{"id": "a", "name": "A", "source": "signal.py",
                                "source_sha256": original}]}
    assert review.audit(registry, tmp_path)[0]["source_state"] == "unchanged"
    source.write_text("x = 2\n", encoding="utf-8")
    row = review.audit(registry, tmp_path)[0]
    assert row["source_state"] == "changed"
    assert row["performance_verified"] is False
    assert registry["strategies"][0]["source_sha256"] == original
    source.unlink()
    assert review.audit(registry, tmp_path)[0]["source_state"] == "missing"


def test_hash_matches_archive_normalization(tmp_path):
    source = tmp_path / "signal.py"
    source.write_bytes(b"\xef\xbb\xbfx = 1\r\n")
    first = review.source_hash(source)
    source.write_bytes(b"x = 1\n")
    assert review.source_hash(source) == first
