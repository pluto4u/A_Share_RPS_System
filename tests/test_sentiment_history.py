"""sentiment_history 模块的单元测试"""
import os
import tempfile

from sentiment_history import save_snapshot, load_history, analyze_trend


def test_save_and_load():
    """保存后能正确加载"""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        save_snapshot("2026-02-10", "10:00", 65, 50, "盘中",
                      {"涨跌比": 80, "赚钱效应": 60}, path=path)
        save_snapshot("2026-02-11", "15:00", 70, 60, "盘后",
                      {"涨跌比": 85, "赚钱效应": 65}, path=path)

        hist = load_history(30, path=path)
        assert len(hist) == 2
        assert hist[0]["date"] == "2026-02-10"
        assert hist[1]["score"] == 70
    finally:
        os.unlink(path)


def test_daily_dedup():
    """同一天多条记录只保留最后一条"""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        save_snapshot("2026-02-10", "10:00", 50, 40, "盘中", path=path)
        save_snapshot("2026-02-10", "14:00", 60, 50, "盘中", path=path)
        save_snapshot("2026-02-10", "15:00", 65, 55, "盘后", path=path)

        hist = load_history(30, path=path)
        assert len(hist) == 1
        assert hist[0]["score"] == 65  # 最后一条
    finally:
        os.unlink(path)


def test_analyze_trend_streak():
    """连续上涨检测"""
    hist = [
        {"date": "2026-02-10", "score": 40, "position": 30},
        {"date": "2026-02-11", "score": 50, "position": 40},
        {"date": "2026-02-12", "score": 55, "position": 45},
        {"date": "2026-02-13", "score": 62, "position": 50},
    ]
    t = analyze_trend(history=hist)
    assert t["streak"] == 3  # 连涨3天 (11>10, 12>11, 13>12)
    assert "连涨" in t["streak_label"]
    assert t["prev_score"] == 55
    assert t["delta"] == 7
    assert t["ma3"] is not None


def test_analyze_trend_decline():
    """连续下降检测"""
    hist = [
        {"date": "2026-02-10", "score": 70, "position": 60},
        {"date": "2026-02-11", "score": 60, "position": 50},
        {"date": "2026-02-12", "score": 45, "position": 35},
    ]
    t = analyze_trend(history=hist)
    assert t["streak"] == -2  # 连跌2天
    assert "连跌" in t["streak_label"]


def test_analyze_trend_empty():
    """无历史数据"""
    t = analyze_trend(history=[])
    assert t["streak"] == 0
    assert "首次运行" in t["summary"]


def test_analyze_trend_single_day():
    """只有一天数据"""
    t = analyze_trend(history=[{"date": "2026-02-10", "score": 55, "position": 40}])
    assert t["streak"] == 0
    assert t["prev_score"] is None
