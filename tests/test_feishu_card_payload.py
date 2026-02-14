import pandas as pd

from feishu_bot import build_market_card


def sample_report_data():
    return {
        "timestamp": "2026-02-14 15:00",
        "status": "盘后",
        "all_spot": pd.DataFrame({"涨跌幅": [1.0, -0.5, 2.3, -1.2, 0.5]}),
        "zt_pools": {
            "zt_n": 20,
            "dt_n": 5,
            "zb_n": 3,
            "zt_df": pd.DataFrame(),
        },
        "money_effect": 1.5,
        "etf_signal": {"vol_today": 100000, "vol_avg5": 80000, "ratio": 1.25},
        "idx_data": [
            {
                "name": "沪深300",
                "close": 4000.0,
                "change_pct": 0.5,
                "ma20_status": "站上",
                "vol_ratio": 1.1,
                "error": False,
            }
        ],
        "trend_gate": 80,
        "breadth_cap": 100,
    }


def test_market_card_includes_score_and_final_position():
    card = build_market_card(sample_report_data())
    card_text = str(card)
    assert "综合评分" in card_text
    assert "建议仓位" in card_text
    assert "最终仓位" in card_text
