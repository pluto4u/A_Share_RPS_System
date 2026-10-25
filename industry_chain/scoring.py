"""Transparent scoring rules for bottleneck ownership and supply constraints."""

from __future__ import annotations

from typing import Any, Iterable


def _number(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    return default if value is None else float(value)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def score_node(row: dict[str, Any]) -> float:
    """Score how likely an industry node is to remain a physical bottleneck."""
    lead_time = min(_number(row, "expansion_lead_months") / 36.0 * 100.0, 100.0)
    certification = min(_number(row, "certification_months") / 24.0 * 100.0, 100.0)
    score = (
        0.16 * _number(row, "supply_concentration", 50)
        + 0.20 * _number(row, "substitution_difficulty", 50)
        + 0.14 * (100.0 - _number(row, "capacity_elasticity", 50))
        + 0.10 * lead_time
        + 0.10 * certification
        + 0.14 * _number(row, "node_pricing_power", row.get("pricing_power", 50))
        + 0.16 * _number(row, "demand_certainty", 50)
    )
    return round(_clamp(score), 2)


def score_exposure(row: dict[str, Any]) -> float:
    """Score whether a company actually owns the identified bottleneck."""
    score = (
        0.10 * _number(row, "exposure_purity", 50)
        + 0.18 * _number(row, "technical_barrier", 50)
        + 0.15 * _number(row, "capacity_scarcity", 50)
        + 0.12 * _number(row, "pricing_power", 50)
        + 0.12 * _number(row, "customer_stickiness", 50)
        + 0.18 * _number(row, "bottleneck_ownership", 50)
        + 0.08 * _number(row, "mapping_confidence", 50)
        + 0.07 * _number(row, "evidence_quality", 50)
    )
    return round(_clamp(score), 2)


def rank_company_exposures(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add component scores and return highest-conviction mappings first."""
    ranked = []
    for source in rows:
        row = dict(source)
        row["node_score"] = score_node(row)
        row["ownership_score"] = score_exposure(row)
        row["bottleneck_score"] = round(
            0.42 * row["node_score"] + 0.58 * row["ownership_score"], 2
        )
        confidence = _number(row, "mapping_confidence", 50)
        quality = _number(row, "evidence_quality", 50)
        row["research_confidence"] = round(0.55 * confidence + 0.45 * quality, 2)
        ranked.append(row)
    return sorted(
        ranked,
        key=lambda item: (item["bottleneck_score"], item["research_confidence"]),
        reverse=True,
    )
