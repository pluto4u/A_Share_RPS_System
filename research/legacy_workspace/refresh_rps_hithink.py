#!/usr/bin/env python3
"""用同花顺本地 v_daily_qfq 重算 RPS，刷新 output/tdx_stock_daily_with_rps.parquet。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rps_analyzer.rps_calculator import calculate_rps

DB = ROOT / "data" / "hithink" / "market.duckdb"
OUT_DIR = ROOT / "output"
PARQUET = OUT_DIR / "tdx_stock_daily_with_rps.parquet"
LATEST_CSV = OUT_DIR / "hithink_rps_latest.csv"
META = OUT_DIR / "hithink_rps_refresh.json"
TMP = Path(os.environ.get("TEMP", ".")) / "hithink-rps-refresh"


def export(sql: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    exe = "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"
    cmd = [exe, "--db", str(DB), "db", "export", "--sql", sql,
           "--output", str(dest), "--file-format", "parquet", "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "export failed")
    return dest


def main() -> int:
    if not DB.exists():
        raise SystemExit(f"缺少本地库 {DB}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    print("[1] 导出同花顺沪深 A 股前复权日 K")
    raw_path = export(
        "SELECT q.date AS trade_date, q.open, q.high, q.low, q.close, q.amount, "
        "q.volume AS vol, q.thscode AS ts_code, s.name "
        "FROM v_daily_qfq q JOIN dim_symbol s ON q.thscode=s.thscode "
        "WHERE s.asset_type='a-share' AND s.exchange IN ('SH','SZ')",
        TMP / "qfq_hs_a.parquet",
    )
    df = pd.read_parquet(raw_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    names = df[["ts_code", "name"]].drop_duplicates("ts_code")
    bars = df.drop(columns=["name"])
    print(f"    {len(bars)} 行 {bars['ts_code'].nunique()} 只 "
          f"{bars['trade_date'].min().date()} → {bars['trade_date'].max().date()}")

    print("[2] 计算 RPS_50 / RPS_120 / RPS_250")
    rps = calculate_rps(bars, periods=[50, 120, 250], verbose=True)
    rps = rps.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

    if PARQUET.exists():
        bak = OUT_DIR / "tdx_stock_daily_with_rps_tdx_raw_backup.parquet"
        if not bak.exists():
            print(f"[3] 备份旧通达信未复权结果 → {bak.name}")
            shutil.copy2(PARQUET, bak)
        else:
            print(f"[3] 已有备份 {bak.name}，跳过复制")

    print(f"[4] 写入 {PARQUET}")
    rps.to_parquet(PARQUET, index=False)

    asof = rps["trade_date"].max()
    latest = rps[rps["trade_date"] == asof].merge(names, on="ts_code", how="left")
    latest = latest.sort_values(["RPS_120", "RPS_250"], ascending=False)
    cols = ["trade_date", "ts_code", "name", "close", "amount", "vol",
            "RPS_50", "RPS_120", "RPS_250"]
    latest[cols].to_csv(LATEST_CSV, index=False, encoding="utf-8-sig")

    ready250 = int(latest["RPS_250"].notna().sum())
    ge80 = int((latest["RPS_120"] >= 80).sum())
    tz = timezone(timedelta(hours=8))
    meta = {
        "refreshed_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "price_source": "hithink v_daily_qfq (local rebuild, adjust=forward)",
        "amount_source": "hithink raw amount (yuan, not price-adjusted)",
        "universe": "沪深 A 股，已排除北交所",
        "formula": "RPS_n = rank_pct((close_t - close_{t-n}) / close_{t-n}) * 100",
        "periods": [50, 120, 250],
        "as_of": asof.strftime("%Y-%m-%d"),
        "rows": int(len(rps)),
        "names": int(rps["ts_code"].nunique()),
        "date_min": rps["trade_date"].min().strftime("%Y-%m-%d"),
        "date_max": rps["trade_date"].max().strftime("%Y-%m-%d"),
        "asof_names": int(len(latest)),
        "asof_rps250_ready": ready250,
        "asof_rps120_ge_80": ge80,
        "parquet": str(PARQUET.as_posix()),
        "latest_csv": str(LATEST_CSV.as_posix()),
        "backup": "output/tdx_stock_daily_with_rps_tdx_raw_backup.parquet",
        "note": "下游脚本若仍读本 parquet，现已是同花顺前复权 RPS。涨停生态继续用通达信未复权。G0 未改。",
        "disclaimer": "非投资建议。",
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "as_of": meta["as_of"],
        "rows": meta["rows"],
        "names": meta["names"],
        "rps120_ge_80": ge80,
        "rps250_ready": ready250,
        "parquet": str(PARQUET),
        "latest_csv": str(LATEST_CSV),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
