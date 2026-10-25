from pathlib import Path

import project_paths


def test_source_data_and_outputs_are_separate():
    assert project_paths.DATA_ROOT != project_paths.OUTPUT_ROOT
    assert project_paths.MARKET_DATA_DIR.parent == project_paths.DATA_ROOT
    assert project_paths.REPORTS_DIR.parent == project_paths.OUTPUT_ROOT
    assert project_paths.EXPORTS_DIR.parent == project_paths.OUTPUT_ROOT
    assert project_paths.SIGNALS_DIR.parent == project_paths.OUTPUT_ROOT


def test_read_helper_prefers_current_then_legacy(tmp_path, monkeypatch):
    current = tmp_path / "data" / "market"
    legacy = tmp_path / "backtest" / "data"
    current.mkdir(parents=True)
    legacy.mkdir(parents=True)
    monkeypatch.setattr(project_paths, "MARKET_DATA_DIR", current)
    monkeypatch.setattr(project_paths, "LEGACY_DATA_DIR", legacy)

    legacy_file = legacy / "prices.csv"
    legacy_file.write_text("legacy", encoding="utf-8")
    assert project_paths.market_data_file("prices.csv") == legacy_file

    current_file = current / "prices.csv"
    current_file.write_text("current", encoding="utf-8")
    assert project_paths.market_data_file("prices.csv") == current_file


def test_output_directory_creation(tmp_path, monkeypatch):
    directories = [tmp_path / name for name in ("reports", "exports", "signals", "logs", "workspace")]
    for attr, path in zip(
        ("REPORTS_DIR", "EXPORTS_DIR", "SIGNALS_DIR", "LOGS_DIR", "WORKSPACE_OUTPUT_DIR"),
        directories,
        strict=True,
    ):
        monkeypatch.setattr(project_paths, attr, path)

    project_paths.ensure_output_dirs()
    assert all(path.is_dir() for path in directories)
