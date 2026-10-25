"""Turn screenshot stock pools into an auditable industry-chain research batch."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from industry_chain.database import DEFAULT_DB_PATH, initialize_database
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, normalize_tdx_dir, read_day_file, load_tdx_market


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POOL_PATH = ROOT / "research_inputs" / "20260715_screenshot_pool.json"
DEFAULT_CLASSIFICATION_PATH = ROOT / "research_inputs" / "chain_classification_20260715.json"
DEFAULT_HQ_CACHE = Path(r"C:\new_tdx\T0002\hq_cache")
DEFAULT_OUTPUT_DIR = ROOT / "output" / "research_batches"

EXISTING_NODE_METADATA = {
    "OPTICAL_TRANSCEIVER": {
        "code": "OPTICAL_TRANSCEIVER",
        "theme_code": "AI_OPTICAL",
        "name": "高速光模块 / 收发器",
        "pricing_power": 62,
    },
    "DATA_CENTER_FIBER": {
        "code": "DATA_CENTER_FIBER",
        "theme_code": "AI_OPTICAL",
        "name": "数据中心新型光纤光缆",
        "pricing_power": 78,
    },
    "ADVANCED_PACKAGING_OSAT": {
        "code": "ADVANCED_PACKAGING_OSAT",
        "theme_code": "AI_PACKAGING",
        "name": "先进封装组装与测试",
        "pricing_power": 66,
    },
    "CPO_PLATFORM": {
        "code": "CPO_PLATFORM",
        "theme_code": "AI_OPTICAL",
        "name": "CPO / 光电共封装平台",
        "pricing_power": 75,
    },
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any) -> str:
    number = _finite(value)
    return "-" if number is None else f"{number * 100:.1f}%"


def _num(value: Any, digits: int = 1) -> str:
    number = _finite(value)
    return "-" if number is None else f"{number:.{digits}f}"


def load_tdx_name_master(hq_cache: Path = DEFAULT_HQ_CACHE) -> dict[str, str]:
    """Read the current TDX security master without a network dependency."""
    result: dict[str, str] = {}
    for market, filename in (("SH", "shs.tnf"), ("SZ", "szs.tnf"), ("BJ", "bjs.tnf")):
        path = hq_cache / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        if len(raw) < 50 or (len(raw) - 50) % 360:
            continue
        for offset in range(50, len(raw), 360):
            record = raw[offset : offset + 360]
            code = record[:6].decode("ascii", errors="ignore")
            name = record[31:63].split(b"\0", 1)[0].decode("gbk", errors="ignore").strip()
            if code.isdigit() and name:
                result[f"{code}.{market}"] = name
    return result


def load_research_inputs(
    pool_path: Path = DEFAULT_POOL_PATH,
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pool = json.loads(Path(pool_path).read_text(encoding="utf-8"))
    classification = json.loads(Path(classification_path).read_text(encoding="utf-8"))
    return pool, classification


def validate_classification(pool: dict[str, Any], classification: dict[str, Any]) -> None:
    universe = {code for source in pool["sources"] for code in source["securities"]}
    assigned = [
        code
        for codes in classification["primary_groups"].values()
        for code in codes
    ]
    counts = Counter(assigned)
    duplicates = sorted(code for code, count in counts.items() if count > 1)
    classified = set(assigned) | set(classification.get("etfs", []))
    missing = sorted(universe - classified)
    extra = sorted(classified - universe)
    if duplicates or missing or extra:
        raise ValueError(
            f"分类覆盖错误: duplicates={duplicates}, missing={missing}, extra={extra}"
        )


def _build_member_rows(
    pool: dict[str, Any],
    classification: dict[str, Any],
    name_master: dict[str, str],
) -> list[dict[str, Any]]:
    validate_classification(pool, classification)
    occurrences: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source in pool["sources"]:
        for security_id in source["securities"]:
            occurrences[security_id].append(
                {"source_id": source["source_id"], "image": source["image"]}
            )

    primary_node = {
        security_id: node_code
        for node_code, codes in classification["primary_groups"].items()
        for security_id in codes
    }
    node_map = {**EXISTING_NODE_METADATA, **{row["code"]: row for row in classification["node_definitions"]}}
    theme_map = {row["code"]: row for row in classification["theme_definitions"]}
    theme_map.update(
        {
            "AI_OPTICAL": {"name": "AI数据中心光互连"},
            "AI_PACKAGING": {"name": "AI先进封装与封测"},
        }
    )
    overrides = pool.get("name_overrides", {})
    notes = pool.get("normalization_notes", {})
    etfs = set(classification.get("etfs", []))

    confidence_by_theme = {
        "AI_OPTICAL": 82,
        "AI_PACKAGING": 82,
        "AI_PCB": 80,
        "SEMICONDUCTOR": 78,
        "ADVANCED_MANUFACTURING": 65,
    }
    rows = []
    for security_id in sorted(occurrences):
        market = security_id.rsplit(".", 1)[-1]
        asset_type = "etf" if security_id in etfs else "stock"
        node_code = primary_node.get(security_id)
        node = node_map.get(node_code, {})
        theme_code = node.get("theme_code")
        theme = theme_map.get(theme_code, {})
        name = overrides.get(security_id) or name_master.get(security_id) or security_id
        refs = occurrences[security_id]
        rows.append(
            {
                "security_id": security_id,
                "name": name,
                "market": market,
                "asset_type": asset_type,
                "source_count": len(refs),
                "source_ids": ",".join(item["source_id"] for item in refs),
                "source_refs": ",".join(item["image"] for item in refs),
                "primary_theme_code": theme_code,
                "primary_node_code": node_code,
                "industry_name": (
                    "ETF参考" if asset_type == "etf" else f"{theme.get('name', '')} / {node.get('name', '')}"
                ),
                "mapping_confidence": 50 if asset_type == "etf" else confidence_by_theme.get(theme_code, 60),
                "classification_notes": notes.get(security_id, ""),
            }
        )
    return rows


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], keys: list[str]) -> None:
    columns = list(row)
    updates = [column for column in columns if column not in keys]
    placeholders = ", ".join("?" for _ in columns)
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in updates)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {update_sql}"
    )
    conn.execute(sql, [row[column] for column in columns])


def materialize_batch(
    pool: dict[str, Any],
    classification: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    hq_cache: Path = DEFAULT_HQ_CACHE,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Store source membership, classifications and chain placements in SQLite."""
    initialize_database(db_path)
    name_master = load_tdx_name_master(hq_cache)
    members = _build_member_rows(pool, classification, name_master)
    node_map = {**EXISTING_NODE_METADATA, **{row["code"]: row for row in classification["node_definitions"]}}
    theme_map = {row["code"]: row for row in classification["theme_definitions"]}
    secondary = classification.get("secondary_placements", {})
    now = datetime.now().isoformat(timespec="seconds")
    batch_id = pool["batch_id"]

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for theme in classification["theme_definitions"]:
            _upsert(conn, "themes", theme, ["code"])

        for node in classification["node_definitions"]:
            full_node = {
                "code": node["code"],
                "theme_code": node["theme_code"],
                "parent_code": node.get("parent_code"),
                "name": node["name"],
                "layer_order": node.get("layer_order", 50),
                "node_type": node.get("node_type", "component"),
                "description": node.get("description", node["name"]),
                "bottleneck_type": node.get("bottleneck_type", "qualification"),
                "expansion_lead_months": node.get("expansion_lead_months", 12),
                "certification_months": node.get("certification_months", 9),
                "supply_concentration": node.get("supply_concentration", 60),
                "substitution_difficulty": node.get("substitution_difficulty", 65),
                "capacity_elasticity": node.get("capacity_elasticity", 48),
                "demand_certainty": node.get("demand_certainty", 65),
                "pricing_power": node.get("pricing_power", 60),
                "as_of_date": pool["source_date"],
            }
            _upsert(conn, "nodes", full_node, ["code"])

        demand_node_by_theme = {
            "AI_PCB": "AI_SERVER_PCB_DEMAND",
            "SEMICONDUCTOR": "AI_CHIP_DEMAND",
            "AI_OPTICAL": "AI_CLUSTER_DEMAND",
            "AI_PACKAGING": "AI_CHIP_PACKAGE_DEMAND",
            "ADVANCED_MANUFACTURING": "ADV_MANUFACTURING_DEMAND",
        }
        all_nodes = {
            row[0]
            for row in conn.execute("SELECT code FROM nodes").fetchall()
        }
        for node in classification["node_definitions"]:
            demand_node = demand_node_by_theme.get(node["theme_code"])
            if not demand_node or node["code"] == demand_node or demand_node not in all_nodes:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO node_relations (
                    upstream_code, downstream_code, relation_type, criticality,
                    substitution_difficulty, lead_time_months, notes
                ) VALUES (?, ?, 'serves', 70, 60, 12, ?)
                """,
                (node["code"], demand_node, f"{node['name']}服务于{demand_node}"),
            )

        _upsert(
            conn,
            "research_batches",
            {
                "batch_id": batch_id,
                "title": pool["title"],
                "source_date": pool["source_date"],
                "analysis_date": datetime.now().date().isoformat(),
                "price_date": None,
                "status": "classified",
                "source_count": len(pool["sources"]),
                "member_count": len(members),
                "notes": pool.get("notes", ""),
                "updated_at": now,
            },
            ["batch_id"],
        )

        placements_by_security: dict[str, dict[str, Any]] = {}
        for member in members:
            _upsert(
                conn,
                "research_batch_members",
                {"batch_id": batch_id, **member},
                ["batch_id", "security_id"],
            )
            if member["asset_type"] == "etf":
                continue

            security_id = member["security_id"]
            ticker, market = security_id.split(".")
            country = "HK" if market == "HK" else "CN"
            conn.execute(
                """
                INSERT INTO companies (
                    security_id, ts_code, ticker, name, market, country,
                    market_cap_bucket, description, as_of_date
                ) VALUES (?, ?, ?, ?, ?, ?, 'unknown', ?, ?)
                ON CONFLICT (security_id) DO UPDATE SET
                    ts_code=excluded.ts_code,
                    ticker=excluded.ticker,
                    name=excluded.name,
                    market=excluded.market,
                    country=excluded.country,
                    as_of_date=excluded.as_of_date
                """,
                (
                    security_id,
                    security_id,
                    ticker,
                    member["name"],
                    market,
                    country,
                    member["industry_name"],
                    pool["source_date"],
                ),
            )

            placement_nodes = [(member["primary_node_code"], "primary", member["mapping_confidence"])]
            placement_nodes.extend((code, "secondary", 58) for code in secondary.get(security_id, []))
            for node_code, placement_type, confidence in placement_nodes:
                if not node_code:
                    continue
                _upsert(
                    conn,
                    "research_batch_placements",
                    {
                        "batch_id": batch_id,
                        "security_id": security_id,
                        "node_code": node_code,
                        "placement_type": placement_type,
                        "mapping_confidence": confidence,
                        "notes": member["classification_notes"],
                    },
                    ["batch_id", "security_id", "node_code"],
                )
                node = node_map.get(node_code)
                if node is None:
                    node_row = conn.execute(
                        "SELECT code, name, pricing_power FROM nodes WHERE code=?", (node_code,)
                    ).fetchone()
                    node = {
                        "code": node_code,
                        "name": node_row[1] if node_row else node_code,
                        "pricing_power": node_row[2] if node_row else 55,
                    }
                conn.execute(
                    """
                    INSERT OR IGNORE INTO company_exposures (
                        security_id, node_code, role, exposure_purity, technical_barrier,
                        capacity_scarcity, pricing_power, customer_stickiness,
                        bottleneck_ownership, mapping_confidence, evidence_quality,
                        thesis, disconfirming_conditions, as_of_date
                    ) VALUES (?, ?, ?, 62, 64, 55, ?, 64, 58, ?, 45, ?, ?, ?)
                    """,
                    (
                        security_id,
                        node_code,
                        "primary_supplier" if placement_type == "primary" else "secondary_exposure",
                        node.get("pricing_power", 55),
                        confidence,
                        f"截图池映射至{node.get('name', node_code)}；需用财报、客户与订单继续验证。",
                        "缺少收入占比、客户认证或量产交付证据；产业景气转弱。",
                        pool["source_date"],
                    ),
                )
            placements_by_security[security_id] = {
                "primary": member["primary_node_code"],
                "secondary": secondary.get(security_id, []),
            }
        conn.commit()
    return members, placements_by_security


def _load_pool_memberships(output_dir: Path, date: str) -> dict[str, set[str]]:
    suffixes = {
        "l1": "",
        "l2": "_L2",
        "huoche": "_huoche",
        "early": "_early",
        "early_l1": "_early_L1",
        "super": "_super",
    }
    result: dict[str, set[str]] = {}
    for label, suffix in suffixes.items():
        path = output_dir / f"tdx_screener_{date}{suffix}.csv"
        if not path.exists():
            result[label] = set()
            continue
        frame = pd.read_csv(path)
        result[label] = set(frame.get("ts_code", pd.Series(dtype=str)).astype(str))
    return result


def _add_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["ts_code", "trade_date"]).copy()
    grouped_close = frame.groupby("ts_code")["close"]
    for period in (5, 20, 60, 120):
        frame[f"ret{period}"] = grouped_close.pct_change(period, fill_method=None)
    for period in (20, 60, 120, 250):
        frame[f"ma{period}"] = grouped_close.transform(
            lambda values, p=period: values.rolling(p, min_periods=p).mean()
        )
    frame["high250"] = grouped_close.transform(
        lambda values: values.rolling(250, min_periods=20).max()
    )
    frame["high20"] = frame.groupby("ts_code")["high"].transform(
        lambda values: values.rolling(20, min_periods=5).max()
    )
    frame["amount20_avg"] = frame.groupby("ts_code")["amount"].transform(
        lambda values: values.rolling(20, min_periods=5).mean()
    )
    return frame


def _single_security_metrics(frame: pd.DataFrame, security_id: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    local = frame.copy()
    local["ts_code"] = security_id
    return _add_metrics(local)


def _classify_trend(row: dict[str, Any]) -> tuple[str, str, float | None, str]:
    status = row["data_status"]
    if status.startswith("unavailable") or status.startswith("missing_"):
        return "DATA_GAP", "DATA_GAP", None, "缺少截至目标日的可靠日线数据。"
    if status.startswith("stale_"):
        return "DATA_STALE", "DATA_STALE", None, "最新可用价格早于批次交易日，不参与当日买入排序。"

    close = _finite(row.get("close"))
    vs20 = _finite(row.get("vs_ma20"))
    vs60 = _finite(row.get("vs_ma60"))
    rps50 = _finite(row.get("rps_50"))
    rps120 = _finite(row.get("rps_120"))
    rps250 = _finite(row.get("rps_250"))
    ret20 = _finite(row.get("ret20"))
    if close is None:
        return "DATA_GAP", "DATA_GAP", None, "没有有效收盘价。"

    if vs20 is not None and vs60 is not None and vs20 > 0 and vs60 > 0:
        trend_state = "STRONG_UP" if (rps120 or 0) >= 90 else "UP"
    elif vs60 is not None and vs60 > 0:
        trend_state = "PULLBACK_ABOVE_MA60"
    elif vs60 is not None:
        trend_state = "WEAK_BELOW_MA60"
    else:
        trend_state = "NEW_OR_PARTIAL_DATA"

    values = [(rps50, 0.30), (rps120, 0.35), (rps250, 0.25)]
    weight = sum(item_weight for value, item_weight in values if value is not None)
    score = (
        sum(value * item_weight for value, item_weight in values if value is not None) / weight
        if weight
        else None
    )
    if score is not None:
        score += 8 * row["in_l1"] + 5 * row["in_l2"] + 3 * row["in_early_l1"]
        if (vs60 or 0) > 0.30 or (ret20 or 0) > 0.35:
            score -= 12
        score = round(max(0, min(100, score)), 2)

    if row["in_l1"]:
        if vs60 is None or vs60 <= 0:
            return trend_state, "NO_BUY_BROKEN_TREND", score, "虽在L1历史池中，但当前已跌破MA60，不满足执行条件。"
        extended = (vs60 is not None and vs60 > 0.25) or (ret20 is not None and ret20 > 0.30)
        near_ma = vs20 is not None and -0.03 <= vs20 <= 0.10
        if extended:
            return trend_state, "WAIT_PULLBACK_EXTENDED", score, "已入L1但短期涨幅或相对MA60乖离过大，等待回踩。"
        if row["in_l2"] and near_ma:
            return trend_state, "SYSTEM_BUYABLE", score, "L1与L2共振，且价格未明显偏离MA20。"
        return trend_state, "SYSTEM_WATCH", score, "已入L1，但买点位置或更严格信号尚未同时满足。"

    if rps120 is None or rps250 is None:
        return trend_state, "NO_FULL_RPS", score, "上市时间、市场覆盖或数据长度不足，不能形成完整RPS判断。"
    if vs60 is not None and vs60 <= 0:
        return trend_state, "NO_BUY_BROKEN_TREND", score, "价格位于MA60下方，RPS属于滞后强度，暂不进入买入观察。"
    if rps120 >= 90 and rps250 >= 90:
        extended = (vs60 is not None and vs60 > 0.25) or (ret20 is not None and ret20 > 0.30)
        if extended:
            return trend_state, "WATCH_EXTENDED", score, "中长期RPS双强，但短期波动或MA60乖离仍大，等待均线靠拢。"
        if vs20 is not None and -0.03 <= vs20 <= 0.10 and vs60 is not None and vs60 > 0:
            return trend_state, "WATCH_NEAR_TRIGGER", score, "中长期RPS双强且靠近MA20，但尚未通过完整L1条件。"
        if vs20 is not None and vs20 < -0.03:
            return trend_state, "WATCH_PULLBACK", score, "中长期RPS双强且仍在MA60上方，但回踩幅度较深，等待企稳。"
        return trend_state, "WATCH_STRONG", score, "RPS双强，但位置或形态尚未形成系统买点。"
    if rps120 >= 80 and (vs60 or -1) > 0:
        return trend_state, "WATCH", score, "中期相对强度尚可并位于MA60上方。"
    return trend_state, "NO_BUY", score, "未达到当前系统的强度与趋势共振条件。"


def compute_trend_snapshots(
    members: list[dict[str, Any]],
    target_date: str,
    tdx_dir: str = DEFAULT_TDX_DIR,
    output_dir: Path = ROOT / "output",
) -> tuple[list[dict[str, Any]], str]:
    """Compute A-share RPS cross-section and trend metrics for every batch member."""
    target_ts = pd.Timestamp(target_date)
    market = load_tdx_market(tdx_dir, markets=["sh", "sz"], min_days=20, verbose=True)
    market = market[
        (market["trade_date"] <= target_ts)
        & (market["trade_date"] >= target_ts - pd.Timedelta(days=550))
    ].copy()
    market = calculate_rps(market, periods=[50, 120, 250], verbose=True)
    market = _add_metrics(market)
    latest_market_date = market["trade_date"].max()
    effective_ts = min(target_ts, latest_market_date)
    effective_date = effective_ts.strftime("%Y%m%d")
    memberships = _load_pool_memberships(Path(output_dir), effective_date)
    tdx_root = Path(normalize_tdx_dir(tdx_dir))
    now = datetime.now().isoformat(timespec="seconds")

    snapshots = []
    for member in members:
        security_id = member["security_id"]
        code, market_code = security_id.split(".")
        asset_type = member["asset_type"]
        row: dict[str, Any] | None = None
        data_status = "ok"

        if market_code in {"SH", "SZ"} and asset_type == "stock":
            subset = market[(market["ts_code"] == security_id) & (market["trade_date"] <= effective_ts)]
            if not subset.empty:
                latest = subset.iloc[-1]
                row = latest.to_dict()
                if latest["trade_date"] < effective_ts:
                    data_status = f"stale_{latest['trade_date'].strftime('%Y%m%d')}"
            else:
                data_status = "missing_local_history_before_target"
        elif market_code in {"SH", "SZ", "BJ"}:
            market_folder = market_code.lower()
            path = tdx_root / market_folder / "lday" / f"{market_folder}{code}.day"
            local = read_day_file(str(path)) if path.exists() else None
            if local is not None:
                local = local[local["trade_date"] <= effective_ts]
                metrics = _single_security_metrics(local, security_id)
                if not metrics.empty:
                    row = metrics.iloc[-1].to_dict()
                    if row["trade_date"] < effective_ts:
                        data_status = f"stale_{row['trade_date'].strftime('%Y%m%d')}_trend_only"
                    else:
                        data_status = "trend_only_no_cross_sectional_rps"
            if row is None:
                data_status = "missing_local_file"
        elif market_code == "HK":
            data_status = "unavailable_hk_daily_stale_local_source"

        base = {
            "batch_id": "",
            "trade_date": effective_ts.date().isoformat(),
            "price_trade_date": None,
            "security_id": security_id,
            "data_status": data_status,
            "close": None,
            "ret5": None,
            "ret20": None,
            "ret60": None,
            "ret120": None,
            "rps_50": None,
            "rps_120": None,
            "rps_250": None,
            "vs_ma20": None,
            "vs_ma60": None,
            "vs_ma120": None,
            "high_250_ratio": None,
            "drawdown20": None,
            "amount20_avg": None,
            "in_l1": int(security_id in memberships["l1"]),
            "in_l2": int(security_id in memberships["l2"]),
            "in_huoche": int(security_id in memberships["huoche"]),
            "in_early": int(security_id in memberships["early"]),
            "in_early_l1": int(security_id in memberships["early_l1"]),
            "in_super": int(security_id in memberships["super"]),
            "trend_state": "unknown",
            "entry_state": "data_gap",
            "trend_score": None,
            "decision_reason": "",
            "computed_at": now,
        }
        if row is not None:
            price_date = row.get("trade_date")
            base.update(
                {
                    "price_trade_date": price_date.date().isoformat() if hasattr(price_date, "date") else str(price_date),
                    "close": _finite(row.get("close")),
                    "ret5": _finite(row.get("ret5")),
                    "ret20": _finite(row.get("ret20")),
                    "ret60": _finite(row.get("ret60")),
                    "ret120": _finite(row.get("ret120")),
                    "rps_50": _finite(row.get("RPS_50")),
                    "rps_120": _finite(row.get("RPS_120")),
                    "rps_250": _finite(row.get("RPS_250")),
                    "vs_ma20": _finite(row.get("close") / row.get("ma20") - 1) if _finite(row.get("ma20")) else None,
                    "vs_ma60": _finite(row.get("close") / row.get("ma60") - 1) if _finite(row.get("ma60")) else None,
                    "vs_ma120": _finite(row.get("close") / row.get("ma120") - 1) if _finite(row.get("ma120")) else None,
                    "high_250_ratio": _finite(row.get("close") / row.get("high250")) if _finite(row.get("high250")) else None,
                    "drawdown20": _finite(row.get("close") / row.get("high20") - 1) if _finite(row.get("high20")) else None,
                    "amount20_avg": _finite(row.get("amount20_avg")),
                }
            )
        trend_state, entry_state, score, reason = _classify_trend(base)
        base.update(
            {
                "trend_state": trend_state,
                "entry_state": entry_state,
                "trend_score": score,
                "decision_reason": reason,
            }
        )
        snapshots.append(base)
    return snapshots, effective_ts.date().isoformat()


def store_trend_snapshots(
    pool: dict[str, Any],
    snapshots: list[dict[str, Any]],
    price_date: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    batch_id = pool["batch_id"]
    with closing(sqlite3.connect(db_path)) as conn:
        for snapshot in snapshots:
            snapshot = {**snapshot, "batch_id": batch_id}
            _upsert(
                conn,
                "trend_snapshots",
                snapshot,
                ["batch_id", "trade_date", "security_id"],
            )
        conn.execute(
            """
            UPDATE research_batches
            SET price_date=?, status='analyzed', updated_at=?
            WHERE batch_id=?
            """,
            (price_date, datetime.now().isoformat(timespec="seconds"), batch_id),
        )
        conn.commit()


def _snapshot_frame(
    members: list[dict[str, Any]], snapshots: list[dict[str, Any]]
) -> pd.DataFrame:
    member_frame = pd.DataFrame(members)
    snapshot_frame = pd.DataFrame(snapshots).drop(columns=["batch_id"], errors="ignore")
    frame = member_frame.merge(snapshot_frame, on="security_id", how="left")
    return frame.sort_values(
        ["entry_state", "trend_score", "source_count"],
        ascending=[True, False, False],
        na_position="last",
    )


def _markdown_table(frame: pd.DataFrame, columns: list[str], headers: list[str]) -> str:
    if frame.empty:
        return "无。"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            value = row.get(column, "")
            values.append(str(value).replace("|", "/").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def generate_reports(
    pool: dict[str, Any],
    members: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = _snapshot_frame(members, snapshots)
    report_date = snapshots[0]["trade_date"] if snapshots else pool["price_target_date"]
    date = report_date.replace("-", "")
    batch_slug = pool["batch_id"].lower()
    market_temperature = pool.get("market_temperature", "UNKNOWN")

    csv_path = output_dir / f"{batch_slug}_{date}_full.csv"
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")

    actionable = frame[frame["entry_state"] == "SYSTEM_BUYABLE"].copy()
    extended_all = frame[
        frame["entry_state"].isin(["WAIT_PULLBACK_EXTENDED", "WATCH_EXTENDED"])
    ].copy()
    extended_all["_extended_priority"] = extended_all["entry_state"].map(
        {"WAIT_PULLBACK_EXTENDED": 0, "WATCH_EXTENDED": 1}
    )
    extended = extended_all.sort_values(
        ["_extended_priority", "trend_score"],
        ascending=[True, False],
        na_position="last",
    ).head(10)
    near_trigger = frame[frame["entry_state"].isin(["WATCH_NEAR_TRIGGER", "SYSTEM_WATCH"])].copy()
    near_trigger["_ma20_distance"] = near_trigger["vs_ma20"].abs()
    near_trigger["_watch_priority"] = near_trigger["entry_state"].map(
        {"SYSTEM_WATCH": 0, "WATCH_NEAR_TRIGGER": 1}
    )
    watch = near_trigger.sort_values(
        ["_watch_priority", "_ma20_distance", "trend_score"],
        ascending=[True, True, False],
        na_position="last",
    ).head(10)

    def display_rows(source: pd.DataFrame) -> pd.DataFrame:
        if source.empty:
            return source
        result = source.copy()
        result["代码"] = result["security_id"]
        result["名称"] = result["name"]
        result["产业位置"] = result["industry_name"]
        result["RPS50/120/250"] = result.apply(
            lambda row: f"{_num(row['rps_50'])}/{_num(row['rps_120'])}/{_num(row['rps_250'])}", axis=1
        )
        result["20日涨幅"] = result["ret20"].map(_pct)
        result["距MA20"] = result["vs_ma20"].map(_pct)
        result["距MA60"] = result["vs_ma60"].map(_pct)
        result["动作"] = result["entry_state"]
        result["原因"] = result["decision_reason"]
        result["趋势状态"] = result["trend_state"]
        return result

    actionable_display = display_rows(actionable)
    extended_display = display_rows(extended)
    watch_display = display_rows(watch)

    brief_path = output_dir / f"{batch_slug}_{date}_brief.md"
    brief_lines = [
        f"# 截图池选股简报（{report_date}）",
        "",
        f"范围：{len(frame)}个证券，来自{len(pool['sources'])}张截图；市场温度：{market_temperature}。",
        "",
        "## 一、明确结论",
        "",
    ]
    if actionable.empty:
        brief_lines.append("**当前没有满足完整买入条件的截图池个股。** 不为了凑名单降低标准。")
    else:
        brief_lines.append(f"满足系统可执行条件：**{len(actionable)}只**。")
        brief_lines.append("")
        brief_lines.append(
            _markdown_table(
                actionable_display,
                ["代码", "名称", "产业位置", "RPS50/120/250", "距MA20", "动作"],
                ["代码", "名称", "产业位置", "RPS", "距MA20", "动作"],
            )
        )
    brief_lines.extend(["", "## 二、强势但不能追", ""])
    brief_lines.append(
        _markdown_table(
            extended_display,
            ["代码", "名称", "RPS50/120/250", "20日涨幅", "距MA60", "原因"],
            ["代码", "名称", "RPS", "20日涨幅", "距MA60", "处理"],
        )
    )
    brief_lines.extend(["", "## 三、下一批观察", ""])
    brief_lines.append(
        _markdown_table(
            watch_display,
            ["代码", "名称", "产业位置", "RPS50/120/250", "距MA20", "动作"],
            ["代码", "名称", "产业位置", "RPS", "距MA20", "状态"],
        )
    )
    brief_lines.extend(
        [
            "",
            "## 四、执行纪律",
            "",
            "- `SYSTEM_BUYABLE`：仅表示通过本项目的趋势与位置规则，不替代仓位和个人风险约束。",
            "- `WAIT_PULLBACK_EXTENDED`：强趋势但已过度乖离，不追高；等待MA20/MA60靠拢及RPS保持。",
            f"- H股当前没有可靠的{report_date}日线和横截面RPS，统一不进入买入名单。",
        ]
    )
    brief_path.write_text("\n".join(brief_lines) + "\n", encoding="utf-8")

    details_path = output_dir / f"{batch_slug}_{date}_detailed.md"
    distribution = (
        frame.groupby(["primary_theme_code", "industry_name"], dropna=False)
        .size()
        .reset_index(name="数量")
        .sort_values(["primary_theme_code", "数量"], ascending=[True, False])
    )
    distribution["主题"] = distribution["primary_theme_code"].fillna("ETF")
    distribution["行业/节点"] = distribution["industry_name"]
    state_counts = frame["entry_state"].value_counts(dropna=False).rename_axis("状态").reset_index(name="数量")

    detail_lines = [
        f"# AI硬件截图池详细研究报告（数据截至{report_date}）",
        "",
        f"批次：`{pool['batch_id']}`  ",
        f"原始出现次数：{sum(member['source_count'] for member in members)}；去重后：{len(members)}；截图：{len(pool['sources'])}张。  ",
        "用途：保留全量分类、产业链位置、趋势快照和数据缺口，供后续财报与新行情迭代。",
        "",
        "## 一、结论摘要",
        "",
        f"- 完整系统买入条件：{len(actionable)}只。",
        f"- 强势但过热、等待回踩：{len(extended_all)}只。",
        f"- H股、ETF、北交所、次新或过期行情等无完整当日A股RPS覆盖：{int((frame['entry_state'].isin(['DATA_GAP', 'DATA_STALE', 'NO_FULL_RPS'])).sum())}只。",
        f"- 当前市场温度为{market_temperature}，报告不因名单热度降低买点要求。",
        "",
        "## 二、代码校验",
        "",
        "按证券名称与本地通达信主表校正了4处冲突：中微公司688012、盛美上海688082、华丰科技688629、美畅股份300861。原始图片已归档。",
        "",
        "## 三、产业分类分布",
        "",
        _markdown_table(distribution, ["主题", "行业/节点", "数量"], ["主题", "行业/节点", "数量"]),
        "",
        "## 四、趋势状态分布",
        "",
        _markdown_table(state_counts, ["状态", "数量"], ["状态", "数量"]),
        "",
        "## 五、系统候选与观察池",
        "",
        "### 可执行候选",
        "",
        _markdown_table(
            actionable_display,
            ["代码", "名称", "产业位置", "RPS50/120/250", "20日涨幅", "距MA20", "距MA60", "原因"],
            ["代码", "名称", "产业位置", "RPS", "20日", "距MA20", "距MA60", "判断"],
        ),
        "",
        "### 过热等待",
        "",
        _markdown_table(
            extended_display,
            ["代码", "名称", "产业位置", "RPS50/120/250", "20日涨幅", "距MA60", "原因"],
            ["代码", "名称", "产业位置", "RPS", "20日", "距MA60", "判断"],
        ),
        "",
        "### 强势观察",
        "",
        _markdown_table(
            watch_display,
            ["代码", "名称", "产业位置", "RPS50/120/250", "20日涨幅", "距MA20", "动作"],
            ["代码", "名称", "产业位置", "RPS", "20日", "距MA20", "状态"],
        ),
        "",
        "## 六、全量产业链摆放与趋势",
        "",
    ]

    for theme_code, theme_frame in frame.groupby(frame["primary_theme_code"].fillna("ETF")):
        detail_lines.extend([f"### {theme_code}", ""])
        for industry, group in theme_frame.groupby("industry_name"):
            display = display_rows(group.sort_values("trend_score", ascending=False, na_position="last"))
            detail_lines.extend(
                [
                    f"**{industry}**",
                    "",
                    _markdown_table(
                        display,
                        ["代码", "名称", "RPS50/120/250", "20日涨幅", "距MA20", "趋势状态", "动作"],
                        ["代码", "名称", "RPS", "20日", "距MA20", "趋势", "动作"],
                    ),
                    "",
                ]
            )

    detail_lines.extend(
        [
            "## 七、方法与持续更新",
            "",
            "1. 新截图只新增一个批次输入文件，不覆盖历史批次。",
            "2. 名称由通达信证券主表校验；冲突必须记录校正原因。",
            "3. 分类采用主位置与次位置；单纯概念关联不提高瓶颈评分。",
            "4. 每次以全市场日线重算RPS50/120/250，并保存趋势快照，历史判断可回看。",
            "5. 买入状态由L1/L2、RPS、MA20/MA60及乖离共同决定；没有通过就明确写零。",
            "6. H股需要单独导入同日复权日线后才计算趋势；未覆盖时不进入买入候选。",
            "",
            "## 八、数据限制",
            "",
            f"- 本批A股数据来自本地通达信，最新交易日为{report_date}。",
            "- 北交所与ETF只计算自身均线趋势，不与现有沪深A股RPS横截面混用。",
            "- 港股本地历史数据过期，报告只保留行业和产业链位置，不输出伪精确RPS。",
            "- 产业链映射是研究入口，不等同于收入占比、订单或客户认证事实。",
        ]
    )
    details_path.write_text("\n".join(detail_lines) + "\n", encoding="utf-8")
    return brief_path, details_path, csv_path


def run_batch(
    pool_path: Path = DEFAULT_POOL_PATH,
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    tdx_dir: str = DEFAULT_TDX_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    price_target_date: str | None = None,
) -> dict[str, Any]:
    pool, classification = load_research_inputs(pool_path, classification_path)
    members, placements = materialize_batch(pool, classification, db_path=db_path)
    snapshots, price_date = compute_trend_snapshots(
        members,
        target_date=price_target_date or pool["price_target_date"],
        tdx_dir=tdx_dir,
        output_dir=ROOT / "output",
    )
    store_trend_snapshots(pool, snapshots, price_date, db_path=db_path)
    brief_path, details_path, csv_path = generate_reports(
        pool, members, snapshots, output_dir=output_dir
    )
    states = Counter(snapshot["entry_state"] for snapshot in snapshots)
    return {
        "batch_id": pool["batch_id"],
        "price_date": price_date,
        "member_count": len(members),
        "placement_count": sum(1 + len(value["secondary"]) for value in placements.values()),
        "states": dict(states),
        "brief_path": brief_path,
        "details_path": details_path,
        "csv_path": csv_path,
    }
