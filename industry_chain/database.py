"""SQLite persistence and graph queries for the industry-chain database."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .scoring import rank_company_exposures


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEMA_PATH = PACKAGE_DIR / "schema.sql"
DEFAULT_SEED_PATH = PACKAGE_DIR / "seed_data.json"
DEFAULT_DB_PATH = PACKAGE_DIR.parent / "data" / "industry_chain.db"


TABLE_KEYS = {
    "themes": ["code"],
    "nodes": ["code"],
    "node_relations": ["upstream_code", "downstream_code", "relation_type"],
    "companies": ["security_id"],
    "company_exposures": ["security_id", "node_code"],
    "evidence": ["evidence_id"],
    "viewpoints": ["viewpoint_id"],
    "events": ["event_id"],
}


def _upsert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> None:
    keys = TABLE_KEYS[table]
    for row in rows:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        updates = [column for column in columns if column not in keys]
        conflict = ", ".join(keys)
        update_sql = ", ".join(f"{column}=excluded.{column}" for column in updates)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {update_sql}"
        )
        conn.execute(sql, [row[column] for column in columns])


def initialize_database(
    db_path: Path | str = DEFAULT_DB_PATH,
    seed_path: Path | str = DEFAULT_SEED_PATH,
    reset: bool = False,
) -> Path:
    """Create or update the database from the editable JSON seed."""
    db_path = Path(db_path)
    seed_path = Path(seed_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()

    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        for table in TABLE_KEYS:
            _upsert_rows(conn, table, seed.get(table, []))
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE signal_observations
            SET is_mapped = 1
            WHERE security_code IN (
                SELECT ts_code FROM companies WHERE ts_code IS NOT NULL
            )
            """
        )
        conn.execute(
            """
            UPDATE research_queue
            SET status = 'mapped', updated_at = ?
            WHERE security_code IN (
                SELECT ts_code FROM companies WHERE ts_code IS NOT NULL
            )
            """,
            (now,),
        )
        conn.commit()
    return db_path


