from sentiment_engine import final_position, volume_clearing_score


def test_final_position_respects_trend_gate():
    score_position = 60
    trend_gate = 0
    breadth_cap = 80
    assert final_position(score_position, trend_gate, breadth_cap) == 0


def test_position_mapping_and_breadth_cap():
    from sentiment_engine import score_to_position, apply_breadth_cap

    assert score_to_position(82) == 80
    assert score_to_position(61) == 60
    assert score_to_position(45) == 30
    assert score_to_position(22) == 10
    assert score_to_position(10) == 0
    assert apply_breadth_cap(60, breadth_pct=8.0, freeze_threshold=10.0) == 10


def test_volume_clearing_score():
    """测试中小盘ETF量能清空评分"""
    # 全面清空 → 100
    all_clearing = [
        {"status": "量能清空", "error": False, "vol_ratio": 0.3},
        {"status": "量能清空", "error": False, "vol_ratio": 0.4},
    ]
    score, desc = volume_clearing_score(all_clearing)
    assert score == 100
    assert "清空" in desc

    # 部分清空 → 80
    partial_clearing = [
        {"status": "量能清空", "error": False, "vol_ratio": 0.3},
        {"status": "正常", "error": False, "vol_ratio": 1.0},
    ]
    score, desc = volume_clearing_score(partial_clearing)
    assert score == 80

    # 全面缩量 → 65
    all_shrink = [
        {"status": "缩量", "error": False, "vol_ratio": 0.6},
        {"status": "缩量", "error": False, "vol_ratio": 0.65},
    ]
    score, desc = volume_clearing_score(all_shrink)
    assert score == 65

    # 全面放量 → 30
    all_surge = [
        {"status": "放量", "error": False, "vol_ratio": 2.0},
        {"status": "放量", "error": False, "vol_ratio": 2.5},
    ]
    score, desc = volume_clearing_score(all_surge)
    assert score == 30

    # 空数据 → 50
    score, desc = volume_clearing_score([])
    assert score == 50

    # 正常 → 50
    normal = [
        {"status": "正常", "error": False, "vol_ratio": 1.0},
        {"status": "正常", "error": False, "vol_ratio": 1.1},
    ]
    score, desc = volume_clearing_score(normal)
    assert score == 50
