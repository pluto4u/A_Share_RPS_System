"""
火山引擎函数服务入口。

用法:
  - 定时触发器调用 handler(event, context)
  - event 可选字段:
      dry_run: bool
      full_mode: bool
      session_only: bool
      tos_sync: bool  (是否在执行前后同步 TOS 数据)
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

from cloud_runner import run_once, _tos_sync_before_pipeline, _tos_sync_after_pipeline


def handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    full_mode = bool(event.get("full_mode", False))
    session_only = bool(event.get("session_only", True))
    tos_sync = bool(event.get("tos_sync", False))

    if tos_sync and not dry_run:
        _tos_sync_before_pipeline()

    code = run_once(
        dry_run=dry_run,
        full_mode=full_mode,
        session_only=session_only,
    )

    if tos_sync and not dry_run and code == 0:
        _tos_sync_after_pipeline()

    ok = code == 0
    return {
        "statusCode": 200 if ok else 500,
        "body": {
            "ok": ok,
            "exit_code": code,
            "dry_run": dry_run,
            "full_mode": full_mode,
            "session_only": session_only,
            "tos_sync": tos_sync,
        },
    }