class IndustryChainDB:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_themes(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT code, name, status, as_of_date, demand_driver FROM themes ORDER BY code"
            ).fetchall()
        return [dict(row) for row in rows]

    def theme_map(self, theme_code: str) -> dict[str, list[dict[str, Any]]]:
        with closing(self.connect()) as conn:
            nodes = conn.execute(
                """
                SELECT * FROM nodes WHERE theme_code = ?
                ORDER BY layer_order, code
                """,
                (theme_code,),
            ).fetchall()
            relations = conn.execute(
                """
                SELECT r.* FROM node_relations r
                JOIN nodes n ON n.code = r.upstream_code
                WHERE n.theme_code = ?
                ORDER BY r.upstream_code, r.downstream_code
                """,
                (theme_code,),
            ).fetchall()
            companies = conn.execute(
                """
                SELECT c.security_id, c.ts_code, c.ticker, c.name, c.market,
                       e.node_code, e.role, e.mapping_confidence
                FROM company_exposures e
                JOIN companies c ON c.security_id = e.security_id
                JOIN nodes n ON n.code = e.node_code
                WHERE n.theme_code = ?
                ORDER BY e.node_code, c.security_id
                """,
                (theme_code,),
            ).fetchall()
        return {
            "nodes": [dict(row) for row in nodes],
            "relations": [dict(row) for row in relations],
            "companies": [dict(row) for row in companies],
        }

    def ranked_exposures(
        self,
        theme_code: str | None = None,
        min_confidence: float = 0,
    ) -> list[dict[str, Any]]:
        where = "WHERE n.theme_code = ?" if theme_code else ""
        params = (theme_code,) if theme_code else ()
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT c.security_id, c.ts_code, c.ticker, c.name, c.market,
                       n.theme_code, n.code AS node_code, n.name AS node_name,
                       n.expansion_lead_months, n.certification_months,
                       n.supply_concentration, n.substitution_difficulty,
                       n.capacity_elasticity, n.demand_certainty,
                       n.pricing_power AS node_pricing_power,
                       e.role, e.exposure_purity, e.technical_barrier,
                       e.capacity_scarcity, e.pricing_power,
                       e.customer_stickiness, e.bottleneck_ownership,
                       e.mapping_confidence, e.evidence_quality,
                       e.thesis, e.disconfirming_conditions, e.as_of_date
                FROM company_exposures e
                JOIN companies c ON c.security_id = e.security_id
                JOIN nodes n ON n.code = e.node_code
                {where}
                """,
                params,
            ).fetchall()
        normalized = []
        for row in rows:
            item = dict(row)
            item["pricing_power"] = item.pop("pricing_power")
            normalized.append(item)
        return [
            row
            for row in rank_company_exposures(normalized)
            if row["research_confidence"] >= min_confidence
        ]

    def evidence_for(self, entity_type: str, entity_code: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence
                WHERE entity_type = ? AND entity_code = ?
                ORDER BY as_of_date DESC, evidence_id
                """,
                (entity_type, entity_code),
            ).fetchall()
        return [dict(row) for row in rows]

    def viewpoints_for(self, entity_type: str, entity_code: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM viewpoints
                WHERE entity_type = ? AND entity_code = ?
                ORDER BY as_of_date DESC, viewpoint_id
                """,
                (entity_type, entity_code),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_signal_observations(
        self,
        rows: Iterable[dict[str, Any]],
        trade_date: str,
        source_pool: str,
        mapped_codes: set[str],
    ) -> None:
        """Persist a daily candidate snapshot and refresh the unmapped research queue."""
        now = datetime.now().isoformat(timespec="seconds")

        def value(row: dict[str, Any], key: str) -> Any:
            item = row.get(key)
            if item is None:
                return None
            try:
                if item != item:
                    return None
            except TypeError:
                pass
            return item

        with closing(self.connect()) as conn:
            observed_codes = []
            for row in rows:
                code = str(row["ts_code"])
                observed_codes.append(code)
                conn.execute(
                    """
                    INSERT INTO signal_observations (
                        trade_date, security_code, source_pool, close,
                        rps_50, rps_120, rps_250, ret20, vs_ma60,
                        is_mapped, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (trade_date, security_code, source_pool) DO UPDATE SET
                        close=excluded.close,
                        rps_50=excluded.rps_50,
                        rps_120=excluded.rps_120,
                        rps_250=excluded.rps_250,
                        ret20=excluded.ret20,
                        vs_ma60=excluded.vs_ma60,
                        is_mapped=excluded.is_mapped,
                        imported_at=excluded.imported_at
                    """,
                    (
                        trade_date,
                        code,
                        source_pool,
                        value(row, "close"),
                        value(row, "RPS_50"),
                        value(row, "RPS_120"),
                        value(row, "RPS_250"),
                        value(row, "ret20"),
                        value(row, "vs_ma60"),
                        int(code in mapped_codes),
                        now,
                    ),
                )

            for code in observed_codes:
                if code in mapped_codes:
                    conn.execute(
                        "UPDATE research_queue SET status='mapped', updated_at=? WHERE security_code=?",
                        (now, code),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO research_queue (
                        security_code, first_seen_date, last_seen_date,
                        occurrence_count, latest_pool, latest_rps_score,
                        status, updated_at
                    ) VALUES (?, ?, ?, 1, ?, NULL, 'pending_mapping', ?)
                    ON CONFLICT (security_code) DO UPDATE SET
                        first_seen_date = MIN(research_queue.first_seen_date, excluded.first_seen_date),
                        last_seen_date = MAX(research_queue.last_seen_date, excluded.last_seen_date),
                        latest_pool = excluded.latest_pool,
                        status = CASE WHEN research_queue.status='mapped'
                                      THEN 'mapped' ELSE 'pending_mapping' END,
                        updated_at = excluded.updated_at
                    """,
                    (code, trade_date, trade_date, source_pool, now),
                )
                conn.execute(
                    """
                    UPDATE research_queue
                    SET occurrence_count = (
                            SELECT COUNT(*) FROM signal_observations
                            WHERE security_code = ? AND is_mapped = 0
                        ),
                        latest_rps_score = (
                            SELECT COALESCE(rps_120, rps_250, rps_50)
                            FROM signal_observations
                            WHERE security_code = ?
                            ORDER BY trade_date DESC, source_pool DESC LIMIT 1
                        )
                    WHERE security_code = ?
                    """,
                    (code, code, code),
                )
            conn.commit()

    def research_queue(self, status: str = "pending_mapping") -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_queue
                WHERE status = ?
                ORDER BY occurrence_count DESC, last_seen_date DESC, latest_rps_score DESC
                """,
                (status,),
            ).fetchall()
        return [dict(row) for row in rows]
