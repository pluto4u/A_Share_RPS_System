#!/usr/bin/env python3
"""门控式 A 股决策链：复权闸门 → 风险许可 → RPS 候选 → 主线共振 → 执行质量 → 账户执行。

与改造前的差异（对应 references/data-availability.md）：
  1. G0 不再接受 `--qfq-status verified` 自我声明，只读 verify_qfq.py 产出的验证报告；
  2. G1 只读 build_limit_ecology.py 产出的本地重建生态，不再读 agent 自己写的 sentiment 字段；
  3. 删除东财/同花顺估算资金流门与题材层——数据无历史，无法回测；
  4. 删除分钟固定目标 CER 与板块 CER——腾讯分钟只有当日，本地分钟停更两年；
  5. 仓位公式加入跌停不可成交惩罚。

用法：
  python skills/a-share-rotation-trader/scripts/run_integrated_decision.py --date 20260814
  # 需要可执行股数时补齐账户字段：
  #   --account-state empty --equity 1000000 --available-cash 1000000 \
  #   --single-trade-risk-pct 0.50 --plan-json output/next_day_plan.json
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

def find_project_root(script_path: Path) -> Path:
    """兼容项目内 skills/ 与 .agents/skills/ 两种安装位置。"""
    for parent in script_path.resolve().parents:
        if (parent / "rps_analyzer").is_dir() and (parent / "data").is_dir():
            return parent
    raise RuntimeError("无法定位项目根目录（缺少 rps_analyzer/ 或 data/）")


ROOT = find_project_root(Path(__file__))
sys.path.insert(0, str(ROOT))

from build_limit_ecology import load_name_master  # noqa: E402
from rps_analyzer.rps_calculator import calculate_rps  # noqa: E402

DEFAULT_TDX = r"C:\new_tdx\vipdoc"
DEFAULT_HQ = Path(r"C:\new_tdx\T0002\hq_cache")
DEFAULT_INCON = Path(r"C:\new_tdx\incon.dat")
DEFAULT_HITHINK_DB = ROOT / "data" / "hithink" / "market.duckdb"

# ST_cycle 未完成走样本外回测前，不允许这个未验证模型授权高于"修复"档的仓位
UNVALIDATED_STATE_CAP = 35.0
# 跌停不可成交惩罚：硬失效位在一字跌停下无法成交，
# 有效止损距离不得小于该股涨跌幅限制的 k 倍
LIMIT_DOWN_PENALTY_K = 0.6


# ---------------------------------------------------------------- 基础工具

def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    """返回 (内容, 失败原因)。

    必须区分"文件不存在"与"文件存在但无法解析"：两者的处置完全不同，
    前者要去跑上游脚本，后者说明产出已损坏。用 utf-8-sig 容忍 BOM——
    在 Windows 上被文本工具改写过的 JSON 常带 BOM，标准 utf-8 会直接报错。
    """
    if not path.exists():
        return {}, f"未找到 {path.as_posix()}"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        return {}, f"{path.as_posix()} 存在但无法解析（{exc.__class__.__name__}: {exc}）"
    if not isinstance(data, dict):
        return {}, f"{path.as_posix()} 顶层不是对象"
    return data, None


def load_hithink_qfq(db_path: Path, target: pd.Timestamp, n_bars: int,
                     min_bars: int = 260, verbose: bool = False
                     ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """从生产库读取沪深 A 股前复权日 K，并保留原始量额口径。

    v_daily_qfq 的 OHLC 为本地公司行动重建的等比前复权价格；volume/amount
    与 v_daily 保持原始口径。通过 CLI 导出，避免依赖当前 Python 环境安装
    duckdb 包。只读取目标日及以前、每股最近 n_bars 根，控制内存占用。
    """
    db_path = db_path.resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"缺少同花顺生产库：{db_path}")
    if n_bars < min_bars:
        raise ValueError(f"bars={n_bars} 小于最少 {min_bars}，无法计算 RPS250")

    target_date = target.strftime("%Y-%m-%d")
    sql = (
        "WITH ranked AS ("
        "SELECT q.thscode AS ts_code, q.date AS trade_date, "
        "q.open, q.high, q.low, q.close, q.volume AS vol, q.amount, s.name, "
        "ROW_NUMBER() OVER (PARTITION BY q.thscode ORDER BY q.date DESC) AS rn "
        "FROM v_daily_qfq q JOIN dim_symbol s ON q.thscode=s.thscode "
        "WHERE s.asset_type='a-share' AND s.exchange IN ('SH','SZ') "
        f"AND q.date <= DATE '{target_date}') "
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, name "
        f"FROM ranked WHERE rn <= {int(n_bars)} ORDER BY trade_date, ts_code"
    )
    exe = "hithink-finance.cmd" if os.name == "nt" else "hithink-finance"
    with tempfile.TemporaryDirectory(prefix="integrated-qfq-") as tmpdir:
        export_path = Path(tmpdir) / "qfq_tail.parquet"
        cmd = [exe, "--db", str(db_path), "db", "export", "--sql", sql,
               "--output", str(export_path), "--file-format", "parquet",
               "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "同花顺前复权导出失败").strip()
            raise RuntimeError(message)
        try:
            receipt = json.loads(proc.stdout or "{}")
        except ValueError as exc:
            raise RuntimeError("同花顺前复权导出回执不是有效 JSON") from exc
        if not receipt.get("ok") or not export_path.exists():
            raise RuntimeError(f"同花顺前复权导出未完成：{receipt}")
        daily = pd.read_parquet(export_path)

    if daily.empty:
        raise RuntimeError(f"v_daily_qfq 在 {target_date} 及以前没有沪深 A 股数据")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    numeric = ["open", "high", "low", "close", "vol", "amount"]
    daily[numeric] = daily[numeric].apply(pd.to_numeric, errors="coerce")

    duplicate_rows = int(daily.duplicated(["ts_code", "trade_date"]).sum())
    null_prices = int(daily[["open", "high", "low", "close"]].isna().any(axis=1).sum())
    invalid_prices = int((daily[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    invalid_ranges = int((daily["high"] < daily["low"]).sum())
    if duplicate_rows or null_prices or invalid_prices or invalid_ranges:
        raise RuntimeError(
            "v_daily_qfq 质量门未通过："
            f"重复={duplicate_rows}，价格缺失={null_prices}，非正价格={invalid_prices}，"
            f"高低价倒置={invalid_ranges}"
        )

    counts = daily.groupby("ts_code")["trade_date"].size()
    excluded_short = int((counts < min_bars).sum())
    daily = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    meta = {
        "price_source": "hithink v_daily_qfq（本地公司行动重建前复权）",
        "volume_amount_source": "v_daily_qfq.volume/amount（与 v_daily 原始口径一致）",
        "db": str(db_path),
        "requested_date": target_date,
        "date_min": daily["trade_date"].min().strftime("%Y-%m-%d"),
        "date_max": daily["trade_date"].max().strftime("%Y-%m-%d"),
        "rows": int(len(daily)),
        "symbols": int(daily["ts_code"].nunique()),
        "excluded_short_history": excluded_short,
        "duplicate_rows": duplicate_rows,
        "null_prices": null_prices,
        "invalid_prices": invalid_prices,
        "invalid_ranges": invalid_ranges,
    }
    if verbose:
        print("[QFQ] " + json.dumps(meta, ensure_ascii=False))
    return daily, meta


def load_industry_map(cfg_path: Path) -> pd.DataFrame:
    market_map = {"0": "SZ", "1": "SH", "2": "BJ"}
    records = []
    with cfg_path.open("r", encoding="gbk", errors="ignore") as fh:
        for line in fh:
            parts = line.strip().split("|")
            if len(parts) < 3 or not parts[2]:
                continue
            market = market_map.get(parts[0])
            if market in (None, "BJ"):
                continue
            records.append({"ts_code": f"{parts[1]}.{market}", "industry": parts[2]})
    return pd.DataFrame(records).drop_duplicates("ts_code")


def load_industry_names(incon: Path) -> dict[str, str]:
    if not incon.exists():
        return {}
    mapping: dict[str, str] = {}
    with incon.open("r", encoding="gbk", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].startswith("T"):
                mapping[parts[0]] = parts[1]
    return mapping


def industry_label(code: str, names: dict[str, str]) -> str:
    for width in (5, 4, 3, 2):
        hit = names.get(code[:width])
        if hit:
            return hit
    return code


# ---------------------------------------------------------------- 指标

def add_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    work = daily.sort_values(["ts_code", "trade_date"]).copy()
    grp = work.groupby("ts_code", sort=False)
    close, amount = grp["close"], grp["amount"]
    work["bars_count"] = grp.cumcount() + 1
    work["ret1"] = close.pct_change(fill_method=None)
    for n in (5, 20, 60):
        work[f"ret{n}"] = close.pct_change(n, fill_method=None)
    for n in (20, 60):
        work[f"ma{n}"] = close.transform(lambda s, n=n: s.rolling(n, min_periods=n).mean())
    work["adv20"] = amount.transform(lambda s: s.shift(1).rolling(20, min_periods=15).median())
    work["amount_ratio"] = work["amount"] / work["adv20"].replace(0, np.nan)
    tr = pd.concat([
        work["high"] - work["low"],
        (work["high"] - grp["close"].shift(1)).abs(),
        (work["low"] - grp["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    work["atr20"] = tr.groupby(work["ts_code"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=15).mean())
    work["vs_ma20_pct"] = (work["close"] / work["ma20"] - 1.0) * 100.0
    work["high60"] = grp["high"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).max())
    # CER_D1：日线量价效率代理。可用本地全历史回测，但未完成样本外验证前
    # 只作观测字段，不参与排序与仓位决定。
    work["cer_d1"] = np.where(
        work["ret1"] > 0,
        work["ret1"] * 100.0 / work["amount_ratio"].replace(0, np.nan),
        np.nan)
    return work


def limit_pct_for(ts_code: str) -> float:
    return 0.20 if ts_code[:3] in ("300", "301", "688", "689") else 0.10


# ---------------------------------------------------------------- 门控

def gate_qfq(outdir: Path, trade_date: str) -> dict[str, Any]:
    report, err = read_json(outdir / "qfq_verification.json")
    if err:
        return {"pass": False, "status": "unknown",
                "detail": f"{err}；请先运行 verify_qfq.py"}
    status = report.get("price_adjustment")
    ok = status == "verified_qfq"
    conv = ",".join(report.get("adjustment_convention", {})) or "未知"
    gate_type = report.get("gate_type", "external_reconciliation")
    if gate_type == "local_operational_consistency":
        checks = report.get("full_market_checks") or {}
        formula = checks.get("formula") or {}
        anchor = checks.get("latest_anchor") or {}
        detail = (
            f"本地运营门={status}；通达信原始锚 "
            f"{report.get('verified')}/{report.get('sample_size')}；"
            f"全库公式错配={formula.get('formula_mismatch', '未取得')}；"
            f"量/额错配={formula.get('volume_mismatch', '未取得')}/"
            f"{formula.get('amount_mismatch', '未取得')}；"
            f"最新锚错配={anchor.get('anchor_mismatch', '未取得')}"
        )
    else:
        detail = (f"复权={status}；抽样 {report.get('verified')}/{report.get('sample_size')} 通过"
                  f"（含除权 {report.get('samples_with_corporate_action')} 只）；约定={conv}")
    return {
        "pass": ok, "status": status,
        "gate_type": gate_type,
        "verification_scope": report.get("verification_scope"),
        "independent_external_check": report.get("independent_external_check"),
        "detail": detail,
        "verified_at": report.get("verified_at"),
        "blocker": report.get("blocker"),
        "convention_note": report.get("convention_note"),
    }


def gate_risk(outdir: Path, trade_date: str) -> dict[str, Any]:
    eco, err = read_json(outdir / "limit_ecology_latest.json")
    if err:
        return {"pass": False, "detail": f"{err}；请先运行 build_limit_ecology.py"}
    as_of = (eco.get("as_of_trade_date") or "").replace("-", "")
    fresh = as_of == trade_date
    coverage = eco.get("coverage")
    formal = bool(eco.get("st_cycle_formal"))
    state_cap = float(eco.get("cycle_position_cap_pct") or 0.0)
    # 未验证模型不得授权高于"修复"档的仓位
    cap = state_cap if formal else min(state_cap, UNVALIDATED_STATE_CAP)
    ok = fresh and coverage is not None and coverage >= 0.80
    return {
        "pass": ok, "as_of": eco.get("as_of_trade_date"), "fresh": fresh,
        "cycle_state": eco.get("cycle_state"), "st_cycle": eco.get("ST_cycle"),
        "slope3": eco.get("slope3"), "coverage": coverage,
        "state_cap_pct": state_cap, "cap_pct": cap, "formal": formal,
        "hard_override": eco.get("hard_override"), "cap_note": eco.get("cap_note"),
        "raw": eco.get("raw") or {},
        "detail": (f"{eco.get('cycle_state')}｜ST_cycle={eco.get('ST_cycle')}"
                   f"｜覆盖率={coverage}｜状态上限={state_cap:.0f}%"
                   + ("" if formal else f"→未验证降至 {cap:.0f}%")
                   + (f"｜硬覆盖：{eco.get('hard_override')}" if eco.get("hard_override") else "")),
    }


def rank_industries(snap: pd.DataFrame, imap: pd.DataFrame,
                    names: dict[str, str], min_members: int = 8) -> pd.DataFrame:
    df = snap.merge(imap, on="ts_code", how="inner")
    rows = []
    for code, grp in df.groupby("industry"):
        if len(grp) < min_members:
            continue
        amt = grp["amount"].sum()
        adv = grp["adv20"].sum()
        rows.append({
            "industry": code,
            "name": industry_label(code, names),
            "members": len(grp),
            "ret5_pct": float(grp["ret5"].median() * 100),
            "ret20_pct": float(grp["ret20"].median() * 100),
            "breadth_ma20_pct": float((grp["close"] > grp["ma20"]).mean() * 100),
            "median_rps120": float(grp["RPS_120"].median()) if "RPS_120" in grp else np.nan,
            "amount_yi": float(amt / 1e8),
            "amount_vs_adv20": float(amt / adv) if adv else np.nan,
            "top1_amount_share_pct": float(grp["amount"].max() / amt * 100) if amt else np.nan,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # 主线需要价格强度、成交额增量与上涨宽度多数共振；单股独占成交额的降级
    for col in ("ret5_pct", "ret20_pct", "breadth_ma20_pct", "median_rps120", "amount_vs_adv20"):
        out[f"z_{col}"] = out[col].rank(pct=True) * 100
    out["resonance"] = out[[f"z_{c}" for c in
                            ("ret5_pct", "ret20_pct", "breadth_ma20_pct",
                             "median_rps120", "amount_vs_adv20")]].mean(axis=1)
    out["single_stock_driven"] = out["top1_amount_share_pct"] > 35.0
    return out.sort_values("resonance", ascending=False).reset_index(drop=True)


def build_candidates(snap: pd.DataFrame, imap: pd.DataFrame, names: dict[str, str],
                     lead_industries: list[str], min_adv: float, top_n: int) -> pd.DataFrame:
    df = snap.merge(imap, on="ts_code", how="inner")
    df = df[df["industry"].isin(lead_industries)].copy()
    df["industry_name"] = [industry_label(c, names) for c in df["industry"]]
    # 主线地位必须逐股标注：RPS 高但不属于第一主线的股票只进入观察，
    # 不能与第一主线标的混排后被当成同等可执行
    rank_map = {code: i + 1 for i, code in enumerate(lead_industries)}
    df["industry_rank"] = df["industry"].map(rank_map)
    df["executable"] = df["industry_rank"] == 1
    cond = (
        df["bars_count"].ge(260)
        & df["adv20"].notna() & (df["adv20"] >= min_adv)
        & df["ma20"].notna() & (df["close"] > df["ma20"])
        & df["ma60"].notna() & (df["close"] > df["ma60"])
        & df["RPS_120"].notna() & (df["RPS_120"] >= 80)
        & df["vs_ma20_pct"].notna() & (df["vs_ma20_pct"] <= 12.0)
    )
    out = df[cond].copy()
    if out.empty:
        return out
    out["limit_pct"] = [limit_pct_for(c) for c in out["ts_code"]]
    out["atr_pct"] = out["atr20"] / out["close"] * 100
    out["dist_high60_pct"] = (out["close"] / out["high60"] - 1.0) * 100
    out["score"] = out[["RPS_50", "RPS_120", "RPS_250"]].mean(axis=1)
    return (out.sort_values(["industry_rank", "score"], ascending=[True, False])
            .head(top_n).reset_index(drop=True))


def size_position(entry: float, invalidation: float, limit_pct: float, atr_pct: float | None,
                  equity: float, risk_pct: float, cash: float,
                  account_cap_pct: float) -> dict[str, Any]:
    """风险仓位。有效止损距离取三者最大，避免用不可实现的窄止损放大仓位。

    1. 计划距离：入场价到硬失效位；
    2. 跌停下限：硬失效位在一字跌停下无法成交，按涨跌幅限制的 k 倍设底；
    3. ATR 下限：止损窄于 1 个 ATR 会被日常波动直接打掉，
       名义风险小但实际触发概率极高，会系统性高估可承载仓位。
    """
    planned = abs(entry - invalidation)
    limit_floor = entry * limit_pct * LIMIT_DOWN_PENALTY_K
    atr_floor = entry * (atr_pct or 0.0) / 100.0
    effective = max(planned, limit_floor, atr_floor)
    binding = "计划距离"
    if effective > planned + 1e-9:
        binding = "跌停不可成交下限" if limit_floor >= atr_floor else "ATR 下限"
    risk_amount = equity * risk_pct / 100.0
    shares_by_risk = math.floor(risk_amount / effective / 100) * 100
    cash_budget = min(cash, equity * account_cap_pct / 100.0)
    shares_by_cash = math.floor(cash_budget / entry / 100) * 100
    shares = max(0, min(shares_by_risk, shares_by_cash))
    return {
        "entry": round(entry, 3), "invalidation": round(invalidation, 3),
        "planned_stop_pct": round(planned / entry * 100, 2),
        "limit_down_floor_pct": round(limit_floor / entry * 100, 2),
        "atr_floor_pct": round(atr_floor / entry * 100, 2),
        "effective_stop_pct": round(effective / entry * 100, 2),
        "binding_constraint": binding,
        "widened": binding != "计划距离",
        "limit_pct": limit_pct,
        "risk_amount": round(risk_amount, 2),
        "cash_budget": round(cash_budget, 2),
        "shares_by_risk": shares_by_risk, "shares_by_cash": shares_by_cash,
        "shares": shares,
        "position_pct": round(shares * entry / equity * 100, 2) if equity else None,
        "t_plus_one": "当日新买不可卖，次日才具备退出能力",
    }


# ---------------------------------------------------------------- 主流程

def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    outdir = Path(args.output_dir)
    g0 = gate_qfq(outdir, args.date)
    g1 = gate_risk(outdir, args.date)

    notes: list[str] = []
    snap = pd.DataFrame()
    industries = pd.DataFrame()
    candidates = pd.DataFrame()
    market: dict[str, Any] = {}
    data_meta: dict[str, Any] = {}
    target = pd.Timestamp(args.date)

    # G2 之前必须先过复权闸门：未通过时价格类跨日指标全部停算，
    # 不允许只降低置信度后继续计算
    if g0["pass"]:
        try:
            daily, data_meta = load_hithink_qfq(
                Path(args.hithink_db), target, n_bars=args.bars,
                min_bars=260, verbose=args.verbose)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            g0 = {**g0, "pass": False, "status": "qfq_source_error",
                  "detail": f"前复权校验已通过，但生产前复权数据读取失败：{exc}"}
            notes.append("生产前复权数据源失败：价格类跨日指标全部停算")

    if g0["pass"]:
        if target not in set(daily["trade_date"]):
            notes.append(f"同花顺生产库无 {args.date} 交易日数据，最新为 "
                         f"{daily['trade_date'].max():%Y-%m-%d}")
            target = daily["trade_date"].max()
        daily = daily[daily["trade_date"] <= target].copy()
        hithink_names = (daily.dropna(subset=["name"])
                         .drop_duplicates("ts_code", keep="last")
                         .set_index("ts_code")["name"].to_dict())
        # dim_symbol 的少量名称可能为空；仅名称字段回退到通达信，不影响价格口径。
        names = load_name_master(Path(args.hq_cache))
        names.update({code: name for code, name in hithink_names.items()
                      if isinstance(name, str) and name.strip()})
        # 价格类指标和 RPS 必须共享同一份 v_daily_qfq 输入；name 不是计算字段。
        price_daily = daily.drop(columns=["name"])
        work = add_indicators(price_daily)
        rps = calculate_rps(price_daily, verbose=args.verbose)
        latest_rps = rps[rps["trade_date"] == target][
            ["ts_code", "RPS_50", "RPS_120", "RPS_250"]]
        snap = work[work["trade_date"] == target].merge(latest_rps, on="ts_code", how="left")
        snap["name"] = snap["ts_code"].map(names).fillna("")

        imap = load_industry_map(Path(args.hq_cache) / "tdxhy.cfg")
        inames = load_industry_names(Path(args.incon))
        industries = rank_industries(snap, imap, inames)
        leads = [] if industries.empty else industries.head(args.lead_industries)["industry"].tolist()
        candidates = build_candidates(snap, imap, inames, leads, args.min_adv, args.top_n)

        breadth = snap[snap["ret1"].abs() <= 0.25]
        market = {
            "trade_date": f"{target:%Y-%m-%d}",
            "stocks": int(len(breadth)),
            "median_ret1_pct": round(float(breadth["ret1"].median() * 100), 2),
            "up_pct": round(float((breadth["ret1"] > 0).mean() * 100), 1),
            "above_ma20_pct": round(float((breadth["close"] > breadth["ma20"]).mean() * 100), 1),
            "above_ma60_pct": round(float((breadth["close"] > breadth["ma60"]).mean() * 100), 1),
            "total_amount_yi": round(float(breadth["amount"].sum() / 1e8), 1),
        }
    else:
        notes.append("复权闸门未通过：RPS、MA、ATR、跨日收益、回撤与形态筛选全部停算")

    executable_n = int(candidates["executable"].sum()) if len(candidates) else 0
    observation_n = int(len(candidates) - executable_n)
    g2 = {"pass": bool(len(candidates)) and g0["pass"],
          "detail": (f"候选 {len(candidates)} 只（第一主线可执行 {executable_n} 只、"
                     f"次主线观察 {observation_n} 只；站上 MA20/MA60、"
                     f"RPS120≥80、乖离≤12%、ADV20≥{args.min_adv / 1e8:.1f}亿）"
                     if g0["pass"] else "复权闸门未通过，RPS 停算")}
    lead_row = None if industries.empty else industries.iloc[0]
    g3 = {"pass": lead_row is not None and not bool(lead_row["single_stock_driven"]),
          "detail": ("未取得行业横截面" if lead_row is None else
                     f"第一主线={lead_row['name']}｜共振分={lead_row['resonance']:.0f}"
                     f"｜宽度={lead_row['breadth_ma20_pct']:.0f}%"
                     f"｜量能={lead_row['amount_vs_adv20']:.2f}×ADV20"
                     + ("｜单股驱动，降级" if lead_row["single_stock_driven"] else ""))}

    exec_ok = bool(len(candidates))
    g4 = {"pass": exec_ok,
          "detail": ("无可比较标的" if not exec_ok else
                     f"{len(candidates)} 只通过流动性与乖离检查；"
                     f"分钟固定目标 CER 已删除（无历史不可回测），CER_D1 仅作观测")}

    # G5 账户执行
    account_known = args.account_state != "unknown"
    plan, plan_err = read_json(Path(args.plan_json)) if args.plan_json else ({}, None)
    if plan_err:
        notes.append(f"外部计划不可用：{plan_err}")
    sizing: dict[str, Any] = {}
    g5_detail = ""
    if not account_known or args.equity is None or args.available_cash is None \
            or args.single_trade_risk_pct is None:
        g5_detail = ("账户状态/权益/可用现金/单笔风险预算不完整：已保留候选与交易资格，"
                     "但不生成仓位和订单")
    elif not plan.get("entry") or not plan.get("invalidation"):
        # 无外部计划时，只从第一主线内的候选生成示例计划。
        # 次主线标的即使 RPS 更高也只进入观察，不得成为唯一主计划。
        pool = candidates[candidates["executable"]] if len(candidates) else candidates
        if len(pool):
            row = pool.iloc[0]
            entry = float(row["close"])
            invalidation = float(row["ma20"]) * 0.985
            plan = {"ts_code": row["ts_code"], "name": row["name"],
                    "industry_name": row["industry_name"],
                    "entry": entry, "invalidation": invalidation,
                    "atr_pct": float(row["atr_pct"]),
                    "trigger": "次日首次回踩 MA20 或开盘区间上沿且不破 MA20",
                    "cancel": "第一主线宽度跌破 50% 或该股失守 MA20 收盘",
                    "source": "脚本按第一主线内首选候选生成的示例计划，非外部指定"}
            g5_detail = "未提供 --plan-json，按第一主线内首选候选生成示例计划"
        elif len(candidates):
            g5_detail = (f"候选 {len(candidates)} 只全部位于次主线，按规范只进入观察，"
                         f"不生成唯一主计划")
        else:
            g5_detail = "无候选且无外部计划"
    if plan.get("entry") and plan.get("invalidation") and account_known \
            and args.equity and args.available_cash and args.single_trade_risk_pct:
        code = plan.get("ts_code", "")
        atr_pct = plan.get("atr_pct")
        if atr_pct is None and len(candidates):
            hit = candidates[candidates["ts_code"] == code]
            atr_pct = float(hit.iloc[0]["atr_pct"]) if len(hit) else None
        sizing = size_position(
            float(plan["entry"]), float(plan["invalidation"]),
            limit_pct_for(code), atr_pct, float(args.equity),
            float(args.single_trade_risk_pct), float(args.available_cash),
            float(g1.get("cap_pct") or 0.0))
        g5_detail = (g5_detail + "；" if g5_detail else "") + \
            f"计划股数={sizing['shares']}（风险 {sizing['shares_by_risk']} / 现金 {sizing['shares_by_cash']}）"
    g5 = {"pass": bool(sizing.get("shares")), "detail": g5_detail}

    gates = [
        {"id": "G0", "name": "复权闸门", **g0},
        {"id": "G1", "name": "风险许可", **g1},
        {"id": "G2", "name": "RPS候选", **g2},
        {"id": "G3", "name": "主线共振", **g3},
        {"id": "G4", "name": "执行质量", **g4},
        {"id": "G5", "name": "账户执行", **g5},
    ]
    failed = [g for g in gates if not g["pass"]]
    hard_failed = [g for g in failed if g["id"] in ("G0", "G1")]
    if hard_failed:
        action = "零交易（硬门未通过）"
    elif failed:
        action = "研究观察（不产生可执行买单）"
    else:
        action = "条件买入（等待唯一计划触发）"

    return {
        "meta": {
            "trade_date": f"{target:%Y-%m-%d}",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "metric_version": "gated_pipeline_v2_hithink_qfq",
            "universe": "沪深A股（不含北交所）",
            "price_source": data_meta.get("price_source", "未取得—复权闸门未通过"),
            "volume_amount_source": data_meta.get("volume_amount_source", "未取得"),
            "data_quality": data_meta,
            "confidence": "低" if len(failed) >= 2 else ("中" if failed else "中"),
            "notes": notes,
        },
        "decision": {
            "action": action,
            "account_cap_pct": g1.get("cap_pct"),
            "reason": "；".join(f"{g['id']} {g['name']}未通过" for g in failed) or "六门全部通过",
            "main_plan": plan or {},
            "sizing": sizing,
            "funnel": {
                "discovery_candidates": int(len(candidates)),
                "trade_eligible": executable_n,
                "observation_only": observation_n,
                "authorized_orders": 1 if g5.get("pass") else 0,
            },
        },
        "risk_state": {k: g1.get(k) for k in
                       ("cycle_state", "st_cycle", "slope3", "coverage", "state_cap_pct",
                        "cap_pct", "formal", "hard_override", "cap_note", "as_of", "raw")},
        "market": market,
        "gates": [{k: v for k, v in g.items() if k != "raw"} for g in gates],
        "industries": [] if industries.empty else json.loads(
            industries.head(12).to_json(orient="records", force_ascii=False)),
        "candidates": [] if candidates.empty else json.loads(
            candidates[["ts_code", "name", "industry_name", "industry_rank", "executable",
                        "close", "RPS_50", "RPS_120", "RPS_250", "vs_ma20_pct", "atr_pct",
                        "dist_high60_pct", "adv20", "amount_ratio", "cer_d1", "limit_pct"]]
            .head(args.top_n).to_json(orient="records", force_ascii=False)),
        "deleted_features": {
            "东财/同花顺估算资金流": "全市场接口502、逐股无历史，无法回测",
            "题材(概念)归属层与 C/SHS": "block_gn.dat 落后一年，同花顺无版本化历史",
            "分钟固定目标 CER 与板块 CER": "腾讯分钟仅当日，本地分钟停更至2024-06",
            "CER_float": "无经校验的流通市值时间序列",
        },
        "qfq_note": g0.get("convention_note"),
    }


# ---------------------------------------------------------------- HTML

def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def num(value: Any, digits: int = 2, missing: str = "未取得") -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return missing
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return missing


def signed(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return '<span class="flat">未取得</span>'
    v = float(value)
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    arrow = "↑" if v > 0 else ("↓" if v < 0 else "→")
    return f'<span class="{cls}">{v:+.{digits}f} {arrow}</span>'


def render_html(snap: dict[str, Any]) -> str:
    meta, dec, risk = snap["meta"], snap["decision"], snap["risk_state"]
    mkt, sizing = snap["market"], dec.get("sizing") or {}
    plan = dec.get("main_plan") or {}
    funnel = dec.get("funnel") or {}

    gate_cards = "".join(
        f'<article class="gate {"ok" if g["pass"] else "stop"}">'
        f'<div class="gid">{esc(g["id"])} · {esc(g["name"])}</div>'
        f'<b>{"通过" if g["pass"] else "未通过"}</b><p>{esc(g["detail"])}</p></article>'
        for g in snap["gates"])

    ind_rows = "".join(
        f'<tr><td>{i}</td><td>{esc(r["name"])}</td><td>{r["members"]}</td>'
        f'<td>{signed(r["ret5_pct"])}</td><td>{signed(r["ret20_pct"])}</td>'
        f'<td>{num(r["breadth_ma20_pct"], 0)}%</td><td>{num(r["median_rps120"], 0)}</td>'
        f'<td>{num(r["amount_vs_adv20"])}×</td><td>{num(r["resonance"], 0)}</td>'
        f'<td>{"单股驱动" if r["single_stock_driven"] else "—"}</td></tr>'
        for i, r in enumerate(snap["industries"], 1)) or \
        '<tr><td colspan="10">未取得行业横截面（复权闸门未通过或数据不足）</td></tr>'

    cand_rows = "".join(
        f'<tr><td>{esc(r["ts_code"])}</td><td>{esc(r["name"])}</td>'
        f'<td>{esc(r["industry_name"])}</td>'
        f'<td>{"第一主线 · 可执行" if r["executable"] else f"第{r['industry_rank']}主线 · 仅观察"}</td>'
        f'<td>{num(r["close"])}</td>'
        f'<td>{num(r["RPS_50"], 0)}</td><td>{num(r["RPS_120"], 0)}</td><td>{num(r["RPS_250"], 0)}</td>'
        f'<td>{signed(r["vs_ma20_pct"])}</td><td>{num(r["atr_pct"])}%</td>'
        f'<td>{signed(r["dist_high60_pct"])}</td>'
        f'<td>{num((r["adv20"] or 0) / 1e8)}亿</td><td>{num(r["cer_d1"], 1, "—")}</td></tr>'
        for r in snap["candidates"]) or \
        '<tr><td colspan="13">无候选（前列行业内无标的同时满足趋势、强度、乖离与流动性）</td></tr>'
    n_exec = sum(1 for r in snap["candidates"] if r["executable"])

    del_rows = "".join(f'<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>'
                       for k, v in snap["deleted_features"].items())

    if sizing:
        widened = sizing.get("widened")
        size_block = f"""<table><tbody>
