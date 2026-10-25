"""Offline checks of explicit research-window CLI boundaries."""

import subprocess
import sys
from pathlib import Path


def test_start_date_is_exposed() -> None:
    script = Path(__file__).resolve().parents[1] / "run_price_volume_launch.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "--start-date" in result.stdout
    assert "starts in cash" in result.stdout


def test_invalid_calendar_date_rejected_before_loading_data() -> None:
    script = Path(__file__).resolve().parents[1] / "run_price_volume_launch.py"
    result = subprocess.run(
        [sys.executable, str(script), "--start-date", "2024-09-31"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "2024-09-31" in result.stderr
    assert "[VERIFY]" not in result.stdout
