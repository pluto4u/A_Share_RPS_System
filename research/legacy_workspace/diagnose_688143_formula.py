#!/usr/bin/env python3
"""逐条件对账 688143 长盈通：通达信原式 vs 当前 Python V2。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from screen_siruipu_right_side_v2 import add_indicators


ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "output" / "tdx_stock_daily_with_rps.parquet"
CODE = "688143.SH"


def b(value) -> bool:
    return bool(value) if not pd.isna(value) else False


def main() -> int:
    cols = [
        "trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
        "RPS_50", "RPS_120", "RPS_250",
    ]
    raw = pd.read_parquet(PARQUET, columns=cols)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    stock = raw[raw["ts_code"] == CODE].sort_values("trade_date").copy()
    g = add_indicators(stock).reset_index(drop=True)
    r = g.iloc[-1]
    bars = len(g)
    c = float(r["close"])

    common = {
        "BASE": bars > 250,
        "DD": b(r["DD"]),
        "LOWNEAR": b(r["LOWNEAR"]),
        "C_GT_M20": c > float(r["M20"]),
        "M5_GT_M10": float(r["M5"]) > float(r["M10"]),
        "M5_GT_REF2": float(r["M5"]) > float(r["M5_REF2"]),
        "C_GT_HH5_REF": c > float(r["HH5_REF"]),
        "C_GT_M120": c > float(r["M120"]),
        "C_GT_HH10_REF": c > float(r["HH10_REF"]),
        "V_GE_VM20_1P10": float(r["vol"]) >= float(r["VM20"]) * 1.10,
        "C_GT_M60": c > float(r["M60"]),
        "M10_GT_M20": float(r["M10"]) > float(r["M20"]),
        "C_GT_HH20_REF": c > float(r["HH20_REF"]),
        "MH_GT_0": float(r["MH"]) > 0,
        "MH_GT_REF1": float(r["MH"]) > float(r["MH_REF1"]),
        "V_GE_VM20_1P80": float(r["vol"]) >= float(r["VM20"]) * 1.80,
        "DIF_GT_0": float(r["DIF"]) > 0,
        "C_M20_LE_1P12": c / float(r["M20"]) <= 1.12,
    }
    yj = all(common[k] for k in [
        "DD", "LOWNEAR", "C_GT_M20", "M5_GT_M10", "M5_GT_REF2",
        "C_GT_HH5_REF", "MH_GT_0", "MH_GT_REF1", "C_M20_LE_1P12",
    ])
    qd = all(common[k] for k in [
        "DD", "LOWNEAR", "C_GT_M20", "M5_GT_M10", "C_GT_M120",
        "C_GT_HH10_REF", "V_GE_VM20_1P10", "MH_GT_0",
    ])
    qr = all(common[k] for k in [
        "DD", "C_GT_M60", "M10_GT_M20", "C_GT_HH20_REF", "MH_GT_0",
    ])
    qqr = qr and common["V_GE_VM20_1P80"] and common["DIF_GT_0"]
    exact = {"YJ": yj, "QD": qd, "QR": qr, "QQR": qqr, "XG": common["BASE"] and (yj or qd or qr or qqr)}

    v2_extra = {
        "ORDER_OK_PEAK_AFTER_LOW": b(r["ORDER_OK"]),
        "ADV20_GE_1E8": float(r["ADV20"]) >= 1.0e8,
        "YJ_C_M20_LE_1P04": c / float(r["M20"]) <= 1.04,
        "QD_C_M20_LE_1P15": c / float(r["M20"]) <= 1.15,
        "QD_V_LE_VM20_3": float(r["vol"]) <= float(r["VM20"]) * 3.0,
        "QR_C_M20_LE_1P18": c / float(r["M20"]) <= 1.18,
    }
    payload = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(PARQUET.as_posix()),
        "price_adjustment": "hithink v_daily_qfq research view",
        "thscode": CODE,
        "name": "长盈通",
        "as_of": r["trade_date"].strftime("%Y-%m-%d"),
        "bars": bars,
        "values": {
            "close": round(c, 4), "M5": round(float(r["M5"]), 4),
            "M10": round(float(r["M10"]), 4), "M20": round(float(r["M20"]), 4),
            "M60": round(float(r["M60"]), 4), "M120": round(float(r["M120"]), 4),
            "vs_M20_pct": round((c / float(r["M20"]) - 1) * 100, 4),
            "vol_ratio": round(float(r["vol"] / r["VM20"]), 4),
            "ADV20_yi": round(float(r["ADV20"]) / 1e8, 4),
            "DIF": round(float(r["DIF"]), 6), "DEA": round(float(r["DEA"]), 6),
            "MH": round(float(r["MH"]), 6), "MH_REF1": round(float(r["MH_REF1"]), 6),
            "LOWAGE": int(r["LOWAGE"]), "PEAKAGE": int(r["PEAKAGE"]),
            "RPS_50": round(float(r["RPS_50"]), 4),
            "RPS_120": round(float(r["RPS_120"]), 4),
            "RPS_250": round(float(r["RPS_250"]), 4),
        },
        "tdx_exact_conditions": common,
        "tdx_exact_result": exact,
        "python_v2_extra_conditions": v2_extra,
        "python_v2_result": {
            "YJ": b(r["YJ"]), "QD": b(r["QD"]), "QR": b(r["QR"]),
            "QQR": b(r["QQR"]), "STRUCT": b(r["STRUCT"]),
        },
    }
    out = ROOT / "output" / "diagnose_688143_formula_20260825.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
