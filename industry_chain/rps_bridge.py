"""Join structured industry-chain research with the existing RPS output pools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from .database import IndustryChainDB


POOL_SUFFIXES = {
    "l1": ".csv",
    "l2": "_L2.csv",
    "super": "_super.csv",
    "huoche": "_huoche.csv",
    "early": "_early_L1.csv",
}


def _pool_pattern(pool: str) -> re.Pattern[str]:
    suffix = re.escape(POOL_SUFFIXES[pool])
    return re.compile(rf"^tdx_screener_(\d{{8}}){suffix}$")


def find_pool_file(
    output_dir: Path | str,
    pool: str = "super",
    date: str | None = None,
) -> tuple[Path, str]:
    if pool not in POOL_SUFFIXES:
        raise ValueError(f"未知候选池: {pool}")
    output_dir = Path(output_dir)
    if date:
        normalized = date.replace("-", "")
        path = output_dir / f"tdx_screener_{normalized}{POOL_SUFFIXES[pool]}"
        if not path.exists():
            raise FileNotFoundError(f"未找到候选池文件: {path}")
        return path, normalized

    pattern = _pool_pattern(pool)
    candidates = []
    for path in output_dir.glob("tdx_screener_*.csv"):
        match = pattern.match(path.name)
        if match:
            candidates.append((match.group(1), path))
    if not candidates:
        raise FileNotFoundError(f"{output_dir} 下没有 {pool} 候选池")
    return max(candidates, key=lambda item: item[0])[1], max(candidates)[0]


def _weighted_rps(row: pd.Series) -> float:
    weights = {"RPS_50": 0.20, "RPS_120": 0.40, "RPS_250": 0.40}
    available = [(column, weight) for column, weight in weights.items() if pd.notna(row.get(column))]
    total_weight = sum(weight for _, weight in available)
    if not total_weight:
        return 0.0
    return sum(float(row[column]) * weight for column, weight in available) / total_weight


def _truthy(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _confluence_score(row: pd.Series, pool: str) -> float:
    layers = 1
    for column in ("IN_L2", "IN_HUOCHE", "IN_EARLY", "HUOCHE_FIRST_W"):
        layers += int(_truthy(row.get(column)))
    base = {"l1": 55, "l2": 65, "early": 70, "huoche": 75, "super": 82}[pool]
    return min(100.0, base + max(0, layers - 1) * 5.0)


def _heat_penalty(row: pd.Series) -> float:
    ret20 = float(row.get("ret20", 0) or 0)
    vs_ma60 = float(row.get("vs_ma60", 0) or 0)
    ret_penalty = max(0.0, min(10.0, (ret20 - 0.30) * 25.0))
    distance_penalty = max(0.0, min(10.0, (vs_ma60 - 0.35) * 25.0))
    return round(ret_penalty + distance_penalty, 2)


def analyze_rps_candidates(
    db: IndustryChainDB,
    output_dir: Path | str,
    date: str | None = None,
    pool: str = "super",
    persist: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rank mapped RPS candidates using both momentum and bottleneck evidence."""
    pool_path, resolved_date = find_pool_file(output_dir, pool=pool, date=date)
    candidates = pd.read_csv(pool_path, encoding="utf-8-sig")
    if "ts_code" not in candidates.columns:
        raise ValueError(f"候选池缺少 ts_code 列: {pool_path}")

    research = pd.DataFrame(db.ranked_exposures())
    research = research.dropna(subset=["ts_code"])
    if not research.empty:
        research = research.sort_values(
            ["bottleneck_score", "research_confidence"], ascending=False
        ).drop_duplicates("ts_code")

    mapped_codes = set(research["ts_code"].astype(str)) if not research.empty else set()
    if persist:
        db.record_signal_observations(
            candidates.to_dict(orient="records"),
            resolved_date,
            pool,
            mapped_codes,
        )

    merged = candidates.merge(research, on="ts_code", how="inner", suffixes=("", "_research"))
    if merged.empty:
        return merged, {
            "date": resolved_date,
            "pool": pool,
            "pool_path": str(pool_path),
            "candidate_count": len(candidates),
            "mapped_count": 0,
            "unmapped_count": len(candidates),
            "unmapped_codes": candidates["ts_code"].astype(str).tolist(),
        }

    merged["rps_score"] = merged.apply(_weighted_rps, axis=1).round(2)
    merged["confluence_score"] = merged.apply(lambda row: _confluence_score(row, pool), axis=1)
    merged["signal_score"] = (
        0.82 * merged["rps_score"] + 0.18 * merged["confluence_score"]
    ).round(2)
    merged["heat_penalty"] = merged.apply(_heat_penalty, axis=1)
    merged["combined_score"] = (
        0.62 * merged["bottleneck_score"]
        + 0.38 * merged["signal_score"]
        - merged["heat_penalty"]
    ).clip(0, 100).round(2)

    def decision(row: pd.Series) -> str:
        if (
            row["combined_score"] >= 75
            and row["research_confidence"] >= 65
            and row["heat_penalty"] <= 8
        ):
            return "A-重点核验"
        if row["combined_score"] >= 65 and row["research_confidence"] >= 55:
            return "B-候选观察"
        if row["research_confidence"] < 55:
            return "C-补证据"
        return "C-暂缓"

    merged["decision"] = merged.apply(decision, axis=1)
    merged["analysis_date"] = resolved_date
    merged["source_pool"] = pool
    merged = merged.sort_values(
        ["combined_score", "research_confidence"], ascending=False
    ).reset_index(drop=True)

    return merged, {
        "date": resolved_date,
        "pool": pool,
        "pool_path": str(pool_path),
        "candidate_count": len(candidates),
        "mapped_count": len(merged),
        "unmapped_count": len(candidates) - len(merged),
        "unmapped_codes": sorted(set(candidates["ts_code"].astype(str)) - mapped_codes),
    }
