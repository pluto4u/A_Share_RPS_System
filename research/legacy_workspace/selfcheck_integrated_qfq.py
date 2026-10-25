#!/usr/bin/env python3
"""综合决策链前复权修复的可重复自检。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "a-share-rotation-trader" / "scripts" / "run_integrated_decision.py"
DB = ROOT / "data" / "hithink" / "market.duckdb"
TRUSTED = ROOT / "output" / "hithink_rps_latest.csv"
OUTPUT = ROOT / "output" / "integrated_qfq_selfcheck_20260830.json"


def load_module():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("integrated_decision_selfcheck", SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载 {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_query(sql: str) -> list[dict]:
    exe = "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"
    proc = subprocess.run(
        [exe, "--db", str(DB), "db", "query", "--sql", sql, "--format", "json"],
        capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "db query failed")
    payload = json.loads(proc.stdout)
    if not payload.get("ok"):
        raise RuntimeError(str(payload))
    return payload.get("data") or []


def main() -> int:
    if not DB.exists() or not TRUSTED.exists() or shutil.which("hithink-finance.cmd") is None:
        raise SystemExit("缺少生产库、冻结 RPS 快照或 hithink-finance CLI")
    module = load_module()
    trusted = pd.read_csv(TRUSTED)
    as_of = pd.Timestamp(trusted["trade_date"].max())
    daily, meta = module.load_hithink_qfq(DB, as_of, n_bars=320, min_bars=260)
    calculated = module.calculate_rps(
        daily.drop(columns=["name"]), periods=[50, 120, 250], verbose=False)
    latest = calculated[calculated["trade_date"] == calculated["trade_date"].max()][
        ["ts_code", "RPS_50", "RPS_120", "RPS_250"]]
    merged = latest.merge(
        trusted[["ts_code", "RPS_50", "RPS_120", "RPS_250"]],
        on="ts_code", suffixes=("_new", "_trusted"), validate="one_to_one")

    rps_checks = {}
    for period in (50, 120, 250):
        left, right = f"RPS_{period}_new", f"RPS_{period}_trusted"
        valid = merged[[left, right]].dropna()
        diff = (valid[left] - valid[right]).abs()
        rps_checks[str(period)] = {
            "compared": int(len(valid)),
            "max_abs_diff": float(diff.max()),
            "median_abs_diff": float(diff.median()),
        }

    new_gate = set(merged.loc[merged["RPS_120_new"] >= 80, "ts_code"])
    trusted_gate = set(merged.loc[merged["RPS_120_trusted"] >= 80, "ts_code"])
    raw_consistency = db_query(
        "SELECT COUNT(*) AS joined_rows, "
        "SUM(CASE WHEN ABS(q.volume-r.volume)>0.000001 THEN 1 ELSE 0 END) AS volume_mismatch, "
        "SUM(CASE WHEN ABS(q.amount-r.amount)>0.01 THEN 1 ELSE 0 END) AS amount_mismatch, "
        "SUM(CASE WHEN ABS(q.close-r.close)>0.000001 THEN 1 ELSE 0 END) AS adjusted_price_rows "
        "FROM v_daily_qfq q JOIN v_daily r USING(thscode,date)")[0]
    latest_anchor = db_query(
        "WITH d AS (SELECT MAX(date) AS date FROM v_daily_qfq) "
        "SELECT q.date, COUNT(*) AS symbols, "
        "SUM(CASE WHEN ABS(q.close-r.close)>0.000001 THEN 1 ELSE 0 END) AS close_mismatch "
        "FROM v_daily_qfq q JOIN v_daily r USING(thscode,date), d "
        "WHERE q.date=d.date GROUP BY q.date")[0]

    script_hash = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    mirror = ROOT / ".agents" / "skills" / "a-share-rotation-trader" / "scripts" / "run_integrated_decision.py"
    mirror_hash = hashlib.sha256(mirror.read_bytes()).hexdigest()
    passed = (
        all(item["max_abs_diff"] <= 1e-10 for item in rps_checks.values())
        and new_gate == trusted_gate
        and int(raw_consistency["volume_mismatch"]) == 0
        and int(raw_consistency["amount_mismatch"]) == 0
        and int(latest_anchor["close_mismatch"]) == 0
        and int(raw_consistency["adjusted_price_rows"]) > 0
        and script_hash == mirror_hash
    )
    report = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "passed" if passed else "failed",
        "as_of": as_of.strftime("%Y-%m-%d"),
        "database": str(DB),
        "loader": meta,
        "rps_reconciliation": rps_checks,
        "rps120_ge80": {
            "new": len(new_gate),
            "trusted": len(trusted_gate),
            "overlap": len(new_gate & trusted_gate),
            "false_in": len(new_gate - trusted_gate),
            "false_out": len(trusted_gate - new_gate),
        },
        "raw_volume_amount_consistency": raw_consistency,
        "latest_price_anchor": latest_anchor,
        "skill_copies": {
            "skills_sha256": script_hash,
            "agents_sha256": mirror_hash,
            "identical": script_hash == mirror_hash,
        },
        "production_gate_note": (
            "本自检确认计算输入与 RPS 口径；正式交易链服从 output/qfq_verification.json 的本地运营 G0 硬门。"
        ),
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "as_of": report["as_of"],
        "symbols": meta["symbols"],
        "rps120_gate": report["rps120_ge80"],
        "volume_mismatch": raw_consistency["volume_mismatch"],
        "amount_mismatch": raw_consistency["amount_mismatch"],
        "latest_close_mismatch": latest_anchor["close_mismatch"],
        "output": str(OUTPUT),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
