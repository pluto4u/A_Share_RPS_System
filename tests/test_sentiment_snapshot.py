import pandas as pd

from feishu_bot import build_sentiment_snapshot


def sample_report_data():
    return {
        "timestamp": "2026-02-14 15:00",
        "all_spot": pd.DataFrame({"涨跌幅": [1.0, -0.5, 2.3, -1.2]}),
        "zt_pools": {"zt_n": 12, "dt_n": 3, "zb_n": 2},
        "money_effect": 0.8,
        "etf_signal": {"ratio": 1.2},
        "trend_gate": 100,
        "breadth_cap": 80,
        "breadth": {"pct": 35.0, "above": 1400, "total": 4000},
    }


def test_snapshot_contains_required_fields():
    snapshot = build_sentiment_snapshot(sample_report_data())
    required = [
        "date",
        "score",
        "position_raw",
        "trend_gate",
        "breadth_cap",
        "position_final",
    ]
    assert all(k in snapshot for k in required)
