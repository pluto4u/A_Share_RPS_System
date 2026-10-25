from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

from pathlib import Path
import hashlib,json
r=PROJECT_ROOT
m=json.loads((r/'manifest.json').read_text(encoding='utf-8'))
bad=[x['path'] for x in m['files'] if not (r/x['path']).is_file() or hashlib.sha256((r/x['path']).read_bytes()).hexdigest()!=x['sha256']]
print('PASS' if not bad else 'FAIL', len(m['files']), 'files', bad)
raise SystemExit(bool(bad))
