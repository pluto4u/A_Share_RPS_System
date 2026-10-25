"""Fixed price/volume launch experiments; RPS retained only as a diagnostic."""

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
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from run_daily_rps import names_from_tnf
from trend_start.panel import load_panel
from trend_start.price_volume import LaunchConfig, launch_events, launch_features
from trend_start.pv_engine import simulate_launch
from trend_start.research import (
    StudyConfig,
    metrics,
    sha256,
    simulate,
    source_manifest,
    validate_panel,
)
from trend_start.signals import stock_signals

ROOT = PROJECT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "output/daily_research_cache_20260930"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="Independent window YYYY-MM-DD; starts in cash",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config/price_volume_launch.json")
    args = parser.parse_args()
    specification = json.loads(args.config.read_text(encoding="utf-8"))
    launch = LaunchConfig.model_validate(specification["launch"])
    cfg = StudyConfig.model_validate(specification["simulation"])
    manifest_path = args.snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_hash = sha256(manifest_path)
    panel_audit = json.loads((args.snapshot / "panel_audit.json").read_text(encoding="utf-8"))
    tdx_root = Path(panel_audit["tdx_root"])
    print("[VERIFY] snapshot/source hashes", flush=True)
    if source_manifest(tdx_root) != manifest["signature"]["sources"]:
        raise ValueError(
            "Source changed; build a new dated snapshot first; do not reuse stale data"
        )
    for name, expected in manifest["artifacts"].items():
        if sha256(args.snapshot / name) != expected:
            raise ValueError(f"Snapshot content changed: {name}")
    for name, expected in manifest["signature"]["code"].items():
        if sha256(ROOT / name) != expected:
            raise ValueError(f"Snapshot builder code changed: {name}")
    panel = load_panel(args.snapshot)
    validate_panel(panel)
    with np.load(args.snapshot / "rps.npz", allow_pickle=False) as archive:
        rps = {key: archive[key] for key in archive.files}
    shape = panel.field("close").shape
    signal_names = ("legacy_rps", "pv_raw_order", "pv_quality", "pv_location", "pv_retest")
    signals = {key: np.zeros(shape, dtype=bool) for key in signal_names}
    matrices = {
        key: np.full(shape, np.nan, dtype=np.float32)
        for key in ("quality", "pivot", "pocket", "atr", "ma20", "ma60")
    }
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    funnel = {
        key: 0
        for key in ("stock_bars", "platform_bars", "raw_events", "located_events", "retest_events")
    }
    names = names_from_tnf(tdx_root / "T0002/hq_cache")
    for i, code in enumerate(panel.codes):
        if i % 500 == 0:
            print(f"[FEATURES] {i}/{len(panel.codes)}", flush=True)
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        if len(cols) < 251:
            continue
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float) for f in panel.data})
        for key, values in rps.items():
            d[key] = values[i, cols]
        f = launch_features(d, launch)
        e = launch_events(d, f, launch)
        old = stock_signals(d, [])
        signals["legacy_rps"][i, cols] = old.A_start.to_numpy()
        signals["pv_raw_order"][i, cols] = e.raw.to_numpy()
        signals["pv_quality"][i, cols] = e.raw.to_numpy()
        signals["pv_location"][i, cols] = e.direct.to_numpy()
        signals["pv_retest"][i, cols] = e.retest.to_numpy()
        for key in ("quality", "ma20", "ma60", "atr"):
            matrices[key][i, cols] = f[key].to_numpy()
        # Event fields frozen for direct/retest signals; raw current features elsewhere.
        matrices["pivot"][i, cols] = e.frozen_pivot.fillna(f["pivot"]).to_numpy()
        matrices["pocket"][i, cols] = e.frozen_pocket.fillna(f.pocket).to_numpy()
        event_quality = e.quality.fillna(f.quality).to_numpy()
        matrices["quality"][i, cols] = event_quality
        funnel["stock_bars"] += len(d)
        funnel["platform_bars"] += int(f.platform.sum())
        funnel["raw_events"] += int(e.raw.sum())
        funnel["located_events"] += int(e.direct.sum())
        funnel["retest_events"] += int(e.retest.sum())
        for t, row in e.loc[
            e.state.isin(["breakout", "touched", "confirmed", "invalid", "expired"])
        ].iterrows():
            t, event = int(t), int(row.event_bar)
            events.append(
                {
                    "code": str(code),
                    "date": str(panel.dates[cols[t]]),
                    "event_date": str(panel.dates[cols[event]]),
                    "state": row.state,
                    "direct_admissible": bool(row.direct),
                    "retest_signal": bool(row.retest),
                    "pivot_adjusted": row.frozen_pivot,
                    "pocket_adjusted": row.frozen_pocket,
                    "quality": row.quality,
                    "risk_pct": row.structure_risk * 100,
                }
            )
        last_state = str(e.state.iloc[-1])
        if cols[-1] == len(panel.dates) - 1 and (
            f.platform.iloc[-1] or last_state not in ("idle", "invalid", "expired")
        ):
            name = names.get(str(code), "缺")
            if "ST" in name.upper().replace(" ", "") or "退" in name:
                continue  # present-day human observations only; never historical universe
            observations.append(
                {
                    "代码": str(code),
                    "名称": name,
                    "as_of": str(panel.dates[-1]),
                    "状态": last_state if last_state != "idle" else "平台观察",
                    "收盘": float(d.close.iloc[-1]),
                    "量价评分": float(event_quality[-1]),
                    "冻结支点": float(matrices["pivot"][i, cols[-1]]),
                    "冻结口袋低点": float(matrices["pocket"][i, cols[-1]]),
                    "RPS50_诊断": float(rps["RPS50"][i, cols[-1]]),
                    "RPS120_诊断": float(rps["RPS120"][i, cols[-1]]),
                    "RPS250_诊断": float(rps["RPS250"][i, cols[-1]]),
                    "仅研究观察": True,
                }
            )
    out = args.output or ROOT / "output" / f"price_volume_launch_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(events).to_csv(out / "event_chain.csv", index=False, encoding="utf-8-sig")
    observed = pd.DataFrame(observations)
    if not observed.empty:
        observed = observed.sort_values("量价评分", ascending=False)
    observed.to_csv(out / "observations.csv", index=False, encoding="utf-8-sig")
    dates = pd.DatetimeIndex(panel.dates)
    windows = {
        "full": ("2016-09-01", str(dates[-1].date())),
        "historical_2022_plus": ("2022-01-01", str(dates[-1].date())),
    }
    for year in range(2017, dates[-1].year + 1):
        windows[f"annual_fold_{year}"] = (f"{year}-01-01", f"{year}-12-31")
    focus_window = "historical_2022_plus"
    if args.start_date is not None:
        if not dates[0].date() <= args.start_date <= dates[-1].date():
            raise ValueError("Requested start date is outside the frozen snapshot")
        focus_window = f"from_{args.start_date:%Y%m%d}"
        windows = {focus_window: (args.start_date.isoformat(), str(dates[-1].date()))}
    rows: list[dict[str, Any]] = []
    modes: tuple[Literal["ma20", "ma60", "staged"], ...] = ("ma20", "ma60", "staged")
    for model, sig in signals.items():
        priority = rps["RPS120"] if model == "legacy_rps" else matrices["quality"]
        if model == "pv_raw_order":
            priority = np.zeros(
                shape, dtype=np.float32
            )  # deterministic code order, no strength rank
        for mode in modes:
            if model == "legacy_rps" and mode == "staged":
                continue  # do not relabel legacy events as the new frozen launch model
            for window, (left, right) in windows.items():
                a = int(dates.searchsorted(pd.Timestamp(left)))
                b = int(dates.searchsorted(pd.Timestamp(right), side="right") - 1)
                if a > b:
                    continue
                if model == "legacy_rps":
                    average = matrices[mode]
                    original_cfg = cfg.model_copy(update={"exit_ma": 20 if mode == "ma20" else 60})
                    result = simulate(panel, sig, priority, average, original_cfg, a, b)
                else:
                    result = simulate_launch(
                        panel,
                        sig,
                        priority,
                        matrices["pivot"],
                        matrices["pocket"],
                        matrices["atr"],
                        matrices["ma20"],
                        matrices["ma60"],
                        cfg,
                        launch,
                        mode,
                        model in ("pv_location", "pv_retest"),
                        a,
                        b,
                    )
                prefix = f"{model}_{mode}_{window}"
                for kind in ("daily", "trades", "open_positions", "orders"):
                    getattr(result, kind).to_csv(
                        out / f"{prefix}_{kind}.csv", index=False, encoding="utf-8-sig"
                    )
                rows.append(
                    {"model": model, "exit": mode, "window": window, **metrics(result, cfg.capital)}
                )
            print(f"[SIMULATE] {model}/{mode}", flush=True)
    table = pd.DataFrame(rows)
    table.to_csv(out / "comparison.csv", index=False, encoding="utf-8-sig")
    if (
        sha256(manifest_path) != snapshot_hash
        or source_manifest(tdx_root) != manifest["signature"]["sources"]
    ):
        raise RuntimeError(
            "Source/snapshot changed during run; outputs are incomplete and unaudited"
        )
    audit = {
        "as_of": str(panel.dates[-1]),
        "requested_start_date": args.start_date.isoformat() if args.start_date else None,
        "initial_state": (
            "cash only; no inherited positions; first entry after an in-window close signal"
        ),
        "config": cfg.model_dump(),
        "launch_config": launch.model_dump(),
        "snapshot_manifest": str(manifest_path.resolve()),
        "snapshot_sha256": snapshot_hash,
        "funnel": funnel,
        "rps_role": "diagnostic only for new models; legacy comparison retains old RPS gates",
        "fixed_protocol": (
            "old baseline; raw/code order; raw/quality rank; location; retest;"
            " MA20/MA60/staged; no optimization"
        ),
        "source_code_hashes": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in (
                Path(__file__).resolve(),
                ROOT / "trend_start/price_volume.py",
                ROOT / "trend_start/pv_engine.py",
            )
        },
        "warnings": [
            "仅历史研究，无订单接口；新阈值是预设假设，不是最优参数。",
            "历史ST、退市覆盖缺失；特殊复权事件整股剔除有未来条件化偏差。",
            "使用复权合成份额、原始成交量和涨跌停代理；不是真实现金分红或券商级仿真。",
            "所有历史已被查看，年度折是回顾性检验，不能当作独立样本外。",
            "RPS仍依赖本地有效股票集合，不代表交易所完整历史全市场。",
        ],
    }
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shown = table.loc[table.window == focus_window].copy()
    shown["model"] = shown.model.map(
        {
            "legacy_rps": "旧RPS启动对照",
            "pv_raw_order": "量价事件／代码顺序",
            "pv_quality": "量价事件／质量排序",
            "pv_location": "位置约束／质量排序",
            "pv_retest": "独立缩量回踩再转强",
        }
    )
    shown = shown[
        ["model", "exit", "cagr_pct", "max_drawdown_pct", "closed_trades", "average_exposure_pct"]
    ]
    shown.columns = ["研究模型", "退出", "年化%", "最大回撤%", "完成交易", "平均资金敞口%"]
    html = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>量价启动研究</title>"
    html += (
        "<style>body{font:15px/1.7 system-ui;margin:28px}table{border-collapse:collapse}"
        "td,th{padding:9px;border-bottom:1px solid #ddd}.scroll{overflow-x:auto}</style>"
    )
    html += (
        "<h1>量价启动事件：RPS只作诊断</h1>"
        f"<p>截至 {escape(str(panel.dates[-1]))}；仅历史研究，不是交易建议。</p>"
    )
    html += (
        "<p>按固定顺序对照：事件定义→质量排序→位置约束→"
        "试错/趋势分阶段退出→等待回踩。没有自动选赢家。</p><ul>"
    )
    html += "".join(f"<li>{escape(w)}</li>" for w in audit["warnings"]) + "</ul>"
    html += (
        f"<h2>{escape(windows[focus_window][0])} 至 {escape(str(panel.dates[-1]))}"
        " 的回顾性比较</h2>"
        "<p>期初全现金，不继承此前持仓；此前数据仅用于指标预热。"
        "首笔模拟成交必须晚于区间内收盘信号。</p><div class='scroll'>"
        + shown.round(2).to_html(index=False, na_rep="缺")
        + "</div>"
    )
    html += (
        "<h2>当前量价观察状态</h2><p>冻结支点与口袋低点是复权研究结构字段，"
        "不是买卖价格。RPS不参与以下排序。</p><div class='scroll'>"
    )
    html += observed.head(40).round(3).to_html(index=False, na_rep="缺") + "</div>"
    html += (
        "<p><a href='comparison.csv'>全部年度对照</a> · "
        "<a href='event_chain.csv'>冻结事件链</a> · "
        "<a href='observations.csv'>全部观察状态</a> · "
        "<a href='audit.json'>假设与审计</a></p>"
    )
    html += f"<p>事件漏斗：{escape(json.dumps(funnel, ensure_ascii=False))}</p></html>"
    (out / "report.html").write_text(html, encoding="utf-8")
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
