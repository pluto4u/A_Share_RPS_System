"""Run registered daily research locally; never fetch quotes or rebuild a panel."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
import multiprocessing as mp
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from trend_start.dual_system import (
    GRID,
    TRADE_COLUMNS,
    Array,
    StudyConfig,
    a_specs,
    b_specs,
    book,
    context,
    control_specs,
    load_prefix,
    metrics,
    nonoverlap,
    sha256,
    trade_rows,
)

ROOT = PROJECT_ROOT
CACHE = ROOT / "output/trend_start_cache_20260930"
SPEC = ROOT / "docs/DUAL_RESEARCH_20261002.md"
OUT = ROOT / "output/dual_research_20261002_v1"


def timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def hashes() -> dict[str, str]:
    files = [
        Path(__file__),
        ROOT / "trend_start/dual_system.py",
        SPEC,
        ROOT / "trend_start/engine.py",
        script_file("run_pullback_system.py"),
        CACHE / "panel.npz",
        CACHE / "rps.npz",
        ROOT / "tests/test_dual_system.py",
    ]
    files += [
        ROOT / f"output/daily_system/{name}"
        for name in (
            "复盘.md",
            "grid_is_summary.csv",
            "trades_strategy.csv",
            "trades_baseline.csv",
            "历史对照.csv",
        )
    ]
    return {str(p.relative_to(ROOT)): sha256(p) for p in files}


def audit_old(out: Path) -> None:
    old = ROOT / "output/daily_system"
    tr = pd.read_csv(old / "trades_strategy.csv")
    hist = pd.read_csv(old / "历史对照.csv")
    if len(tr) != len(hist) or not np.array_equal(tr.code, hist["代码"]):
        raise ValueError("old trade rows cannot be aligned with dated history")
    aligned = tr.copy()
    aligned["signal_date"] = hist["信号日"].to_numpy()
    cutoff_rows = aligned.loc[aligned.signal_date == "2021-12-31", "sig_cal"]
    if cutoff_rows.empty:
        raise ValueError("old table does not establish cutoff calendar index")
    cutoff = int(cutoff_rows.iloc[0])
    exit_field = "exit_cal_open" if "exit_cal_open" in aligned.columns else "exit_cal"
    if exit_field not in aligned.columns:
        raise ValueError("old trade table missing exit calendar field")
    leak = aligned.loc[
        (aligned.signal_date >= "2016-09-01")
        & (aligned.signal_date <= "2021-12-31")
        & (aligned[exit_field] > cutoff)
    ]
    leak.to_csv(out / "old_boundary_leaks.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                "check": "old_strategy_boundary_crossing",
                "finding": len(leak),
                "status": "needs_revision",
                "source": "run_pullback_system.py:480-491",
            },
            {
                "check": "old_base_relative_narrowing",
                "finding": None,
                "status": "missing_predicate",
                "source": "run_pullback_system.py:132-146",
            },
            {
                "check": "old_actual_price_retest",
                "finding": None,
                "status": "missing_predicate",
                "source": "run_pullback_system.py:155-181",
            },
            {
                "check": "history_independence",
                "finding": None,
                "status": "already_accessed_not_independent",
                "source": "conversation",
            },
        ]
    ).to_csv(out / "old_audit.csv", index=False, encoding="utf-8-sig")


def grid_worker(args: tuple[str, str, int, int]) -> str:
    cache, destination, begin, end = args
    cfg = StudyConfig()
    panel, rps = load_prefix(Path(cache), cfg.is_end)
    start_cal = int(np.searchsorted(panel.dates, np.datetime64(cfg.is_start)))
    sums = np.zeros(len(panel.dates))
    counts: Array = np.zeros(len(panel.dates), dtype=np.int64)
    rows: list[pd.DataFrame] = []
    eligible, excluded = 0, 0
    for i in range(begin, min(end, len(panel.codes))):
        if (i - begin) % 200 == 0:
            print(f"[IS GRID {begin}:{end}] stock {i}", flush=True)
        ctx = context(panel, rps, i)
        if ctx is None:
            excluded += 1
            continue
        eligible += 1
        frame, _ = trade_rows(ctx, a_specs(ctx), cfg.is_start, forward=False)
        if not frame.empty:
            chained = nonoverlap(frame)
            rows.append(chained[["combo", "sig_cal", "ret", "unfinished"]])
        ctl, _ = trade_rows(ctx, control_specs(ctx), cfg.is_start, forward=False)
        if not ctl.empty:
            np.add.at(sums, ctl.sig_cal.to_numpy(dtype=int), ctl.ret.to_numpy())
            np.add.at(counts, ctl.sig_cal.to_numpy(dtype=int), 1)
    payload = {
        "rows": pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(columns=["combo", "sig_cal", "ret", "unfinished"]),
        "sums": sums,
        "counts": counts,
        "cutoff": str(panel.dates[-1]),
        "start_cal": start_cal,
        "range": [begin, end],
        "eligible": eligible,
        "excluded_short_history": excluded,
    }
    path = Path(destination) / f"grid_{begin}_{end}.pkl"
    with path.open("wb") as stream:
        pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[IS GRID {begin}:{end}] saved {len(payload['rows'])} bounded rows", flush=True)
    return str(path)


def daily_forward_table(offers: pd.DataFrame, panel: Any) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in offers.itertuples(index=False):
        item: dict[str, Any] = {
            "model": r.model,
            "code": r.code,
            "signal_date": r.signal_date,
            "signal_close": r.signal_close,
        }
        for h in (1, 5):
            col = int(r.sig_cal) + h
            present = col < len(panel.dates)
            item[f"d{h}_date"] = str(panel.dates[col]) if present else None
            px = float(panel.field("close")[int(r.code_row), col]) if present else np.nan
            item[f"d{h}_close"] = px
            item[f"d{h}_return_pct"] = (px / r.signal_close - 1) * 100
        rows.append(item)
    return pd.DataFrame(rows)


def period(out: Path, cutoff: str, start: str, cid: int | None, cfg: StudyConfig) -> pd.DataFrame:
    out.mkdir(exist_ok=False)
    panel, rps = load_prefix(CACHE, cutoff)
    frames: list[pd.DataFrame] = []
    rejected: list[pd.DataFrame] = []
    excluded: list[dict[str, Any]] = []
    for i, code in enumerate(panel.codes):
        if i % 700 == 0:
            print(f"[DETAIL {cutoff}] {i}/{len(panel.codes)}", flush=True)
        ctx = context(panel, rps, i)
        if ctx is None:
            excluded.append({"code": str(code), "reason": "insufficient_history_at_cutoff"})
            continue
        specs = b_specs(ctx, cfg) + control_specs(ctx)
        if cid is not None:
            specs += a_specs(ctx, cid) + a_specs(ctx, cid, volume_baseline=True)
        frame, rej = trade_rows(ctx, specs, start)
        if not frame.empty:
            frames.append(frame)
        if not rej.empty:
            rejected.append(rej)
    all_events = (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=TRADE_COLUMNS)
    )
    reject = (
        pd.concat(rejected, ignore_index=True)
        if rejected
        else pd.DataFrame(
            columns=[
                "code",
                "model",
                "signal_date",
                "sig_cal",
                "reason",
                "signal_close",
                "ma20",
                "pocket_low",
                "rps120",
            ]
        )
    )
    # All future-return columns are evaluation data, not state-machine inputs.
    causal_columns = [
        "model",
        "combo",
        "code",
        "sig_cal",
        "signal_date",
        "rps120",
        "signal_close",
        "ma20",
        "pocket_low",
        "volume_bar",
    ]
    all_events.loc[all_events.model != "control", causal_columns].to_csv(
        out / "signal_evidence.csv", index=False, encoding="utf-8-sig"
    )
    reject.to_csv(out / "rejected_execution.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excluded, columns=["code", "reason"]).to_csv(
        out / "excluded_history.csv", index=False, encoding="utf-8-sig"
    )
    cohort = (
        all_events.loc[all_events.model == "control"]
        .groupby("sig_cal")
        .agg(stocks=("ret", "size"), mean_ret=("ret", "mean"), d1=("d1", "mean"), d5=("d5", "mean"))
    )
    cohort["signal_date"] = [str(panel.dates[int(j)]) for j in cohort.index]
    cohort.to_csv(out / "control_by_date.csv", encoding="utf-8-sig")
    summary: list[dict[str, Any]] = []
    models = ["A_retest", "A_volume", "B_trend", "control", "A_plus_B"]
    for model in models:
        offered = (
            all_events.loc[all_events.model.isin(["A_retest", "B_trend"])].copy()
            if model == "A_plus_B"
            else all_events.loc[all_events.model == model].copy()
        )
        offered = offered.reset_index(drop=True)
        chain = nonoverlap(offered)
        result = book(offered, panel, cfg, start)
        dest = out / model
        dest.mkdir()
        chain.to_csv(dest / "trade_outcomes.csv", index=False, encoding="utf-8-sig")
        result.curve.to_csv(dest / "equity_cash.csv", index=False, encoding="utf-8-sig")
        result.holdings.to_csv(dest / "holdings.csv", index=False, encoding="utf-8-sig")
        result.trades.to_csv(dest / "portfolio_trades.csv", index=False, encoding="utf-8-sig")
        result.rejected.to_csv(dest / "portfolio_rejected.csv", index=False, encoding="utf-8-sig")
        result.contribution.to_csv(
            dest / "stock_contribution.csv", index=False, encoding="utf-8-sig"
        )
        daily_forward_table(chain, panel).to_csv(
            dest / "daily_forward.csv", index=False, encoding="utf-8-sig"
        )
        m = metrics(chain, cohort.mean_ret, result, cfg)
        m.update(model=model, start=start, end=str(panel.dates[-1]))
        summary.append(m)
        year = pd.to_datetime(result.curve.date).dt.year
        annual = []
        prior = cfg.capital
        for y, group in result.curve.groupby(year):
            annual.append(
                {
                    "year": int(y),
                    "return_pct": (group.equity.iloc[-1] / prior - 1) * 100,
                    "end_equity": group.equity.iloc[-1],
                }
            )
            prior = float(group.equity.iloc[-1])
        pd.DataFrame(annual).to_csv(dest / "annual.csv", index=False, encoding="utf-8-sig")
    stats = pd.DataFrame(summary)
    stats.to_csv(out / "metrics.csv", index=False, encoding="utf-8-sig")
    # Only current pending signals, never current buy/sell conclusions.
    watch = reject.loc[
        (reject.reason == "pending_next_session")
        & (reject.sig_cal == len(panel.dates) - 1)
        & (reject.model.isin(["A_retest", "B_trend"]))
    ].copy()
    watch = watch.sort_values(["rps120", "code", "model"], ascending=[False, True, True])
    watch["state"] = "candidate_for_human_review"
    watch.to_csv(out / "latest_candidates.csv", index=False, encoding="utf-8-sig")
    return stats


def train(workers: int) -> None:
    cfg = StudyConfig()
    OUT.mkdir(exist_ok=False)
    manifest = {
        "created_at": timestamp(),
        "cfg": cfg.model_dump(),
        "hashes": hashes(),
        "future_history_policy": "already_accessed_not_independent",
        "price_units": "adjusted_fractional_research_units_not_real_shares",
        "versions": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__},
    }
    write_json(OUT / "registered.json", manifest)
    audit_old(OUT)
    # Read metadata, not a full-history signal or return computation.
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        nstocks = len(z["codes"])
    parts = OUT / "is_grid_parts"
    parts.mkdir()
    step = (nstocks + workers - 1) // workers
    jobs = [(str(CACHE), str(parts), s, min(s + step, nstocks)) for s in range(0, nstocks, step)]
    with mp.get_context("spawn").Pool(workers) as pool:
        paths = pool.map(grid_worker, jobs)
    packs = []
    for path in paths:
        with Path(path).open("rb") as stream:
            packs.append(pickle.load(stream))
    if any(p["cutoff"] > cfg.is_end for p in packs):
        raise AssertionError("IS part used future prices")
    sums = np.sum([p["sums"] for p in packs], axis=0)
    counts = np.sum([p["counts"] for p in packs], axis=0)
    ctl = pd.Series(np.divide(sums, counts, out=np.full(len(sums), np.nan), where=counts > 0))
    grid = pd.concat([p["rows"] for p in packs], ignore_index=True)
    grid["excess"] = grid.ret.to_numpy() - ctl.reindex(grid.sig_cal).to_numpy()
    grouped = grid.groupby("combo").agg(
        trades=("ret", "size"),
        mean_ret=("ret", "mean"),
        mean_excess=("excess", "mean"),
        boundary_marks=("unfinished", "sum"),
    )
    grouped = grouped.reindex(range(len(GRID))).reset_index(names="combo")
    grouped["trades"] = grouped.trades.fillna(0).astype(int)
    for j, field in enumerate(
        [
            "decline_bars",
            "decline_min",
            "base_bars",
            "base_range",
            "vol_contract",
            "vol_expand",
            "retest_bars",
            "tolerance",
        ]
    ):
        grouped[field] = [p[j] for p in GRID]
    grouped["eligible"] = grouped.trades >= cfg.min_is_trades
    grouped = grouped.sort_values(
        ["mean_excess", "trades", "combo"], ascending=[False, False, True]
    )
    grouped.to_csv(OUT / "grid_is_summary.csv", index=False, encoding="utf-8-sig")
    candidates = grouped.loc[grouped.eligible & grouped.mean_excess.notna()]
    winner = int(candidates.iloc[0].combo) if not candidates.empty else None
    if hashes() != manifest["hashes"]:
        raise RuntimeError("sources changed during training; this run is invalid")
    freeze = {
        **manifest,
        "frozen_at": timestamp(),
        "a_combo": winner,
        "a_params": list(GRID[winner]) if winner is not None else None,
        "a_status": "selected_in_sample_only" if winner is not None else "no_eligible_combo",
        "is_input_end": cfg.is_end,
        "parts": [
            {k: p[k] for k in ("range", "cutoff", "eligible", "excluded_short_history")}
            for p in packs
        ],
    }
    write_json(OUT / "freeze.json", freeze)
    period(OUT / "is", cfg.is_end, cfg.is_start, winner, cfg)
    print(f"[FREEZE] A combo={winner}; IS only. No independent validation claimed.", flush=True)


def number(value: Any) -> str:
    return "缺" if value is None or pd.isna(value) else f"{float(value):.2f}"


def review(is_stats: pd.DataFrame, history: pd.DataFrame) -> None:
    freeze = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    rows = []
    for r in history.itertuples(index=False):
        rows.append(
            f"| {r.model} | {number(r.cagr_pct)} | {number(r.max_drawdown_pct)} | "
            f"{r.portfolio_trades} | {number(r.mean_cash_pct)} | "
            f"[{number(r.ci_lo_pct)}, {number(r.ci_hi_pct)}] |"
        )
    a = history.loc[history.model == "A_retest"].iloc[0]
    base = history.loc[history.model == "A_volume"].iloc[0]
    uplift = (
        pd.notna(a.ci_lo_pct)
        and a.ci_lo_pct > 0
        and pd.notna(base.excess_mean_pct)
        and a.excess_mean_pct > base.excess_mean_pct
    )
    text = "\n".join(
        [
            "# 双引擎日线研究结果",
            "",
            f"生成：{timestamp()}",
            "",
            "100万元研究资金、最多20只、次日开盘模拟；最大回撤20%为验收线。",
            "复权可分割单位不是真实股数，整手、最低佣金、原始限价与容量未完整模拟。",
            "",
            "## 判定",
            "",
            "**证据不足，尚不能证明能够在A股稳定赚钱。**",
            "2022年之后已被查看，本表仅为已污染历史复盘，不是新独立样本外。",
            f"A对放量即进入对照：{'历史统计通过，但仍无独立验证' if uplift else '没有提升'}。",
            "净收益为正或回撤通过，不等于选股优势已经验证。失败后没有自动换参数。",
            "",
            "## 历史复盘：2022-01-01至既有面板末日",
            "",
            "| 模型 | 年化% | 最大回撤% | 组合模拟笔数 | 平均现金% | 单股超额95%区间 |",
            "|---|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## 参数和边界",
            "",
            f"A组合：{freeze['a_combo']}；参数：{freeze['a_params']}。",
            "A在允许网格内挑选，输入和结算严格截断至2021-12-31；B为事前登记的单一假设，未调参。",
            "1296次搜索仍有选择偏差，月度区间不是多重检验校正证明。",
            "旧版不覆盖，跨年泄漏证据见old_boundary_leaks.csv。",
            "",
            "## 文件怎么查看",
            "",
            "- grid_is_summary.csv：所有允许组合的样本内排名；freeze.json：配置及哈希。",
            "- is/metrics.csv：严格截止的样本内结果。",
            "- history/metrics.csv：历史复盘汇总，不是独立验证。",
            "- history/模型/equity_cash.csv、holdings.csv：逐日现金、权益、持仓和陈旧估值标记。",
            "- history/模型/portfolio_trades.csv：接受事件；portfolio_rejected.csv：拒绝事件。",
            "- history/模型/stock_contribution.csv：股票贡献，与净值变化守恒。",
            "- history/模型/daily_forward.csv：第1/5个市场交易日的日期、收盘与收益；缺值保留。",
            "- history/signal_evidence.csv：因果信号证据，与未来收益评估表分离。",
            "- history/latest_candidates.csv：仅最新收盘待人工复核状态，不是交易指令。",
            "",
            "## 限制与下一步",
            "",
            "历史ST身份、退市股完整性、复权成交单位和涨跌停代理仍有限制；不能把覆盖的股票称为完整无偏历史市场。",
            "同日RPS辅助排序来自既有全市场缓存，不在候选池内重排RPS。",
            "回撤超过20%的版本不符合用户风险验收线；通过也不是未来最大损失保证。",
            "下一轮只提样本内假设；真正验证须冻结后积累未来未见数据，本轮不创建自动交易。",
            "",
        ]
    )
    (OUT / "review.md").write_text(text, encoding="utf-8")
    is_stats.to_csv(OUT / "is_metrics.csv", index=False, encoding="utf-8-sig")


def replay() -> None:
    frozen = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    if hashes() != frozen["hashes"]:
        raise RuntimeError("code/config/source changed since freeze; replay forbidden")
    history_dir = OUT / "history"
    if history_dir.exists() or (OUT / "history_access.json").exists():
        raise RuntimeError("this version already accessed historical replay; no second run")
    cfg = StudyConfig.model_validate(frozen["cfg"])
    # Access is logged before any historical prices enter signal/outcome computation.
    write_json(
        OUT / "history_access.json",
        {
            "first_access": timestamp(),
            "policy": "already_accessed_history_not_independent",
            "freeze_sha256": sha256(OUT / "freeze.json"),
        },
    )
    with np.load(CACHE / "panel.npz", allow_pickle=False) as z:
        last = str(z["dates"][-1])
    result = period(history_dir, last, cfg.history_start, frozen["a_combo"], cfg)
    if hashes() != frozen["hashes"]:
        raise RuntimeError("sources changed after historical access; this run is invalid")
    write_json(
        OUT / "verification.json",
        {
            "checked_at": timestamp(),
            "hashes_unchanged": True,
            "independent_validation": False,
            "no_parameter_change_after_history": True,
            "evidence_status": "insufficient_independent_validation",
        },
    )
    review(pd.read_csv(OUT / "is/metrics.csv"), result)
    print(result.to_string(index=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["train", "replay"])
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")
    if args.stage == "train":
        train(args.workers)
    else:
        replay()


if __name__ == "__main__":
    mp.freeze_support()
    main()
