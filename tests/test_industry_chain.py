from pathlib import Path

import pandas as pd

from industry_chain.database import IndustryChainDB, initialize_database
from industry_chain.rps_bridge import analyze_rps_candidates


def test_database_initializes_and_ranks_bottlenecks(tmp_path: Path):
    db_path = tmp_path / "industry_chain.db"
    initialize_database(db_path, reset=True)
    db = IndustryChainDB(db_path)

    themes = db.list_themes()
    assert {row["code"] for row in themes} == {
        "AI_OPTICAL",
        "AI_PACKAGING",
        "HUMANOID",
    }

    graph = db.theme_map("AI_OPTICAL")
    assert any(row["code"] == "INP_SUBSTRATE" for row in graph["nodes"])
    assert any(row["security_id"] == "300308.SZ" for row in graph["companies"])
    assert any(row["security_id"] == "601869.SH" for row in graph["companies"])

    ranked = db.ranked_exposures()
    assert ranked[0]["security_id"] == "NVDA.US"
    assert all(0 <= row["bottleneck_score"] <= 100 for row in ranked)
    assert all("disconfirming_conditions" in row for row in ranked)

    views = db.viewpoints_for("security", "601869.SH")
    assert any(row["source_category"] == "influencer_commentary" for row in views)
    assert all(row["adoption_status"] for row in views)


def test_rps_bridge_ranks_mapped_and_queues_unmapped(tmp_path: Path):
    db_path = tmp_path / "industry_chain.db"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    initialize_database(db_path, reset=True)
    db = IndustryChainDB(db_path)

    candidates = pd.DataFrame(
        [
            {
                "trade_date": "2026-07-10",
                "ts_code": "300308.SZ",
                "close": 100.0,
                "RPS_50": 96.0,
                "RPS_120": 98.0,
                "RPS_250": 99.0,
                "ret20": 0.20,
                "vs_ma60": 0.22,
                "IN_L2": True,
                "IN_HUOCHE": True,
                "IN_EARLY": False,
            },
            {
                "trade_date": "2026-07-10",
                "ts_code": "999999.SZ",
                "close": 20.0,
                "RPS_50": 95.0,
                "RPS_120": 97.0,
                "RPS_250": 98.0,
                "ret20": 0.45,
                "vs_ma60": 0.50,
                "IN_L2": True,
                "IN_HUOCHE": False,
                "IN_EARLY": False,
            },
        ]
    )
    candidates.to_csv(output_dir / "tdx_screener_20260710_super.csv", index=False)

    result, meta = analyze_rps_candidates(db, output_dir, pool="super")
    assert meta["date"] == "20260710"
    assert meta["candidate_count"] == 2
    assert meta["mapped_count"] == 1
    assert result.iloc[0]["ts_code"] == "300308.SZ"
    assert result.iloc[0]["combined_score"] > result.iloc[0]["bottleneck_score"]

    queue = db.research_queue()
    assert [row["security_code"] for row in queue] == ["999999.SZ"]

    analyze_rps_candidates(db, output_dir, pool="super")
    assert db.research_queue()[0]["occurrence_count"] == 1