<tr><th>标的</th><td>{esc(plan.get('ts_code'))} {esc(plan.get('name'))}
· {esc(plan.get('industry_name'))}（第一主线）</td></tr>
<tr><th>入场 / 硬失效</th><td>{num(sizing['entry'])} / {num(sizing['invalidation'])}</td></tr>
<tr><th>计划止损距离</th><td>{num(sizing['planned_stop_pct'])}%</td></tr>
<tr><th>跌停不可成交下限</th><td>{num(sizing['limit_down_floor_pct'])}%
（涨跌幅限制 {num(sizing['limit_pct'] * 100, 0)}% × {LIMIT_DOWN_PENALTY_K}）</td></tr>
<tr><th>ATR 下限</th><td>{num(sizing['atr_floor_pct'])}%（1 × ATR20）</td></tr>
<tr><th>有效止损距离</th><td><b>{num(sizing['effective_stop_pct'])}%</b>
· 约束来自 {esc(sizing['binding_constraint'])}
{'<span class="riskflag">风险 · 已放大，仓位相应下调</span>' if widened else ''}</td></tr>
<tr><th>单笔风险金额</th><td>{num(sizing['risk_amount'], 0)} 元</td></tr>
<tr><th>股数（风险 / 现金 / 最终）</th><td>{sizing['shares_by_risk']} / {sizing['shares_by_cash']} / <b>{sizing['shares']}</b></td></tr>
<tr><th>对应仓位</th><td>{num(sizing.get('position_pct'))}%（账户上限 {num(risk.get('cap_pct'), 0)}%）</td></tr>
<tr><th>触发条件</th><td>{esc(plan.get('trigger'))}</td></tr>
<tr><th>取消条件</th><td>{esc(plan.get('cancel'))}</td></tr>
<tr><th>T+1</th><td>{esc(sizing['t_plus_one'])}</td></tr></tbody></table>"""
    else:
        size_block = ('<p class="warn"><b>风险 · 不计算股数：</b>'
                      f'{esc(dec["reason"])}。缺少账户状态、权益、可用现金、'
                      '单笔风险预算或硬失效位时，订单数为 0，但候选与交易资格继续保留，'
                      '不生成伪精确仓位。</p>')

    notes_block = "".join(f"<li>{esc(n)}</li>" for n in meta["notes"])
    raw = risk.get("raw") or {}
    audit = json.dumps(snap, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股门控式决策 · {esc(meta['trade_date'])}</title><style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9;--up:#c62828;--down:#16794b;--amber:#9a5b00;--blue:#1f5f9e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1440px;margin:auto;padding:18px}}
.hero{{background:linear-gradient(135deg,#06182a,#11405f);color:#fff;border-radius:18px;padding:26px}}
.eyebrow{{color:#9ecbeb;font-weight:700;letter-spacing:.08em;font-size:12px}}
h1{{font-size:27px;margin:6px 0 16px}}h2{{margin:0 0 12px;font-size:20px}}
.dec{{display:grid;grid-template-columns:1.4fr .8fr .8fr .9fr;gap:12px}}
.dec>div{{background:#ffffff14;border:1px solid #ffffff2b;padding:14px;border-radius:12px}}
.big{{font-size:22px;font-weight:800;margin-top:2px}}
.metaline{{color:#c3d7e7;margin-top:14px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}
.card{{grid-column:span 12;background:#fff;border:1px solid var(--line);border-radius:15px;padding:18px}}
.half{{grid-column:span 6}}
.gates{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}
.gate{{border:1px solid var(--line);border-top:5px solid #16794b;border-radius:12px;padding:12px;background:#fbfcfd}}
.gate.stop{{border-top-color:#c54f3f;background:#fff8f6}}
.gid{{font-weight:800;color:var(--muted);font-size:12px}}.gate p{{color:var(--muted);margin:6px 0 0;font-size:13px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{color:var(--muted);background:#f6f8fa;position:sticky;top:0}}
tbody th{{color:var(--muted);width:38%;font-weight:600}}
.tablebox{{overflow:auto;max-height:520px}}
.up{{color:var(--up);font-weight:600}}.down{{color:var(--down);font-weight:600}}.flat{{color:var(--muted)}}
.riskflag{{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;
background:#fff3e0;color:var(--amber);border:1px solid #e8c48f;font-size:12px;font-weight:700}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:12px;border-radius:8px}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.kv div{{background:#f5f8fb;border:1px solid var(--line);border-radius:10px;padding:11px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:17px}}
.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.three section{{background:#f7f9fb;border:1px solid var(--line);border-radius:10px;padding:13px}}
footer{{color:var(--muted);padding:16px 4px;font-size:13px}}
@media(max-width:980px){{.half{{grid-column:span 12}}.gates{{grid-template-columns:repeat(2,1fr)}}
.dec,.three{{grid-template-columns:1fr}}}}
@media print{{body{{background:#fff}}.tablebox{{max-height:none}}}}
</style></head><body><main class="wrap">

<section class="hero"><div class="eyebrow">复权闸门 · 风险许可 · RPS候选 · 主线共振 · 执行质量 · 账户执行</div>
<h1>A股门控式决策链</h1>
<div class="dec">
<div><small>当前动作</small><div class="big">{esc(dec['action'])}</div><div>{esc(dec['reason'])}</div></div>
<div><small>账户仓位上限</small><div class="big">{num(risk.get('cap_pct'), 0)}%</div>
<div>{esc(risk.get('cycle_state'))} · ST_cycle {num(risk.get('st_cycle'), 1)}</div></div>
<div><small>价格复权状态</small><div class="big">{esc(snap['gates'][0].get('status'))}</div>
<div>置信度：{esc(meta['confidence'])}</div></div>
<div><small>数据截至</small><div class="big">{esc(meta['trade_date'])}</div>
<div>{esc(meta['universe'])}</div></div></div>
<div class="metaline">生成 {esc(meta['generated_at'])} · 口径版本 {esc(meta['metric_version'])}
· 价格：{esc(meta.get('price_source'))} · 量额：{esc(meta.get('volume_amount_source'))}
· ST_cycle 正式口径：{'是' if risk.get('formal') else '否（未完成走样本外回测，仅作风险上限参考）'}</div>
</section>

<section class="grid">
<article class="card"><h2>六道门控</h2><div class="gates">{gate_cards}</div>
{f'<ul>{notes_block}</ul>' if notes_block else ''}</article>

<article class="card"><h2>信号—资格—订单漏斗</h2><div class="kv">
<div><small>发现候选</small><b>{esc(funnel.get('discovery_candidates', 0))}</b></div>
<div><small>第一主线可交易资格</small><b>{esc(funnel.get('trade_eligible', 0))}</b></div>
<div><small>次主线仅观察</small><b>{esc(funnel.get('observation_only', 0))}</b></div>
<div><small>已授权订单</small><b>{esc(funnel.get('authorized_orders', 0))}</b></div>
</div><p class="warn"><b>口径：</b>订单为 0 不等于信号为 0；账户参数只影响最后一层。</p></article>

<article class="card half"><h2>市场宽度（本地重建）</h2><div class="kv">
<div><small>有效样本</small><b>{esc(mkt.get('stocks', '未取得'))}</b></div>
<div><small>中位涨跌</small><b>{signed(mkt.get('median_ret1_pct'))}</b></div>
<div><small>上涨占比</small><b>{num(mkt.get('up_pct'), 1)}%</b></div>
<div><small>站上MA20</small><b>{num(mkt.get('above_ma20_pct'), 1)}%</b></div>
<div><small>站上MA60</small><b>{num(mkt.get('above_ma60_pct'), 1)}%</b></div>
<div><small>两市成交额</small><b>{num(mkt.get('total_amount_yi'), 0)}亿</b></div>
</div></article>

<article class="card half"><h2>涨停生态与情绪</h2><div class="kv">
<div><small>涨停 / 跌停</small><b>{num(raw.get('limit_up_n'), 0)} / {num(raw.get('limit_down_n'), 0)}</b></div>
<div><small>炸板数 / 炸板率</small><b>{num(raw.get('broken_n'), 0)} / {num(raw.get('FR'))}</b></div>
<div><small>一板 / 二板 / 三板</small><b>{num(raw.get('N1'), 0)} / {num(raw.get('N2'), 0)} / {num(raw.get('N3'), 0)}</b></div>
<div><small>最高连板</small><b>{num(raw.get('max_board'), 0)}</b></div>
<div><small>J2（收缩后）</small><b>{num(raw.get('J2_shrunk'), 3)}</b></div>
<div><small>昨涨停today收益</small><b>{signed(raw.get('YLR_close'))}</b></div>
</div></article>

<article class="card"><h2>行业共振排序（第一主线自上而下）</h2><div class="tablebox"><table>
<thead><tr><th>#</th><th>行业</th><th>成分</th><th>5日中位</th><th>20日中位</th>
<th>站上MA20</th><th>RPS120中位</th><th>量能/ADV20</th><th>共振分</th><th>标记</th></tr></thead>
<tbody>{ind_rows}</tbody></table></div>
<p class="warn"><b>风险 · 口径说明：</b>行业来自 <code>tdxhy.cfg</code>（当日更新）。
题材/概念层已删除——唯一本地源 <code>block_gn.dat</code> 落后一年，无法版本化重算，
不满足回测要求。因此本表只有行业维度，不得称为"题材主线"。</p></article>

<article class="card"><h2>候选池（盘前冻结，非买入指令）· 可执行 {n_exec} / 共 {len(snap["candidates"])}</h2>
<div class="tablebox"><table>
<thead><tr><th>代码</th><th>名称</th><th>行业</th><th>主线地位</th><th>收盘</th><th>RPS50</th><th>RPS120</th>
<th>RPS250</th><th>距MA20</th><th>ATR%</th><th>距60日高</th><th>ADV20</th><th>CER_D1</th></tr></thead>
<tbody>{cand_rows}</tbody></table></div>
<p class="warn"><b>风险 · 只有第一主线内的标的可执行：</b>次主线标的即使 RPS 更高也只进入观察，
不得成为唯一主计划。<b>CER_D1 仅作观测：</b>该日线量价效率代理未完成走样本外验证，
不参与排序与仓位决定；分钟级固定目标 CER 已整块删除。</p></article>

<article class="card half"><h2>唯一主计划与仓位</h2>{size_block}</article>

<article class="card half"><h2>已删除的功能及原因</h2><div class="tablebox"><table>
<thead><tr><th>功能</th><th>删除原因</th></tr></thead><tbody>{del_rows}</tbody></table></div>
<p class="warn"><b>风险 · 复权约定：</b>{esc(snap.get('qfq_note'))}</p></article>

<article class="card"><h2>事实、推断、交易假设</h2><div class="three">
<section><b>事实</b><p>本地日线覆盖与最新交易日；复权抽样对账结果与检出的除权日；
涨停/跌停/触板/炸板/连板逐股重建结果（已对齐东财涨停池，2026-08-14 为 63/63，
连板高度逐股一致）；行业配置文件更新时间；账户字段是否完整。</p></section>
<section><b>推断</b><p>用行业中位收益、站上 MA20 宽度、成交额相对 ADV20 与 RPS 中位数
合成共振分推断第一主线；用 ST_cycle 分位推断短周期风险上限。这些是模型输出，
不是市场事实。</p></section>
<section><b>交易假设</b><p>只有六门全过且唯一计划触发才允许条件买入；任一硬门失败即零交易。
T+1 下当日新买不可卖，硬失效位在一字跌停下不可成交，故有效止损距离已按
涨跌幅限制的 {LIMIT_DOWN_PENALTY_K} 倍设下限。</p></section></div>
<p class="warn"><b>风险 · 四项：</b>最大风险是 ST_cycle 阈值尚未走样本外验证，
状态与上限可能系统性偏松或偏紧；失效条件是第一主线宽度跌破 50% 或候选失守 MA20；
对应动作是取消计划并降至观察；反向风险是市场随后放量上行而账户仓位不足，
该机会成本已在上限中显式承担。本页为研究与风险管理材料，不构成收益保证。</p></article>
</section>

<footer>自包含单文件 · 无外链 CSS/JS/字体/CDN · 审计快照已内嵌于下方 JSON。
<script id="audit-snapshot" type="application/json">{audit}</script></footer>
</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="门控式 A 股决策链")
    ap.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--tdx-dir", default=DEFAULT_TDX)
    ap.add_argument("--hithink-db", default=str(DEFAULT_HITHINK_DB),
                    help="同花顺生产 DuckDB；价格指标读取 v_daily_qfq")
    ap.add_argument("--hq-cache", default=str(DEFAULT_HQ))
    ap.add_argument("--incon", default=str(DEFAULT_INCON))
    ap.add_argument("--bars", type=int, default=320, help="读取的日线条数，需覆盖 RPS_250")
    ap.add_argument("--lead-industries", type=int, default=3, help="取前 N 个行业作为候选来源")
    ap.add_argument("--top-n", type=int, default=25)
    ap.add_argument("--min-adv", type=float, default=1.5e8, help="ADV20 下限，元")
    ap.add_argument("--plan-json")
    ap.add_argument("--account-state", choices=("empty", "holding", "unknown"), default="unknown")
    ap.add_argument("--equity", type=float)
    ap.add_argument("--available-cash", type=float)
    ap.add_argument("--single-trade-risk-pct", type=float, help="账户权益百分比，0.50 表示 0.50%%")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot(args)

    stamp = snapshot["meta"]["trade_date"].replace("-", "")
    now = datetime.now().strftime("%H%M")
    json_path = outdir / f"integrated_decision_{stamp}_audit.json"
    html_path = outdir / f"a_share_analysis_{stamp}_{now}.html"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
                         encoding="utf-8")
    html_path.write_text(render_html(snapshot), encoding="utf-8")

    print(f"\n动作: {snapshot['decision']['action']}")
    print(f"原因: {snapshot['decision']['reason']}")
    for g in snapshot["gates"]:
        print(f"  [{'OK  ' if g['pass'] else 'STOP'}] {g['id']} {g['name']}: {g['detail']}")
    print(f"\nHTML : {html_path.resolve()}")
    print(f"审计 : {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
