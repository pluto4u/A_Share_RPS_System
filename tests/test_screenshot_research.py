from collections import Counter
import sqlite3

from research_pipeline.screenshot_research import (
    _classify_trend,
    load_research_inputs,
    materialize_batch,
    validate_classification,
)


def _trend_row(**overrides):
    row = {
        "data_status": "ok",
        "close": 100.0,
        "vs_ma20": 0.01,
        "vs_ma60": 0.10,
        "rps_50": 95.0,
        "rps_120": 96.0,
        "rps_250": 97.0,
        "ret20": 0.08,
        "in_l1": 0,
        "in_l2": 0,
        "in_early_l1": 0,
    }
    row.update(overrides)
    return row


def test_screenshot_pool_is_complete_and_classification_is_one_to_one():
    pool, classification = load_research_inputs()
    validate_classification(pool, classification)

    occurrences = [code for source in pool["sources"] for code in source["securities"]]
    assert len(occurrences) == 173
    assert len(set(occurrences)) == 141
    assert set(pool["normalization_notes"]) == {
        "688012.SH",
        "688082.SH",
        "688629.SH",
        "300861.SZ",
    }

    primary = [
        code
        for codes in classification["primary_groups"].values()
        for code in codes
    ]
    assert all(count == 1 for count in Counter(primary).values())


def test_screenshot_batch_materializes_without_overwriting_history(tmp_path):
    pool, classification = load_research_inputs()
    db_path = tmp_path / "industry_chain.db"

    members, placements = materialize_batch(
        pool,
        classification,
        db_path=db_path,
        hq_cache=tmp_path / "empty_hq_cache",
    )
    assert len(members) == 141
    assert sum(1 + len(item["secondary"]) for item in placements.values()) == 147

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_batches").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_batch_members").fetchone()[0] == 141
        assert conn.execute("SELECT COUNT(*) FROM research_batch_placements").fetchone()[0] == 147


def test_trend_classifier_requires_intact_ma60_before_buy_or_shortlist():
    assert _classify_trend(_trend_row(vs_ma60=-0.01))[1] == "NO_BUY_BROKEN_TREND"
    assert (
        _classify_trend(_trend_row(in_l1=1, in_l2=1, vs_ma60=-0.01))[1]
        == "NO_BUY_BROKEN_TREND"
    )
    assert _classify_trend(_trend_row(vs_ma20=-0.05))[1] == "WATCH_PULLBACK"
    assert _classify_trend(_trend_row(vs_ma20=-0.02))[1] == "WATCH_NEAR_TRIGGER"


def test_trend_classifier_separates_buyable_extended_and_stale_data():
    assert _classify_trend(_trend_row(in_l1=1, in_l2=1))[1] == "SYSTEM_BUYABLE"
    assert _classify_trend(_trend_row(ret20=0.31))[1] == "WATCH_EXTENDED"
    assert (
        _classify_trend(_trend_row(in_l1=1, in_l2=1, ret20=0.31))[1]
        == "WAIT_PULLBACK_EXTENDED"
    )
    assert _classify_trend(_trend_row(data_status="stale_20260708"))[1] == "DATA_STALE"
    assert (
        _classify_trend(_trend_row(data_status="missing_local_history_before_target"))[1]
        == "DATA_GAP"
    )
