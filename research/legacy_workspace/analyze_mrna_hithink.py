#!/usr/bin/env python3
"""同花顺前复权研究口径下的三只 mRNA 相关股票横向诊断。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from screen_siruipu_right_side_v2 import add_indicators


ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
TARGETS = {
    "300142.SZ": "沃森生物",
    "688185.SH": "康希诺",
    "002821.SZ": "凯莱英",
}


def pct(value: float | None) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value) * 100, 2)


def main() -> int:
    cols = [
        "trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
        "RPS_50", "RPS_120", "RPS_250",
    ]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw = raw[raw["ts_code"].isin(TARGETS)].copy()
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    rows = []
    for code, frame in raw.groupby("ts_code", sort=False):
        g = add_indicators(frame.sort_values("trade_date").tail(500)).reset_index(drop=True)
        last = g.iloc[-1]
        prior = g.iloc[-2] if len(g) >= 2 else None
        close = float(last["close"])
        ret_1d = close / float(prior["close"]) - 1 if prior is not None else None
        ret_5d = close / float(g.iloc[-6]["close"]) - 1 if len(g) >= 6 else None
        ret_20d = close / float(g.iloc[-21]["close"]) - 1 if len(g) >= 21 else None
        volume_ratio = float(last["vol"] / last["VM20"]) if pd.notna(last["VM20"]) else None
        amount_ratio = float(last["amount"] / last["ADV20"]) if pd.notna(last["ADV20"]) else None
        rows.append({
            "thscode": code,
            "name": TARGETS[code],
            "as_of": last["trade_date"].strftime("%Y-%m-%d"),
            "close": round(close, 2),
            "ret_1d_pct": pct(ret_1d),
            "ret_5d_pct": pct(ret_5d),
            "ret_20d_pct": pct(ret_20d),
            "rps50": round(float(last["RPS_50"]), 2),
            "rps120": round(float(last["RPS_120"]), 2),
            "rps250": round(float(last["RPS_250"]), 2),
            "ma20": round(float(last["M20"]), 2),
            "ma60": round(float(last["M60"]), 2),
            "above_ma20": bool(close > float(last["M20"])),
            "above_ma60": bool(close > float(last["M60"])),
            "vs_ma20_pct": pct(close / float(last["M20"]) - 1),
            "adv20_yi": round(float(last["ADV20"]) / 1e8, 2),
            "amount_yi": round(float(last["amount"]) / 1e8, 2),
            "amount_vs_adv20": None if amount_ratio is None else round(amount_ratio, 2),
            "volume_vs_vma20": None if volume_ratio is None else round(volume_ratio, 2),
            "right_side": {
                "YJ": bool(last["YJ"]),
                "QD": bool(last["QD"]),
                "QR": bool(last["QR"]),
                "QQR": bool(last["QQR"]),
                "STRUCT": bool(last["STRUCT"]),
            },
            "research_gate_g2": bool(
                close > float(last["M20"])
                and close > float(last["M60"])
                and float(last["RPS_120"]) >= 80
                and close / float(last["M20"]) <= 1.12
                and float(last["ADV20"]) >= 1.5e8
            ),
        })

    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "hithink local v_daily_qfq; amount/volume raw",
        "scope_note": "用户指定的 mRNA 比较篮子；不是经版本化概念成分验证的官方板块",
        "price_gate_note": "研究诊断；本项目 output/qfq_verification.json 未过时，不得升级为可执行交易指标",
        "stocks": sorted(rows, key=lambda x: x["thscode"]),
    }
    out = ROOT / "output" / "hithink_mrna_analysis_20260825.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
