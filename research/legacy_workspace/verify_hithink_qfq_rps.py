#!/usr/bin/env python3
"""验证：本地 RPS 方案能否使用同花顺本地重建前复权（v_daily_qfq）。

对照三套收盘价：
  1. 通达信未复权 close
  2. 同花顺 v_daily_qfq close
  3. 用同花顺日频 forward_factor 把通达信未复权还原成等比前复权

输出只写文件，不把长序列打进 stdout。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.tdx_reader import load_tdx_market

AS_OF = date(2026, 8, 21)
LOOKBACK_START = date(2024, 8, 1)  # 覆盖 RPS_250 所需 250 根 + 余量
DB = ROOT / "data" / "hithink" / "market.duckdb"
OUT_JSON = ROOT / "output" / "hithink_qfq_rps_verify_20260821.json"
TDX = r"C:\new_tdx\vipdoc"
SAMPLE_N = 36


def piecewise_constant(series: np.ndarray, step_tol: float, flat_tol: float, max_steps: int):
    if len(series) < 5:
        return None
    diff = np.abs(np.diff(series))
    step_idx = (np.flatnonzero(diff > step_tol) + 1).tolist()
    if len(step_idx) > max_steps:
        return None
    bounds = [0, *step_idx, len(series)]
    worst = 0.0
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        seg = series[lo:hi]
        if len(seg) >= 2:
            worst = max(worst, float(seg.max() - seg.min()))
    if worst > flat_tol:
        return None
    return step_idx, worst


def _cli_export(sql: str, dest: Path) -> Path:
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    exe = "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"
    cmd = [
        exe, "--db", str(DB),
        "db", "export", "--sql", sql, "--output", str(dest),
        "--file-format", "parquet", "--format", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "db export failed")
    return dest


def load_hithink() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tmp = Path(os.environ.get("TEMP", ".")) / "hithink-rps-verify"
    start, end = LOOKBACK_START.isoformat(), AS_OF.isoformat()
    qfq = pd.read_parquet(_cli_export(
        "SELECT q.thscode AS ts_code, q.date AS trade_date, q.close, q.amount "
        "FROM v_daily_qfq q JOIN dim_symbol s ON q.thscode=s.thscode "
        f"WHERE s.asset_type='a-share' AND s.exchange IN ('SH','SZ') "
        f"AND q.date>=DATE '{start}' AND q.date<=DATE '{end}'",
        tmp / "qfq.parquet"))
    raw = pd.read_parquet(_cli_export(
        "SELECT r.thscode AS ts_code, r.date AS trade_date, r.close AS close_raw_hithink "
        "FROM v_daily r JOIN dim_symbol s ON r.thscode=s.thscode "
        f"WHERE s.asset_type='a-share' AND s.exchange IN ('SH','SZ') "
        f"AND r.date>=DATE '{start}' AND r.date<=DATE '{end}'",
        tmp / "raw.parquet"))
    fac = pd.read_parquet(_cli_export(
        "SELECT thscode AS ts_code, date AS trade_date, forward_factor "
        f"FROM calc_adjust_factor_daily WHERE date>=DATE '{start}' AND date<=DATE '{end}'",
        tmp / "fac.parquet"))
    events = pd.read_parquet(_cli_export(
        "SELECT thscode AS ts_code, ex_date, dividend_per_share, per_share_bonus, "
        "rights_ratio, rights_price FROM raw_adjustment_events "
        f"WHERE ex_date>=DATE '{start}' AND ex_date<=DATE '{end}'",
        tmp / "evt.parquet"))
    qfq["trade_date"] = pd.to_datetime(qfq["trade_date"])
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    fac["trade_date"] = pd.to_datetime(fac["trade_date"])
    return qfq, raw, fac, events


def convention_one(ts_code: str, merged: pd.DataFrame) -> dict:
    sub = merged[merged["ts_code"] == ts_code].dropna(subset=["close_qfq", "close_tdx"]).sort_values("trade_date")
    if len(sub) < 60:
        return {"ts_code": ts_code, "status": "unknown", "reason": f"共同交易日仅 {len(sub)}"}
    raw = sub["close_tdx"].to_numpy(dtype=float)
    qfq = sub["close_qfq"].to_numpy(dtype=float)
    if np.any(raw <= 0) or np.any(qfq <= 0):
        return {"ts_code": ts_code, "status": "unknown", "reason": "非正价格"}
    tick = 0.011
    med = float(np.median(raw))
    ratio = qfq / raw
    offset = qfq - raw
    max_steps = max(2, int(8 * len(sub) / 250))
    mult = piecewise_constant(ratio, 4 * tick / med, 3 * tick / med, max_steps)
    addi = piecewise_constant(offset, 4 * tick, 3 * tick, max_steps)
    latest_ratio = float(ratio[-1])
    latest_off = float(offset[-1])
    if addi is not None and (mult is None or len(addi[0]) <= len(mult[0])):
        convention, steps = "additive_dividend", addi[0]
        anchor_ok = abs(latest_off) <= 0.03
    elif mult is not None:
        convention, steps = "multiplicative_factor", mult[0]
        anchor_ok = abs(latest_ratio - 1.0) <= 0.008
    else:
        return {
            "ts_code": ts_code, "status": "unmapped", "convention": "none",
            "common_bars": int(len(sub)),
            "latest_ratio": round(latest_ratio, 6),
            "latest_offset": round(latest_off, 4),
            "max_abs_ratio_dev_from_1": round(float(np.max(np.abs(ratio - 1))), 6),
            "reason": "qfq/raw 与 qfq-raw 均非分段恒定",
        }
    # 除权日方向：前复权收益不应低于未复权收益
    bad_ex = 0
    for idx in steps:
        raw_ret = raw[idx] / raw[idx - 1] - 1.0
        qfq_ret = qfq[idx] / qfq[idx - 1] - 1.0
        if qfq_ret < raw_ret - 5e-4:
            bad_ex += 1
    status = "verified_qfq" if anchor_ok and bad_ex == 0 else "unadjusted"
    return {
        "ts_code": ts_code,
        "status": status,
        "convention": convention,
        "common_bars": int(len(sub)),
        "n_steps": len(steps),
        "latest_ratio": round(latest_ratio, 6),
        "latest_offset": round(latest_off, 4),
        "latest_tdx": round(float(raw[-1]), 4),
        "latest_qfq": round(float(qfq[-1]), 4),
        "bad_ex_direction": bad_ex,
        "anchor_ok": anchor_ok,
    }


def spearman(a: pd.Series, b: pd.Series) -> float:
    ra = a.rank()
    rb = b.rank()
    return float(ra.corr(rb))


def main() -> int:
    print("[1] 读取同花顺 DuckDB 前复权 / 未复权 / 因子")
    qfq, hraw, fac, events = load_hithink()
    qfq = qfq.rename(columns={"close": "close_qfq", "amount": "amount_qfq"})
    print(f"    qfq {len(qfq)} 行 {qfq['ts_code'].nunique()} 只")

    print("[2] 读取通达信未复权日线")
    tdx = load_tdx_market(TDX, min_days=200, verbose=True)
    tdx = tdx[(tdx["trade_date"] >= pd.Timestamp(LOOKBACK_START)) & (tdx["trade_date"] <= pd.Timestamp(AS_OF))]
    tdx = tdx.rename(columns={"close": "close_tdx", "amount": "amount_tdx"})
    print(f"    tdx 窗口 {len(tdx)} 行 {tdx['ts_code'].nunique()} 只")

    merged = qfq.merge(
        tdx[["ts_code", "trade_date", "close_tdx", "amount_tdx"]],
        on=["ts_code", "trade_date"], how="inner",
    )
    merged = merged.merge(hraw, on=["ts_code", "trade_date"], how="left")
    merged = merged.merge(fac, on=["ts_code", "trade_date"], how="left")

    asof = pd.Timestamp(AS_OF)
    asof_m = merged[merged["trade_date"] == asof]
    latest_abs = (asof_m["close_qfq"] - asof_m["close_tdx"]).abs()
    latest_hithink_raw = (asof_m["close_raw_hithink"] - asof_m["close_tdx"]).abs()

    # 抽样：有除权事件的优先，其余随机
    ev_codes = set(events["ts_code"].astype(str))
    common_codes = sorted(set(asof_m["ts_code"]))
    with_ev = [c for c in common_codes if c in ev_codes]
    without = [c for c in common_codes if c not in ev_codes]
    rng = np.random.default_rng(20260821)
    sample = list(rng.choice(with_ev, size=min(24, len(with_ev)), replace=False)) if with_ev else []
    if without:
        sample += list(rng.choice(without, size=min(12, len(without)), replace=False))

    print(f"[3] 复权约定抽样 {len(sample)} 只")
    details = [convention_one(c, merged) for c in sample]
    conv_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for d in details:
        conv_counts[d.get("convention", "none")] = conv_counts.get(d.get("convention", "none"), 0) + 1
        status_counts[d.get("status", "?")] = status_counts.get(d.get("status", "?"), 0) + 1

    # 用同花顺因子把通达信还原成前复权：qfq ≈ raw * factor / factor_last
    print("[4] 用 forward_factor 重建通达信前复权并计算两套 RPS")
    work = merged.dropna(subset=["close_qfq", "close_tdx"]).copy()
    last_fac = work.loc[work["trade_date"] == asof, ["ts_code", "forward_factor"]].rename(
        columns={"forward_factor": "factor_last"})
    work = work.merge(last_fac, on="ts_code", how="left")
    work["close_tdx_qfq"] = np.where(
        work["forward_factor"].notna() & work["factor_last"].notna() & (work["factor_last"] != 0),
        work["close_tdx"] * work["forward_factor"] / work["factor_last"],
        np.nan,
    )

    def rps_on(df: pd.DataFrame, close_col: str, label: str) -> pd.DataFrame:
        sub = df[["trade_date", "ts_code", close_col]].dropna().rename(columns={close_col: "close"})
        print(f"    RPS {label}: {sub['ts_code'].nunique()} 只 {len(sub)} 行")
        out = calculate_rps(sub, periods=[50, 120, 250], verbose=False)
        return out[out["trade_date"] == asof][["ts_code", "RPS_50", "RPS_120", "RPS_250", "close"]]

    rps_qfq = rps_on(work, "close_qfq", "hithink_qfq")
    rps_raw = rps_on(work, "close_tdx", "tdx_raw")
    rps_reb = rps_on(work, "close_tdx_qfq", "tdx_x_hithink_factor")

    cmp_qr = rps_qfq.merge(rps_raw, on="ts_code", suffixes=("_qfq", "_raw"))
    cmp_qb = rps_qfq.merge(rps_reb, on="ts_code", suffixes=("_qfq", "_reb"))
    cmp_qr = cmp_qr.dropna(subset=["RPS_120_qfq", "RPS_120_raw", "RPS_250_qfq", "RPS_250_raw"])
    cmp_qb = cmp_qb.dropna(subset=["RPS_120_qfq", "RPS_120_reb", "RPS_250_qfq", "RPS_250_reb"])

    def overlap(a: pd.Series, b: pd.Series, thr: float) -> dict:
        sa, sb = set(a[a >= thr].index), set(b[b >= thr].index)
        inter = sa & sb
        return {
            "qfq_n": len(sa), "other_n": len(sb),
            "overlap": len(inter),
            "jaccard": round(len(inter) / len(sa | sb), 4) if sa or sb else None,
        }

    qr120 = cmp_qr.set_index("ts_code")
    qb120 = cmp_qb.set_index("ts_code")

    # 除权股 ROC 扭曲：窗口内有事件的股票，未复权 ROC120 vs 前复权 ROC120
    look120 = work.sort_values(["ts_code", "trade_date"]).copy()
    look120["roc120_qfq"] = look120.groupby("ts_code")["close_qfq"].pct_change(120, fill_method=None)
    look120["roc120_raw"] = look120.groupby("ts_code")["close_tdx"].pct_change(120, fill_method=None)
    ev_asof = look120[(look120["trade_date"] == asof) & look120["ts_code"].isin(ev_codes)]
    roc_gap = (ev_asof["roc120_qfq"] - ev_asof["roc120_raw"]).abs()

    # 覆盖
    bars_qfq = qfq.groupby("ts_code").size()
    asof_qfq_n = int((qfq["trade_date"] == asof).sum())
    asof_tdx_n = int((tdx["trade_date"] == asof).sum())
    rps250_ready = int((bars_qfq >= 250).sum())

    # 金额对照（不应复权）
    amt = asof_m.dropna(subset=["amount_qfq", "amount_tdx"])
    amt_rel = ((amt["amount_qfq"] - amt["amount_tdx"]).abs() / amt["amount_tdx"].replace(0, np.nan))

    # 示例：除权后 ROC 差最大的几只
    ev_asof = ev_asof.dropna(subset=["roc120_qfq", "roc120_raw"]).copy()
    ev_asof["roc_gap"] = ev_asof["roc120_qfq"] - ev_asof["roc120_raw"]
    top_gap = ev_asof.nlargest(8, "roc_gap")[["ts_code", "roc120_qfq", "roc120_raw", "roc_gap"]]
    top_examples = []
    for _, row in top_gap.iterrows():
        top_examples.append({
            "ts_code": row["ts_code"],
            "roc120_qfq_pct": round(float(row["roc120_qfq"] * 100), 2),
            "roc120_raw_pct": round(float(row["roc120_raw"] * 100), 2),
            "gap_pp": round(float(row["roc_gap"] * 100), 2),
        })

    # 候选翻转：RPS_120>=80
    flip_in = sorted(set(qr120.index[qr120["RPS_120_qfq"] >= 80]) - set(qr120.index[qr120["RPS_120_raw"] >= 80]))
    flip_out = sorted(set(qr120.index[qr120["RPS_120_raw"] >= 80]) - set(qr120.index[qr120["RPS_120_qfq"] >= 80]))

    report = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": str(AS_OF),
        "formula": {
            "definition": "RPS_n = 当日横截面百分位( (close_t - close_{t-n}) / close_{t-n} ) × 100",
            "code": "rps_analyzer/rps_calculator.py::calculate_rps",
            "periods": [50, 120, 250],
            "requires": "等比前复权收盘价；成交额保持原始元口径",
            "current_pipeline": "run_integrated_decision.py 在 G0 通过后仍把通达信未复权 close 直接代入 calculate_rps",
        },
        "coverage": {
            "hithink_qfq_names_in_window": int(qfq["ts_code"].nunique()),
            "hithink_qfq_asof": asof_qfq_n,
            "tdx_asof": asof_tdx_n,
            "inner_join_asof": int(len(asof_m)),
            "names_with_250_qfq_bars": rps250_ready,
            "window": [str(LOOKBACK_START), str(AS_OF)],
            "latest_qfq_vs_tdx_abs_median": round(float(latest_abs.median()), 4) if len(latest_abs) else None,
            "latest_qfq_vs_tdx_abs_p95": round(float(latest_abs.quantile(0.95)), 4) if len(latest_abs) else None,
            "latest_hithink_raw_vs_tdx_abs_median": round(float(latest_hithink_raw.median()), 4) if len(latest_hithink_raw) else None,
            "amount_rel_median": round(float(amt_rel.median()), 6) if len(amt_rel) else None,
        },
        "convention_sample": {
            "n": len(details),
            "status": status_counts,
            "convention": conv_counts,
            "pass_rate": round(status_counts.get("verified_qfq", 0) / len(details), 4) if details else None,
            "details": details,
        },
        "rps_asof": {
            "n_qfq_vs_tdx_raw": int(len(cmp_qr)),
            "n_qfq_vs_tdx_rebuilt": int(len(cmp_qb)),
            "spearman": {
                "RPS_50_qfq_vs_raw": round(spearman(cmp_qr["RPS_50_qfq"], cmp_qr["RPS_50_raw"]), 4) if len(cmp_qr) else None,
                "RPS_120_qfq_vs_raw": round(spearman(cmp_qr["RPS_120_qfq"], cmp_qr["RPS_120_raw"]), 4) if len(cmp_qr) else None,
                "RPS_250_qfq_vs_raw": round(spearman(cmp_qr["RPS_250_qfq"], cmp_qr["RPS_250_raw"]), 4) if len(cmp_qr) else None,
                "RPS_50_qfq_vs_rebuilt": round(spearman(cmp_qb["RPS_50_qfq"], cmp_qb["RPS_50_reb"]), 4) if len(cmp_qb) else None,
                "RPS_120_qfq_vs_rebuilt": round(spearman(cmp_qb["RPS_120_qfq"], cmp_qb["RPS_120_reb"]), 4) if len(cmp_qb) else None,
                "RPS_250_qfq_vs_rebuilt": round(spearman(cmp_qb["RPS_250_qfq"], cmp_qb["RPS_250_reb"]), 4) if len(cmp_qb) else None,
            },
            "median_abs_rps_diff": {
                "RPS_120_qfq_minus_raw": round(float((cmp_qr["RPS_120_qfq"] - cmp_qr["RPS_120_raw"]).abs().median()), 2) if len(cmp_qr) else None,
                "RPS_250_qfq_minus_raw": round(float((cmp_qr["RPS_250_qfq"] - cmp_qr["RPS_250_raw"]).abs().median()), 2) if len(cmp_qr) else None,
                "RPS_120_qfq_minus_rebuilt": round(float((cmp_qb["RPS_120_qfq"] - cmp_qb["RPS_120_reb"]).abs().median()), 2) if len(cmp_qb) else None,
                "RPS_250_qfq_minus_rebuilt": round(float((cmp_qb["RPS_250_qfq"] - cmp_qb["RPS_250_reb"]).abs().median()), 2) if len(cmp_qb) else None,
            },
            "gate_RPS120_ge_80": {
                "qfq_vs_raw": overlap(qr120["RPS_120_qfq"], qr120["RPS_120_raw"], 80),
                "qfq_vs_rebuilt": overlap(qb120["RPS_120_qfq"], qb120["RPS_120_reb"], 80),
                "flip_in_from_raw_to_qfq_n": len(flip_in),
                "flip_out_from_raw_to_qfq_n": len(flip_out),
                "flip_in_sample": flip_in[:12],
                "flip_out_sample": flip_out[:12],
            },
        },
        "dividend_roc_distortion": {
            "names_with_events_in_window": int(len(ev_codes)),
            "asof_event_names_with_roc120": int(len(ev_asof)),
            "median_abs_roc120_gap_pp": round(float(roc_gap.median() * 100), 3) if len(roc_gap) else None,
            "p95_abs_roc120_gap_pp": round(float(roc_gap.quantile(0.95) * 100), 3) if len(roc_gap) else None,
            "largest_qfq_minus_raw": top_examples,
        },
        "verdict": {},
        "disclaimer": "非投资建议。验证的是计算口径与排序稳定性，不是荐股。",
    }

    # 裁决
    pass_rate = report["convention_sample"]["pass_rate"] or 0
    sp120_reb = report["rps_asof"]["spearman"]["RPS_120_qfq_vs_rebuilt"] or 0
    latest_med = report["coverage"]["latest_qfq_vs_tdx_abs_median"] or 99
    formula_ok = True
    convention_ok = pass_rate >= 0.80 and conv_counts.get("multiplicative_factor", 0) >= conv_counts.get("additive_dividend", 0)
    rps_ok = sp120_reb >= 0.98 and (report["rps_asof"]["median_abs_rps_diff"]["RPS_120_qfq_minus_rebuilt"] or 99) <= 1.5
    price_ok = latest_med <= 0.02
    usable = formula_ok and convention_ok and rps_ok and price_ok
    report["verdict"] = {
        "usable_for_local_rps_scheme": usable,
        "formula_compatible": formula_ok,
        "convention_is_multiplicative": convention_ok,
        "rps_matches_tdx_times_hithink_factor": rps_ok,
        "latest_price_aligned_with_tdx": price_ok,
        "gate_note": (
            "公式兼容，可用同花顺 v_daily_qfq.close 直接代入 calculate_rps；"
            "成交额继续用原始 amount。这不能自动打开 G0——"
            "G0 仍只认 output/qfq_verification.json。"
            if usable else
            "存在未通过项，见 coverage / convention_sample / rps_asof，不得把未复权通达信 RPS 与这套前复权 RPS 混排。"
        ),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "path": str(OUT_JSON),
        "usable": usable,
        "convention": conv_counts,
        "status": status_counts,
        "spearman_120_raw": report["rps_asof"]["spearman"]["RPS_120_qfq_vs_raw"],
        "spearman_120_reb": report["rps_asof"]["spearman"]["RPS_120_qfq_vs_rebuilt"],
        "latest_med": latest_med,
        "flip_in": len(flip_in),
        "flip_out": len(flip_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
