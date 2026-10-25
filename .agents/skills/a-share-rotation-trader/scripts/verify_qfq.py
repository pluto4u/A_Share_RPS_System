#!/usr/bin/env python3
"""复权闸门：默认使用本地通达信原始日线核验生产前复权链。

存在的原因：改造前 G0 的 qfq_verified 只来自 agent 自己写的 JSON，或命令行
`--qfq-status verified`，没有任何代码真的做过除权对账。全框架最强的护栏由
一个参数即可绕过。本脚本把它变成必须由数据通过的检查。

默认 ``--mode local`` 的运营门：
  1. 本地通达信 ``.day`` 抽样历史与生产库 ``v_daily`` 原始 OHLC 对齐；
  2. 全库核验 ``v_daily_qfq = v_daily × forward_factor``；
  3. 全库核验量额不变、因子为正、最新日因子归一到 1；
  4. 只有抽样原始锚和全库公式检查全部通过，才输出 ``verified_qfq``。

``--mode tencent`` 保留原有跨源诊断：
  1. 抽样若干只股票，取腾讯 qfqday 与本地 .day 的共同交易日；
  2. 逐日计算 factor = qfq_close / raw_close；
  3. 若某股在窗口内 factor 基本恒定（相对离散度极小），说明窗口内无除权，
     两套价格可互相换算；
  4. 若 factor 出现台阶，则定位除权日，并检查前复权序列在除权日前后
     是否消除了机械价差（未复权收益率异常、前复权收益率正常）；
  5. 最新交易日价格必须与实际行情一致。

本地运营门不是独立外部证明，报告会显式记录这一边界；腾讯诊断不再阻塞
用户指定的本地数据交易链。结论仍只有三种：verified_qfq / unadjusted / unknown。
未通过时 MA、ATR、RPS、跨日收益、回撤、形态筛选和回测必须停算，
不允许只降低置信度后继续计算。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import struct
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
RECORD_SIZE = 32
RECORD_DTYPE = np.dtype([
    ("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
    ("close", "<u4"), ("amount", "<f4"), ("vol", "<u4"), ("reserved", "<u4"),
])
PREFIX_SH = ("600", "601", "603", "605", "688", "689")
PREFIX_SZ = ("000", "001", "002", "003", "300", "301")

FACTOR_FLAT_TOL = 2e-3     # 同一复权段内 factor 的相对离散度上限
STEP_TOL = 1e-3            # 判定为除权台阶的 factor 相对跳变下限
PRICE_MATCH_TOL = 5e-3     # 最新交易日 factor 应归一到 1
MAX_STEPS_PER_250 = 8      # 每 250 个交易日允许的除权次数上限，超出视为噪声而非公司行为
RETURN_SIGN_TOL = 5e-4     # 除权日“前复权收益不低于未复权收益”的容差
MIN_COMMON_BARS = 60
MIN_PASS_RATE = 0.90       # 抽样通过率下限
LOCAL_MIN_PASS_RATE = 1.0  # 本地运营门要求抽样原始行情全部一致
LOCAL_PRICE_TOL = 0.011    # 通达信价格量化至 0.01 元


def find_project_root(script_path: Path) -> Path:
    for parent in script_path.resolve().parents:
        if (parent / "rps_analyzer").is_dir() and (parent / "data").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录")


ROOT = find_project_root(Path(__file__))
DEFAULT_HITHINK_DB = ROOT / "data" / "hithink" / "market.duckdb"


def read_local_tail(path: Path, n_bars: int) -> dict[int, float]:
    n_avail = path.stat().st_size // RECORD_SIZE
    take = min(n_bars, n_avail)
    if take < 5:
        return {}
    with path.open("rb") as fh:
        fh.seek((n_avail - take) * RECORD_SIZE)
        rec = np.frombuffer(fh.read(take * RECORD_SIZE), dtype=RECORD_DTYPE)
    return {int(d): float(c) / 100.0 for d, c in zip(rec["date"], rec["close"])}


def read_local_tail_frame(path: Path, n_bars: int) -> pd.DataFrame:
    """读取通达信原始 OHLC；量额不在此处跨源比较，避免单位口径混淆。"""
    n_avail = path.stat().st_size // RECORD_SIZE
    take = min(n_bars, n_avail)
    if take < 5:
        return pd.DataFrame()
    with path.open("rb") as fh:
        fh.seek((n_avail - take) * RECORD_SIZE)
        rec = np.frombuffer(fh.read(take * RECORD_SIZE), dtype=RECORD_DTYPE)
    return pd.DataFrame({
        "date": rec["date"].astype("int64"),
        "tdx_open": rec["open"].astype("float64") / 100.0,
        "tdx_high": rec["high"].astype("float64") / 100.0,
        "tdx_low": rec["low"].astype("float64") / 100.0,
        "tdx_close": rec["close"].astype("float64") / 100.0,
    })


def _hithink_cli() -> str:
    return "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"


def db_query(db_path: Path, sql: str) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [_hithink_cli(), "--db", str(db_path), "db", "query", "--sql", sql,
         "--format", "json"], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "同花顺本地库查询失败").strip())
    payload = json.loads(proc.stdout or "{}")
    if not payload.get("ok"):
        raise RuntimeError(f"同花顺本地库查询失败：{payload}")
    return payload.get("data") or []


def export_raw_sample(db_path: Path, codes: list[str], bars: int) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    quoted = ",".join("'" + code.replace("'", "''") + "'" for code in codes)
    sql = (
        "WITH ranked AS (SELECT r.thscode,r.date,r.open,r.high,r.low,r.close,"
        "f.forward_factor,ROW_NUMBER() OVER(PARTITION BY r.thscode ORDER BY r.date DESC) rn "
        "FROM v_daily r JOIN calc_adjust_factor_daily f USING(thscode,date) "
        f"WHERE r.thscode IN ({quoted})) "
        "SELECT thscode,date,open,high,low,close,forward_factor FROM ranked "
        f"WHERE rn<={int(bars)} ORDER BY thscode,date")
    with tempfile.TemporaryDirectory(prefix="local-qfq-gate-") as tmpdir:
        target = Path(tmpdir) / "raw_sample.parquet"
        proc = subprocess.run(
            [_hithink_cli(), "--db", str(db_path), "db", "export", "--sql", sql,
             "--output", str(target), "--file-format", "parquet", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "本地原始行情导出失败").strip())
        payload = json.loads(proc.stdout or "{}")
        if not payload.get("ok") or not target.exists():
            raise RuntimeError(f"本地原始行情导出失败：{payload}")
        return pd.read_parquet(target)


def fetch_tencent_qfq(symbol: str, bars: int, retries: int = 2) -> dict[int, float]:
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,,,{bars},qfq")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            if payload.get("code") != 0:
                return {}
            node = (payload.get("data") or {}).get(symbol) or {}
            rows = node.get("qfqday") or node.get("day") or []
            out: dict[int, float] = {}
            for row in rows:
                # row = [date, open, close, high, low, volume, ...]
                out[int(str(row[0]).replace("-", ""))] = float(row[2])
            return out
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, IndexError):
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return {}


def to_symbol(ts_code: str) -> str:
    code, market = ts_code.split(".")
    return ("sh" if market == "SH" else "sz") + code


def _piecewise_constant(series: np.ndarray, step_tol: float, flat_tol: float,
                        max_steps: int) -> tuple[list[int], float] | None:
    """检测序列是否分段恒定。返回 (台阶位置, 段内最大离散度) 或 None。

    离散度用绝对极差衡量，调用方按各自量纲传入容差。
    """
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


def check_one(ts_code: str, local: dict[int, float], remote: dict[int, float]) -> dict[str, Any]:
    common = sorted(set(local) & set(remote))
    if len(common) < MIN_COMMON_BARS:
        return {"ts_code": ts_code, "status": "unknown",
                "reason": f"共同交易日仅 {len(common)} 个，不足 {MIN_COMMON_BARS}"}

    raw = np.array([local[d] for d in common])
    qfq = np.array([remote[d] for d in common])
    if np.any(raw <= 0):
        return {"ts_code": ts_code, "status": "unknown", "reason": "本地价格含非正值"}

    latest_date = common[-1]
    result: dict[str, Any] = {
        "ts_code": ts_code,
        "common_bars": len(common),
        "window": [common[0], latest_date],
        "latest_close_local": round(float(raw[-1]), 3),
        "latest_close_qfq": round(float(qfq[-1]), 3),
    }

    # 容差随价格缩放：本地 .day 收盘价是整数除以 100（绝对精度 0.01），
    # 腾讯前复权价再有 0.001 的舍入。低价股的相对量化误差可达 0.7%，
    # 用固定相对容差会把量化噪声误判为未复权或虚假除权台阶。
    tick = 0.01 + 0.001
    median_price = float(np.median(raw))
    max_steps = max(2, int(MAX_STEPS_PER_250 * len(common) / 250))

    # 复权有两种实现约定，必须分别检验，不能只假设其中一种：
    #   等比（因子式）: qfq = raw × factor，检验 qfq/raw 分段恒定
    #   等差（减累计分红）: qfq = raw - cum_div，检验 qfq-raw 分段恒定
    # 腾讯 qfqday 实测为等差：某股连续多日 raw-qfq 恒为 0.32，
    # 派息日跳为 0.22，最终归零。若只按等比检验会整片误判为“未复权”。
    ratio = qfq / raw
    offset = qfq - raw
    mult = _piecewise_constant(ratio, step_tol=4.0 * tick / median_price,
                               flat_tol=3.0 * tick / median_price, max_steps=max_steps)
    addi = _piecewise_constant(offset, step_tol=4.0 * tick,
                               flat_tol=3.0 * tick, max_steps=max_steps)

    if addi is not None and (mult is None or len(addi[0]) <= len(mult[0])):
        convention, steps, worst = "additive_dividend", addi[0], addi[1]
        result["offset_first"] = round(float(offset[0]), 4)
        result["offset_latest"] = round(float(offset[-1]), 4)
        anchor_ok = abs(float(offset[-1])) <= tick
        anchor_desc = f"末值偏移 {offset[-1]:+.4f}"
    elif mult is not None:
        convention, steps, worst = "multiplicative_factor", mult[0], mult[1]
        result["ratio_first"] = round(float(ratio[0]), 6)
        result["ratio_latest"] = round(float(ratio[-1]), 6)
        anchor_ok = abs(float(ratio[-1]) - 1.0) <= PRICE_MATCH_TOL
        anchor_desc = f"末值比值 {ratio[-1]:.6f}"
    else:
        result.update({"status": "unadjusted", "convention": "none",
                       "reason": "qfq/raw 与 qfq-raw 均非分段恒定，两套序列无法互相换算"})
        return result

    result["convention"] = convention
    result["worst_segment_spread"] = round(worst, 6)
    result["n_steps"] = len(steps)

    # 前复权以最新交易日为基准归一：等比末值应为 1，等差末值偏移应为 0
    if not anchor_ok:
        result.update({"status": "unadjusted",
                       "reason": f"{anchor_desc}，未以最新交易日归一，可能错位或非同一标的"})
        return result

    if not steps:
        result.update({"status": "verified_qfq", "corporate_action": "none_in_window",
                       "reason": "窗口内无除权，本地原始价即前复权价"})
        return result

    # 从台阶反解每次除权，并重建等比因子。
    # 等差复权会扭曲百分比收益率，不能直接用于 RPS 与跨日收益；
    # 必须换算成等比因子后再算价格类指标。
    actions = []
    for idx in steps:
        prev_raw = float(raw[idx - 1])
        if convention == "additive_dividend":
            dividend = float(offset[idx] - offset[idx - 1])
            factor = (prev_raw - dividend) / prev_raw if prev_raw else np.nan
        else:
            dividend = float(prev_raw * (1.0 - ratio[idx - 1] / ratio[idx]))
            factor = float(ratio[idx - 1] / ratio[idx])
        raw_ret = float(raw[idx] / prev_raw - 1.0)
        qfq_ret = float(qfq[idx] / qfq[idx - 1] - 1.0)
        actions.append({
            "ex_date": common[idx],
            "dividend_per_share": round(dividend, 4),
            "multiplicative_factor": round(factor, 6),
            "raw_return": round(raw_ret, 4),
            "qfq_return": round(qfq_ret, 4),
            # 除权抹掉的是向下的机械价差，故前复权收益不应低于未复权收益
            "ok": qfq_ret >= raw_ret - RETURN_SIGN_TOL and 0.5 < factor <= 1.0 + RETURN_SIGN_TOL,
        })
    result["corporate_action"] = "detected"
    result["actions"] = actions

    bad = [a for a in actions if not a["ok"]]
    if bad:
        result.update({"status": "unadjusted",
                       "reason": (f"除权日 {bad[0]['ex_date']} 对账不符："
                                  f"未复权 {bad[0]['raw_return']:.2%} / "
                                  f"前复权 {bad[0]['qfq_return']:.2%} / "
                                  f"因子 {bad[0]['multiplicative_factor']:.4f}")})
        return result

    first = actions[0]
    conv_cn = "等差(减累计分红)" if convention == "additive_dividend" else "等比(因子式)"
    result.update({"status": "verified_qfq",
                   "reason": (f"{conv_cn}分段恒定，{len(actions)} 次除权对账通过"
                              f"（首个 {first['ex_date']} 派息 {first['dividend_per_share']:.4f}，"
                              f"等比因子 {first['multiplicative_factor']:.4f}）")})
    return result


def pick_universe(tdx_root: Path, sample: int, min_bars: int, seed: int) -> list[tuple[str, Path]]:
    pool: list[tuple[str, Path]] = []
    for market, prefixes in (("sh", PREFIX_SH), ("sz", PREFIX_SZ)):
        lday = tdx_root / market / "lday"
        if not lday.exists():
            continue
        for path in lday.glob("*.day"):
            if path.name[2:5] not in prefixes:
                continue
            if path.stat().st_size // RECORD_SIZE < min_bars:
                continue
            pool.append((f"{path.name[2:8]}.{market.upper()}", path))
    random.Random(seed).shuffle(pool)
    return pool[:sample]


def run_tencent_gate(args: argparse.Namespace) -> int:
    picks = pick_universe(Path(args.tdx_dir), args.sample, args.bars, args.seed)
    if not picks:
        print("[复权闸门] 本地无可用样本，结论 unknown")
        picks = []

    print(f"[复权闸门] 抽样 {len(picks)} 只，窗口 {args.bars} 个交易日")
    details: list[dict[str, Any]] = []
    for ts_code, path in picks:
        local = read_local_tail(path, args.bars)
        remote = fetch_tencent_qfq(to_symbol(ts_code), args.bars + 40)
        if not remote:
            details.append({"ts_code": ts_code, "status": "unknown",
                            "reason": "腾讯前复权接口未返回可用数据"})
        else:
            details.append(check_one(ts_code, local, remote))
        d = details[-1]
        print(f"  {ts_code}  {d['status']:13s} {d.get('reason', '')}")
        time.sleep(0.3)

    n = len(details)
    verified = sum(1 for d in details if d["status"] == "verified_qfq")
    unadjusted = sum(1 for d in details if d["status"] == "unadjusted")
    unknown = n - verified - unadjusted
    with_action = sum(1 for d in details if d.get("corporate_action") == "detected")
    pass_rate = verified / n if n else 0.0

    conventions: dict[str, int] = {}
    for d in details:
        conv = d.get("convention")
        if conv:
            conventions[conv] = conventions.get(conv, 0) + 1

    # 本地 .day 是未复权的：只有在腾讯前复权可换算、且除权日对账通过时，
    # 才允许把“本地原始价 + 腾讯前复权因子”这条链路标记为 verified_qfq。
    if n == 0 or unknown == n:
        status, blocker = "unknown", "无有效样本或腾讯前复权接口不可用"
    elif pass_rate >= MIN_PASS_RATE and unadjusted == 0:
        status, blocker = "verified_qfq", None
    else:
        status = "unadjusted"
        blocker = (f"抽样通过率 {pass_rate:.0%} 低于 {MIN_PASS_RATE:.0%} "
                   f"或存在 {unadjusted} 只对账失败")

    report = {
        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price_adjustment": status,
        "adjustment_source": "腾讯 web.ifzq.gtimg.cn/appstock/app/fqkline/get (qfqday)",
        "local_source": f"{args.tdx_dir}/{{sh,sz}}/lday/*.day (原始未复权)",
        "verification_method": ("双约定分段恒定性检验（等比 qfq/raw、等差 qfq-raw）"
                                "+ 最新交易日归一 + 逐除权日方向与因子区间对账，容差按价格量化缩放"),
        "adjustment_convention": conventions,
        "convention_note": ("腾讯 qfqday 实测为等差复权（原价减累计分红）。"
                            "等差序列会扭曲百分比收益率，不能直接用于 RPS、跨日收益与回撤；"
                            "须使用 details[].actions 中反解出的 multiplicative_factor "
                            "重建等比前复权序列后再计算价格类指标。"),
        "window_bars": args.bars,
        "sample_size": n,
        "verified": verified,
        "unadjusted": unadjusted,
        "unknown": unknown,
        "samples_with_corporate_action": with_action,
        "pass_rate": round(pass_rate, 3),
        "min_pass_rate": MIN_PASS_RATE,
        "blocker": blocker,
        "gated_metrics": ["MA", "ATR", "RPS", "跨日收益", "回撤", "形态筛选", "回测", "日线CER"],
        "details": details,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[复权闸门] 结论: {status}  "
          f"(verified {verified} / unadjusted {unadjusted} / unknown {unknown}，"
          f"含除权样本 {with_action} 只)")
    if blocker:
        print(f"[复权闸门] 阻塞原因: {blocker}")
        print("[复权闸门] MA/ATR/RPS/跨日收益/回撤/回测 必须停算")
    print(f"[复权闸门] 报告: {out}")
    return 0 if status == "verified_qfq" else 1


def _as_int(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    return int(value) if value is not None else 0


def run_local_gate(args: argparse.Namespace) -> int:
    """本地运营门：TDX 原始价锚 + 全库因子公式检查。"""
    tdx_root = Path(args.tdx_dir)
    db_path = Path(args.hithink_db).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"缺少同花顺本地生产库：{db_path}")

    picks = pick_universe(tdx_root, args.sample, args.bars, args.seed)
    if not picks:
        raise RuntimeError(f"本地通达信没有可用样本：{tdx_root}")
    codes = [code for code, _ in picks]
    raw_db = export_raw_sample(db_path, codes, args.bars)
    if raw_db.empty:
        raise RuntimeError("生产库没有返回抽样原始行情")
    raw_db["date"] = pd.to_datetime(raw_db["date"]).dt.strftime("%Y%m%d").astype("int64")

    details: list[dict[str, Any]] = []
    for ts_code, path in picks:
        tdx = read_local_tail_frame(path, args.bars)
        db_part = raw_db.loc[raw_db["thscode"] == ts_code].copy()
        merged = tdx.merge(db_part, on="date", how="inner")
        if len(merged) < MIN_COMMON_BARS:
            details.append({
                "ts_code": ts_code, "status": "unknown", "common_bars": int(len(merged)),
                "reason": f"通达信与生产库共同交易日仅 {len(merged)} 个",
            })
            continue
        diffs = []
        mismatch = np.zeros(len(merged), dtype=bool)
        for field in ("open", "high", "low", "close"):
            diff = (merged[f"tdx_{field}"] - merged[field]).abs()
            diffs.append(float(diff.max()))
            mismatch |= diff.to_numpy() > LOCAL_PRICE_TOL
        latest_tdx = int(tdx["date"].max())
        latest_db = int(db_part["date"].max())
        status = "verified_raw_anchor" if not mismatch.any() and latest_tdx == latest_db else "unadjusted"
        details.append({
            "ts_code": ts_code,
            "status": status,
            "common_bars": int(len(merged)),
            "window": [int(merged["date"].min()), int(merged["date"].max())],
            "latest_date_tdx": latest_tdx,
            "latest_date_db": latest_db,
            "max_abs_ohlc_diff": round(max(diffs), 6),
            "mismatch_rows": int(mismatch.sum()),
            "has_adjustment_in_window": bool((db_part["forward_factor"] < 1.0 - 1e-10).any()),
            "reason": ("通达信原始OHLC与生产库原始视图一致"
                       if status == "verified_raw_anchor" else
                       "通达信原始OHLC与生产库原始视图存在错配或最新日不同"),
        })

    formula = db_query(db_path,
        "SELECT COUNT(*) AS joined_rows, "
        "SUM(CASE WHEN ABS(q.open-r.open*f.forward_factor)>0.00000001 OR "
        "ABS(q.high-r.high*f.forward_factor)>0.00000001 OR "
        "ABS(q.low-r.low*f.forward_factor)>0.00000001 OR "
        "ABS(q.close-r.close*f.forward_factor)>0.00000001 THEN 1 ELSE 0 END) formula_mismatch, "
        "SUM(CASE WHEN ABS(q.volume-r.volume)>0.000001 THEN 1 ELSE 0 END) volume_mismatch, "
        "SUM(CASE WHEN ABS(q.amount-r.amount)>0.01 THEN 1 ELSE 0 END) amount_mismatch, "
        "SUM(CASE WHEN ABS(q.close-r.close)>0.000001 THEN 1 ELSE 0 END) adjusted_rows "
        "FROM v_daily_qfq q JOIN v_daily r USING(thscode,date) "
        "JOIN calc_adjust_factor_daily f USING(thscode,date)")[0]
    anchor = db_query(db_path,
        "WITH latest AS (SELECT thscode,MAX(date) date FROM v_daily_qfq GROUP BY thscode) "
        "SELECT COUNT(*) symbols, "
        "SUM(CASE WHEN ABS(q.close-r.close)>0.000001 OR "
        "ABS(f.forward_factor-1)>0.00000001 THEN 1 ELSE 0 END) anchor_mismatch, "
        "MAX(q.date) max_date FROM latest l JOIN v_daily_qfq q USING(thscode,date) "
        "JOIN v_daily r USING(thscode,date) JOIN calc_adjust_factor_daily f USING(thscode,date)")[0]
    factor = db_query(db_path,
        "SELECT MIN(forward_factor) min_factor, MAX(forward_factor) max_factor, "
        "SUM(CASE WHEN forward_factor<=0 OR forward_factor IS NULL THEN 1 ELSE 0 END) invalid_factor "
        "FROM calc_adjust_factor_daily")[0]

    verified = sum(d["status"] == "verified_raw_anchor" for d in details)
    unknown = sum(d["status"] == "unknown" for d in details)
    failed = len(details) - verified - unknown
    pass_rate = verified / len(details) if details else 0.0
    full_ok = (
        _as_int(formula, "joined_rows") > 0
        and _as_int(formula, "formula_mismatch") == 0
        and _as_int(formula, "volume_mismatch") == 0
        and _as_int(formula, "amount_mismatch") == 0
        and _as_int(formula, "adjusted_rows") > 0
        and _as_int(anchor, "anchor_mismatch") == 0
        and _as_int(factor, "invalid_factor") == 0
    )
    passed = pass_rate >= LOCAL_MIN_PASS_RATE and failed == 0 and unknown == 0 and full_ok
    status = "verified_qfq" if passed else "unadjusted"
    blocker = None if passed else (
        f"本地原始锚通过 {verified}/{len(details)}，失败 {failed}，未知 {unknown}；"
        f"全库公式检查={'通过' if full_ok else '失败'}")
    report = {
        "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "price_adjustment": status,
        "gate_type": "local_operational_consistency",
        "verification_scope": "本地运营一致性验证（非独立外部复权证明）",
        "independent_external_check": "not_required_by_local_mode",
        "adjustment_source": f"{db_path}:v_daily_qfq + calc_adjust_factor_daily",
        "local_source": f"{args.tdx_dir}/{{sh,sz}}/lday/*.day（原始未复权锚）",
        "verification_method": (
            "通达信原始OHLC抽样历史对齐 + 全库qfq=raw×forward_factor + "
            "全库量额不变 + 最新日因子归一"),
        "adjustment_convention": {"multiplicative_factor": _as_int(formula, "adjusted_rows")},
        "convention_note": (
            "生产价格为本地公司行动重建的等比前复权；通达信.day仅作为原始行情锚。"
            "本门验证本地链路自洽，不等同于腾讯等外部源的独立证明。"),
        "window_bars": args.bars,
        "sample_size": len(details),
        "verified": verified,
        "unadjusted": failed,
        "unknown": unknown,
        "samples_with_corporate_action": sum(bool(d.get("has_adjustment_in_window")) for d in details),
        "pass_rate": round(pass_rate, 3),
        "min_pass_rate": LOCAL_MIN_PASS_RATE,
        "blocker": blocker,
        "full_market_checks": {
            "formula": formula, "latest_anchor": anchor, "factor": factor,
        },
        "gated_metrics": ["MA", "ATR", "RPS", "跨日收益", "回撤", "形态筛选", "回测", "日线CER"],
        "details": details,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"[本地复权门] 结论: {status}；TDX原始锚 {verified}/{len(details)}；全库公式 {'通过' if full_ok else '失败'}")
    print(f"[本地复权门] 报告: {out}")
    if blocker:
        print(f"[本地复权门] 阻塞原因: {blocker}")
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("local", "tencent"), default="local",
                    help="local=本地通达信运营门（默认）；tencent=跨源诊断")
    ap.add_argument("--tdx-dir", default=r"C:\new_tdx\vipdoc")
    ap.add_argument("--hithink-db", default=str(DEFAULT_HITHINK_DB))
    ap.add_argument("--sample", type=int, default=64, help="抽样股票数")
    ap.add_argument("--bars", type=int, default=250, help="对账窗口交易日数")
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--output", help="默认：local写正式门报告；tencent写非阻塞诊断报告")
    args = ap.parse_args()
    if not args.output:
        args.output = ("output/qfq_verification.json" if args.mode == "local"
                       else "output/qfq_tencent_diagnostic.json")
    return run_local_gate(args) if args.mode == "local" else run_tencent_gate(args)


if __name__ == "__main__":
    raise SystemExit(main())
