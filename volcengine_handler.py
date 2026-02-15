"""
火山引擎函数服务入口。

用法:
  - 定时触发器调用 handler(event, context)
  - event 可选字段:
      dry_run: bool
      full_mode: bool
      session_only: bool
"""

from cloud_runner import run_once


def handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    full_mode = bool(event.get("full_mode", False))
    session_only = bool(event.get("session_only", True))

    code = run_once(
        dry_run=dry_run,
        full_mode=full_mode,
        session_only=session_only,
    )
    ok = code == 0
    return {
        "statusCode": 200 if ok else 500,
        "body": {
            "ok": ok,
            "exit_code": code,
            "dry_run": dry_run,
            "full_mode": full_mode,
            "session_only": session_only,
        },
    }
