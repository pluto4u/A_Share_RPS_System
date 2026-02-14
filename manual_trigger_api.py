"""
================================================================================
手动触发 API  manual_trigger_api.py
================================================================================
提供 HTTP 接口供手动触发情绪监控流水线（不依赖定时任务）。

启动:
    uvicorn manual_trigger_api:app --host 0.0.0.0 --port 8000

接口:
    POST /run-now         立即执行一次完整监控
    GET  /health          健康检查
================================================================================
"""

from datetime import datetime

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse

app = FastAPI(title="A股情绪监控 - 手动触发API")


@app.get("/health")
def health_check():
    """健康检查接口"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/run-now")
def run_now(background_tasks: BackgroundTasks, full: bool = False, dry_run: bool = False):
    """
    立即执行一次情绪监控流水线。

    Parameters
    ----------
    full : bool
        是否启用完整模式 (含广度 + RPS)
    dry_run : bool
        是否试运行 (不推送飞书)

    Returns
    -------
    JSON
        包含 score, position_final 等快照字段
    """
    if dry_run:
        # 试运行返回模拟快照
        return JSONResponse({
            "status": "dry_run",
            "score": 65,
            "position_raw": 60,
            "trend_gate": 100,
            "breadth_cap": 100,
            "position_final": 60,
            "timestamp": datetime.now().isoformat(),
        })

    # 后台异步执行，避免阻塞响应
    def _run_pipeline():
        from cloud_runner import run_once
        run_once(dry_run=False, full_mode=full)

    background_tasks.add_task(_run_pipeline)

    return JSONResponse({
        "status": "triggered",
        "message": "情绪监控流水线已触发，将在后台执行",
        "full_mode": full,
        "timestamp": datetime.now().isoformat(),
        "score": 0,  # 占位，实际值由流水线计算并推送飞书
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
