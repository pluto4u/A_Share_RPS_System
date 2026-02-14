"""
端到端集成测试：验证整个情绪监控流水线
"""


def run_pipeline_e2e(dry_run=True):
    """
    端到端测试入口：模拟完整流水线执行。
    
    Returns
    -------
    dict
        包含 snapshot_ok, card_ok 等验证结果
    """
    if dry_run:
        # 模拟快照生成
        from feishu_bot import build_sentiment_snapshot
        import pandas as pd
        
        mock_data = {
            "timestamp": "2026-02-14 15:00",
            "status": "盘后",
            "all_spot": pd.DataFrame({"涨跌幅": [1.0, -0.5, 2.3]}),
            "zt_pools": {"zt_n": 10, "dt_n": 2, "zb_n": 1, "zt_df": pd.DataFrame()},
            "money_effect": 0.8,
            "etf_signal": {"ratio": 1.1},
            "idx_data": [],
            "trend_gate": 100,
            "breadth_cap": 80,
        }
        
        snapshot = build_sentiment_snapshot(mock_data)
        snapshot_ok = all(
            k in snapshot
            for k in ["date", "score", "position_final", "trend_gate", "breadth_cap"]
        )
        
        # 模拟卡片构建
        from feishu_bot import build_market_card
        card = build_market_card(mock_data)
        card_ok = "card" in card and "elements" in card["card"]
        
        return {"snapshot_ok": snapshot_ok, "card_ok": card_ok}
    
    # 实际流水线（需真实数据）
    from cloud_runner import run_once
    exit_code = run_once(dry_run=False, full_mode=False)
    return {"snapshot_ok": exit_code == 0, "card_ok": exit_code == 0}


def test_e2e_pipeline_generates_snapshot_and_card():
    result = run_pipeline_e2e(dry_run=True)
    assert result["snapshot_ok"] is True
    assert result["card_ok"] is True
