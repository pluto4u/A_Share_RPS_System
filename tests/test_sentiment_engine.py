from sentiment_engine import final_position


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
