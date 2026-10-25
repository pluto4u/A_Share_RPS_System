"""Canonical project paths.

Source code and configuration stay in the repository. Mutable market data is
kept under ``data/`` and generated artifacts are kept under ``outputs/``.
Environment variables make both locations relocatable for servers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPT_CATEGORIES = (
    "daily",
    "screening",
    "research",
    "validation",
    "reporting",
    "integration",
)


def _configured_path(env_name: str, default: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser().resolve() if value else default


DATA_ROOT = _configured_path("A_SHARE_DATA_DIR", PROJECT_ROOT / "data")
MARKET_DATA_DIR = DATA_ROOT / "market"
TDX_ROOT = _configured_path("TDX_ROOT", Path("C:/new_tdx"))

OUTPUT_ROOT = _configured_path("A_SHARE_OUTPUT_DIR", PROJECT_ROOT / "outputs")
REPORTS_DIR = OUTPUT_ROOT / "reports"
EXPORTS_DIR = OUTPUT_ROOT / "exports"
SIGNALS_DIR = OUTPUT_ROOT / "signals"
LOGS_DIR = OUTPUT_ROOT / "logs"
WORKSPACE_OUTPUT_DIR = OUTPUT_ROOT / "workspace"

# Read-only compatibility locations. New writes must never target these paths.
LEGACY_DATA_DIR = PROJECT_ROOT / "backtest" / "data"
LEGACY_REPORTS_DIR = PROJECT_ROOT / "backtest" / "reports"
LEGACY_OUTPUT_DIR = PROJECT_ROOT / "output"


def _prefer_current(current: Path, legacy: Path) -> Path:
    """Use a current-layout file, falling back to an existing legacy file."""

    return current if current.exists() or not legacy.exists() else legacy


def market_data_file(*parts: str) -> Path:
    return _prefer_current(MARKET_DATA_DIR.joinpath(*parts), LEGACY_DATA_DIR.joinpath(*parts))


def report_file(*parts: str) -> Path:
    return _prefer_current(REPORTS_DIR.joinpath(*parts), LEGACY_REPORTS_DIR.joinpath(*parts))


def script_dirs() -> list[Path]:
    scripts = PROJECT_ROOT / "scripts"
    if not scripts.is_dir():
        return []
    return [
        scripts / name
        for name in SCRIPT_CATEGORIES
        if (scripts / name).is_dir()
    ]


def script_file(name: str) -> Path:
    """Resolve a root-era script name after the scripts/ classification move."""

    filename = name if name.endswith(".py") else f"{name}.py"
    at_root = PROJECT_ROOT / filename
    if at_root.exists():
        return at_root
    matches = [directory / filename for directory in script_dirs() if (directory / filename).exists()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous script {filename}: {matches}")
    raise FileNotFoundError(filename)


def ensure_script_imports() -> None:
    """Keep historical `from run_daily_rps import ...` working after the move."""

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    for directory in script_dirs():
        path = str(directory)
        if path not in sys.path:
            sys.path.append(path)


def ensure_output_dirs() -> None:
    for directory in (REPORTS_DIR, EXPORTS_DIR, SIGNALS_DIR, LOGS_DIR, WORKSPACE_OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


ensure_script_imports()

