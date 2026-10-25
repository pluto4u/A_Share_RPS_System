from pathlib import Path

from run_daily_sop import _count_codes


def test_count_codes_uses_explicit_missing_value(tmp_path: Path):
    missing = tmp_path / "missing_codes.csv"

    assert _count_codes(missing) == -1
    assert _count_codes(missing, missing_value=0) == 0


def test_count_codes_counts_nonempty_lines(tmp_path: Path):
    codes = tmp_path / "codes.csv"
    codes.write_text("000001.SZ\n\n600000.SH\n", encoding="utf-8")

    assert _count_codes(codes, missing_value=0) == 2
