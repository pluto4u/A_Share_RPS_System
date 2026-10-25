from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import logging
import sys
from emotion_validation.etf_rotation.etf_data import ETFDataManager

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

print("Starting ETF update...")
manager = ETFDataManager()
manager.fetch_all(start_date="20240101")
print("Done!")
