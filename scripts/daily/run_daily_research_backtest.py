"""Reproducible multi-stock daily RPS research; offline after local data exist."""

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
import platform
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.engine import block_bootstrap_mean
from trend_start.panel import build_panel, load_panel
from trend_start.research import (
    StudyConfig,
    linked_retest,
    metrics,
    sha256,
    simulate,
    source_manifest,
    validate_panel,
)
from trend_start.signals import cross_section_rps, stock_signals

ROOT = PROJECT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD, an observed completed session")
    parser.add_argument("--start", default="2016-09-01")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cfg = (
        StudyConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        if args.config
        else StudyConfig()
    )
    as_of = pd.Timestamp(args.as_of)
    if as_of.date() > datetime.now().date():
        raise ValueError("Future as-of date forbidden")
    cache = ROOT / "output" / f"daily_research_cache_{as_of:%Y%m%d}"
    cache.mkdir(parents=True, exist_ok=True)
    print("[AUDIT] hashing local source files", flush=True)
    sources = source_manifest(args.tdx_root)
    code_paths = [
        Path(__file__).resolve(),
        ROOT / "trend_start/panel.py",
        ROOT / "trend_start/signals.py",
        ROOT / "trend_start/research.py",
        script_file("run_daily_rps.py"),
        script_file("run_local_tdx_compare.py"),
        ROOT / "rps_analyzer/right_confirmation_v2.py",
    ]
    code_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in code_paths}
    signature = {
        "as_of": args.as_of,
        "sources": sources,
        "code": code_hashes,
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }
    manifest_path = cache / "manifest.json"
    cached: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    cached_ok = (
        cached.get("signature") == signature
        and all(
            (cache / name).exists() and sha256(cache / name) == value
            for name, value in cached.get("artifacts", {}).items()
        )
        and bool(cached.get("artifacts"))
    )
    if cached_ok:
        print("[CACHE] verified source/code/content hashes", flush=True)
        panel = load_panel(cache)
        audit = json.loads((cache / "panel_audit.json").read_text(encoding="utf-8"))
    else:
        panel, audit = build_panel(args.tdx_root, as_of, cache)
        if any(x["reason"] != "unsupported adjustment category" for x in audit["excluded_detail"]):
            raise ValueError("Parse/data failure: inspect panel_audit.json; no backtest generated")
    validate_panel(panel)
    if cached_ok:
        with np.load(cache / "rps.npz", allow_pickle=False) as archive:
            rps = {name: archive[name] for name in archive.files}
    else:
        print("[RPS] historical full-valid-local-universe ranks", flush=True)
        rps = cross_section_rps(panel.field("close"))
        np.savez(cache / "rps.npz", **rps)
        if sources != source_manifest(args.tdx_root):
            raise RuntimeError("Local data changed while building snapshot; rerun")
        manifest_path.write_text(
            json.dumps(
                {
                    "signature": signature,
                    "artifacts": {
                        name: sha256(cache / name)
                        for name in ("panel.npz", "rps.npz", "panel_audit.json")
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    shape = panel.field("close").shape
    signals = {s: np.zeros(shape, dtype=bool) for s in ("ongoing", "breakout", "retest")}
    mas = {p: np.full(shape, np.nan, dtype=np.float32) for p in (20, 60)}
    event_rows: list[dict[str, Any]] = []
    for i, code in enumerate(panel.codes):
        if i % 500 == 0:
            print(f"[SIGNALS] {i}/{len(panel.codes)}", flush=True)
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        if len(cols) < 251:
            continue
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float) for f in panel.data})
        for name, matrix in rps.items():
            d[name] = matrix[i, cols]
        original = stock_signals(d, [])
        linked = linked_retest(d, original.A_start.to_numpy(), cfg)
        signals["ongoing"][i, cols] = original.A_trend.to_numpy()
        signals["breakout"][i, cols] = original.A_start.to_numpy()
        signals["retest"][i, cols] = linked.confirmed.to_numpy()
        for p in mas:
            mas[p][i, cols] = d.close.rolling(p).mean().to_numpy()
        for t, row in linked.loc[
            linked.state.isin(["breakout", "touched", "confirmed", "invalid", "expired"])
        ].iterrows():
            t = int(t)
            event = int(row.event_bar)
            event_rows.append(
                {
                    "code": str(code),
                    "date": str(panel.dates[cols[t]]),
                    "event_date": str(panel.dates[cols[event]]) if event >= 0 else None,
                    "state": row.state,
                    "pivot_adjusted": row.frozen_pivot,
                    "floor_adjusted": row.frozen_floor,
                }
            )
    out = (
        args.output
        or ROOT / "output" / f"daily_research_{as_of:%Y%m%d}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(event_rows).to_csv(out / "event_chain.csv", index=False, encoding="utf-8-sig")
    denominator = np.isfinite(rps["RPS250"]).sum(axis=0)
    pd.DataFrame({"date": panel.dates, "valid_local_universe": denominator}).to_csv(
        out / "rps_denominator.csv", index=False
    )
    dates = pd.DatetimeIndex(panel.dates)
    windows = {
        "full": (args.start, args.as_of),
        "historical_2016_2021": (args.start, "2021-12-31"),
        "historical_2022_plus": ("2022-01-01", args.as_of),
    }
    # Annual fresh-capital folds use fixed rules; these are NOT unseen holdouts.
    for year in range(max(pd.Timestamp(args.start).year, 2017), as_of.year + 1):
        windows[f"annual_fold_{year}"] = (f"{year}-01-01", f"{year}-12-31")
    marked = pd.DataFrame(panel.field("close").T).ffill().to_numpy().T
    event_stats: list[dict[str, Any]] = []
    for strategy, sig in signals.items():
        rows, columns = np.where(sig[:, :-1])
        entry = columns + 1
        price = panel.field("open")[rows, entry].astype(float)
        previous = panel.field("close")[rows, columns].astype(float)
        # Same next-open admissibility as the portfolio; no holding-slot filter.
        from trend_start.research import limit_proxy

        limits = np.array(
            [
                limit_proxy(str(panel.codes[r]), panel.dates[e], cfg)
                for r, e in zip(rows, entry, strict=True)
            ]
        )
        gap = price / previous - 1
        admissible = np.isfinite(price) & (gap < limits - 0.003) & (gap <= cfg.max_entry_gap)
        for horizon in (20, 60):
            target = entry + horizon - 1
            mature = admissible & (target < len(dates))
            rr = marked[rows[mature], target[mature]] / price[mature] - 1
            months = pd.Series(dates[columns[mature]].to_period("M").astype(str))
            ci = block_bootstrap_mean(pd.Series(rr), months)
            event_stats.append(
                {
                    "strategy": strategy,
                    "horizon_market_sessions": horizon,
                    "all_signals": len(rows),
                    "admissible_mature": int(mature.sum()),
                    "unmatured_admissible": int((admissible & ~mature).sum()),
                    "mean_gross_mark_return_pct": float(np.mean(rr) * 100) if len(rr) else None,
                    "monthly_bootstrap_low_pct": float(ci[0] * 100) if np.isfinite(ci[0]) else None,
                    "monthly_bootstrap_high_pct": float(ci[1] * 100)
                    if np.isfinite(ci[1])
                    else None,
                }
            )
    pd.DataFrame(event_stats).to_csv(out / "fixed_horizon_events.csv", index=False)
    benchmark = pd.DataFrame({"date": dates, "hs300_close": panel.index_close})
    raw = panel.field("close").astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        both = np.isfinite(raw[:, 1:]) & np.isfinite(raw[:, :-1])
        returns = np.where(both, raw[:, 1:] / raw[:, :-1] - 1, np.nan)
    counts = np.isfinite(returns).sum(axis=0)
    avg = np.divide(np.nansum(returns, axis=0), counts, out=np.zeros(len(counts)), where=counts > 0)
    benchmark["local_equal_weight_rebalanced_index"] = np.r_[1.0, np.cumprod(1 + avg)]
    benchmark.to_csv(out / "benchmarks.csv", index=False)
    # Fixed comparison protocol, no winner selection or parameter optimization.
    comparisons: list[dict[str, Any]] = []
    for strategy, sig in signals.items():
        for exit_ma in (20, 60):
            for market_filter in (False, True):
                variant = cfg.model_copy(
                    update={"exit_ma": exit_ma, "market_filter": market_filter}
                )
                for window, (left, right) in windows.items():
                    a, b = (
                        int(dates.searchsorted(pd.Timestamp(left))),
                        int(dates.searchsorted(pd.Timestamp(right), side="right") - 1),
                    )
                    if a > b:
                        continue
                    result = simulate(panel, sig, rps["RPS120"], mas[exit_ma], variant, a, b)
                    key = f"{strategy}_ma{exit_ma}_market{int(market_filter)}_{window}"
                    for name in ("daily", "trades", "open_positions", "orders"):
                        getattr(result, name).to_csv(
                            out / f"{key}_{name}.csv", index=False, encoding="utf-8-sig"
                        )
                    comparisons.append(
                        {
                            "strategy": strategy,
                            "exit_ma": exit_ma,
                            "market_filter": market_filter,
                            "window": window,
                            **metrics(result, cfg.capital),
                        }
                    )
                print(f"[SIMULATE] {strategy} MA{exit_ma} market={market_filter}", flush=True)
    table = pd.DataFrame(comparisons)
    table.to_csv(out / "comparison.csv", index=False, encoding="utf-8-sig")
    warnings = [
        "研究模拟，不是实时买卖指令；没有券商接口。",
        "前复权合成份额；不模拟整手、最低佣金、真实股数及现金分红。",
        "缺历史ST/退市全量/流通股本；全市场仅指本地有效沪深股票，不等于交易所完整历史市场。",
        "特殊复权事件整股剔除会产生未来信息条件化；不可把结果称为无偏回测。",
        "涨跌停为代理，主板默认5%保守阻挡；缺历史实际涨跌停参考价。",
        "所有历史区间已被研究过；分段结果是回顾性检验，不是未触碰的样本外。",
        "未实现行业/因子中性归因、基准配对置信区间及独立未来验证，不能证明盈利优势。",
        "成交量仍为原始股数，送转附近缩量比较可能失真。",
    ]
    record = {
        "as_of": args.as_of,
        "generated_at": datetime.now().isoformat(),
        "config": cfg.model_dump(),
        "panel": audit,
        "snapshot_manifest": str(manifest_path),
        "snapshot_sha256": sha256(manifest_path),
        "python": platform.python_version(),
        "warnings": warnings,
        "code_hashes": code_hashes,
        "comparison_protocol": (
            "3 entries x MA20/60 x market off/on; retrospective split and annual folds;"
            " no optimization. Fixed-horizon events are gross marks, not executable returns."
        ),
    }
    (out / "audit.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    display = table.loc[table.window == "historical_2022_plus"].round(3)
    html = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>日线RPS研究回测</title>"
    html += (
        "<style>body{font:15px sans-serif;margin:28px;line-height:1.7}"
        "td,th{padding:8px;border:1px solid #ddd}table{border-collapse:collapse}"
        "li{color:#824b10}</style>"
    )
    html += f"<h1>日线 RPS 研究回测</h1><p>截至 {args.as_of}；仅历史研究，非交易指令。</p>"
    html += "<h2>必须先读的限制</h2><ul>" + "".join(f"<li>{s}</li>" for s in warnings) + "</ul>"
    html += "<h2>2022年以后的回顾性比较</h2>" + display.to_html(index=False, na_rep="缺")
    html += (
        "<p>完整数据见 comparison.csv（含逐年固定规则检验）；每个方案有"
        " daily/trades/open_positions/orders 四张独立审计表。"
        "event_chain.csv 保存冻结突破位与事件日期。没有自动选出最佳方案。</p>"
        "<p>fixed_horizon_events.csv 为20/60个市场交易日的毛估值收益；"
        "不是成交收益，未成熟事件单列，不重叠信号未去重，不能与独立交易胜率混用。"
        "benchmarks.csv 的等权指数每日再平衡且不含费用，仅为描述性基准。</p></html>"
    )
    (out / "report.html").write_text(html, encoding="utf-8")
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
