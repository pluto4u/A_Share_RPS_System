from manual_trigger_api import run_now


def test_run_now_endpoint_returns_snapshot():
    # 直接测试函数，避免 TestClient 环境问题
    from fastapi import BackgroundTasks
    
    resp = run_now(
        background_tasks=BackgroundTasks(),
        full=False,
        dry_run=True,
    )
    body = resp.body.decode()
    assert "score" in body
