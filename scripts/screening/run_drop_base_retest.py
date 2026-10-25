"""Small daily review queue; full ordered pattern required, no arbitrary top-N."""

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
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from run_daily_rps import names_from_tnf
from trend_start.drop_base_retest import PatternConfig, pattern_events
from trend_start.panel import load_panel
from trend_start.research import sha256, source_manifest, validate_panel

ROOT = PROJECT_ROOT


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    as_of: str
    stage: str
    review_kind: str
    close: float
    ma20: float
    ma60: float
    drop_pct: float
    base_start: str
    base_end: str
    base_bars: int
    peak_date: str
    trough_date: str
    event_date: str
    review_date: str
    pivot: float
    floor: float
    width_pct: float
    dry_ratio: float
    breakout_volume_ratio: float
    volume_ratio5: float
    extension_pct: float
    RPS50: float | None
    RPS120: float | None
    RPS250: float | None
    source_file: str


def optional_number(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "output/daily_research_cache_20260930"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/drop_base_retest.json")
    parser.add_argument("--retest-mode", choices=["quiet", "loud", "both"], default="quiet")
    args = parser.parse_args()
    cfg = PatternConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    mp = args.snapshot / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    snapshot_hash = sha256(mp)
    previous = json.loads((args.snapshot / "panel_audit.json").read_text(encoding="utf-8"))
    tdx_root = Path(previous["tdx_root"])
    print("[VERIFY] frozen snapshot and local TDX", flush=True)
    if source_manifest(tdx_root) != manifest["signature"]["sources"]:
        raise ValueError("Local source changed; rebuild snapshot")
    for name, digest in manifest["artifacts"].items():
        if sha256(args.snapshot / name) != digest:
            raise ValueError(f"Changed artifact: {name}")
    for name, digest in manifest["signature"]["code"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError(f"Changed snapshot builder: {name}")
    panel = load_panel(args.snapshot)
    validate_panel(panel)
    with np.load(args.snapshot / "rps.npz", allow_pickle=False) as z:
        rps = {k: z[k] for k in ("RPS50", "RPS120", "RPS250")}
    names = names_from_tnf(tdx_root / "T0002/hq_cache")
    pool: list[dict[str, Any]] = []
    loud: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    event_chain: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    eligible = attempts = breaks = 0
    as_of = str(panel.dates[-1])
    for i, raw_code in enumerate(panel.codes):
        if i % 1000 == 0:
            print(f"[PATTERN] {i}/{len(panel.codes)}", flush=True)
        code, name = str(raw_code), names.get(str(raw_code), "缺")
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        reason = ""
        if len(cols) < 251:
            reason = "insufficient_history"
        elif cols[-1] != len(panel.dates) - 1:
            reason = "no_latest_traded_bar"
        elif "ST" in name.upper().replace(" ", "") or "退" in name:
            reason = "current_ST_or_delisting_name"
        if reason:
            diagnostics.append({"code": code, "name": name, "selected": False, "reason": reason})
            continue
        eligible += 1
        d = pd.DataFrame({k: panel.field(k)[i, cols].astype(float) for k in panel.data})
        scan_start = max(0, len(d) - 65)
        e = pattern_events(d, cfg, scan_start=scan_start)
        attempts += int(e.attempt.sum())
        breaks += int(e.breakout.sum())
        ma20, ma60 = d.close.rolling(20).mean(), d.close.rolling(60).mean()
        for t, row in e.iterrows():
            if (
                bool(row.breakout)
                or bool(row.quiet_touch)
                or bool(row.loud_touch)
                or bool(row.confirmed)
                or row.stage in ("形态失效", "事件过期")
            ):
                event_chain.append(
                    {
                        "code": code,
                        "name": name,
                        "date": str(panel.dates[cols[int(t)]]),
                        "event_date": str(panel.dates[cols[int(row.event_bar)]])
                        if row.event_bar >= 0
                        else None,
                        "stage": str(row.stage),
                        "pivot": row["pivot"],
                        "floor": row.floor,
                        "base_start": str(panel.dates[cols[int(row.start)]])
                        if pd.notna(row.start)
                        else None,
                        "volume_ratio5": row.volume_ratio5,
                    }
                )
        last = e.iloc[-1]
        recognized = bool(last.review)
        selected = recognized and (
            args.retest_mode == "both" or last.review_kind == args.retest_mode
        )
        diagnostics.append(
            {
                "code": code,
                "name": name,
                "selected": selected,
                "reason": "selected" if selected else str(last.stage),
                "review_kind": str(last.review_kind),
                "event_bar": int(last.event_bar),
            }
        )
        if last.event_bar >= 0 and last.stage == "放量突破后待回踩":
            waiting.append(
                {
                    "code": code,
                    "name": name,
                    "stage": str(last.stage),
                    "event_date": str(panel.dates[cols[int(last.event_bar)]]),
                }
            )
        if recognized:
            def own_date(t: float, own_cols: np.ndarray = cols) -> str:
                return str(panel.dates[own_cols[int(t)]])
            candidate = Candidate.model_validate(
                {
                    "code": code,
                    "name": name,
                    "as_of": as_of,
                    "stage": str(last.stage),
                    "review_kind": str(last.review_kind),
                    "close": float(d.close.iloc[-1]),
                    "ma20": float(ma20.iloc[-1]),
                    "ma60": float(ma60.iloc[-1]),
                    "drop_pct": float(last.drop_pct),
                    "base_start": own_date(last.start),
                    "base_end": own_date(last.end),
                    "base_bars": int(last.end - last.start + 1),
                    "peak_date": own_date(last.peak),
                    "trough_date": own_date(last.trough),
                    "event_date": own_date(last.event_bar),
                    "review_date": own_date(last.last_review_bar),
                    "pivot": float(last["pivot"]),
                    "floor": float(last.floor),
                    "width_pct": float(last.width_pct),
                    "dry_ratio": float(last.dry_ratio),
                    "breakout_volume_ratio": float(last.break_volume / last.quiet_volume),
                    "volume_ratio5": float(last.volume_ratio5),
                    "extension_pct": float((d.close.iloc[-1] / last["pivot"] - 1) * 100),
                    **{k: optional_number(v[i, -1]) for k, v in rps.items()},
                    "source_file": str(
                        tdx_root
                        / "vipdoc"
                        / code[-2:].lower()
                        / "lday"
                        / f"{code[-2:].lower()}{code[:6]}.day"
                    ),
                }
            ).model_dump()
            if selected:
                pool.append(candidate)
            if last.review_kind == "loud":
                loud.append(candidate)
        # Primary queue history, plus explicit case history (not an admission override).
        if selected or code == "688536.SH":
            for t in range(max(0, len(d) - 100), len(d)):
                history.append(
                    {
                        "code": code,
                        "name": name,
                        "date": str(panel.dates[cols[t]]),
                        "close": float(d.close.iloc[t]),
                        "ma20": float(ma20.iloc[t]),
                        "ma60": float(ma60.iloc[t]),
                        "volume_wan": float(d.vol.iloc[t]) / 10000,
                        "stage": str(e.loc[t, "stage"]) if t in e.index else "未在本次事件窗口评估",
                    }
                )
    if sha256(mp) != snapshot_hash or source_manifest(tdx_root) != manifest["signature"]["sources"]:
        raise RuntimeError("Sources changed during run; no completed output")
    args.output.mkdir(parents=True, exist_ok=False)
    columns = list(Candidate.model_fields)
    for name, records in (("observations", pool), ("loud_retests", loud)):
        pd.DataFrame(records, columns=columns).to_csv(
            args.output / f"{name}.csv", index=False, encoding="utf-8-sig"
        )
    for name, records in (
        ("waiting", waiting),
        ("history", history),
        ("event_chain", event_chain),
        ("diagnostics", diagnostics),
    ):
        pd.DataFrame(records).to_csv(args.output / f"{name}.csv", index=False, encoding="utf-8-sig")
    totals = {
        "as_of": as_of,
        "valid_stocks": eligible,
        "trend_stocks": len(pool),
        "aligned_stocks": len(loud),
        "waiting_stocks": len(waiting),
        "price_attempts_65bars": attempts,
        "breakout_events_65bars": breaks,
        "snapshot_excluded": previous["excluded"],
        "retest_mode": args.retest_mode,
    }
    stages = dict(Counter(r["stage"] for r in pool))
    sources = {
        "name": "本地通达信日K：有序事件链",
        "files": [str((args.snapshot / "panel.npz").resolve()), str(mp.resolve())],
        "evidenceFlow": [
            {
                "title": "复现",
                "detail": (
                    f"py -3.12 run_drop_base_retest.py --retest-mode {args.retest_mode} "
                    "--output <new-folder>"
                ),
            },
            {"title": "规则", "detail": json.dumps(cfg.model_dump(), ensure_ascii=False)},
        ],
        "metricDefinitions": [
            {
                "label": "入列",
                "definition": "按时间完成大跌、缩量横盘、放量突破、近期有效回踩；"
                "不是今天MA向上名单",
            },
            {
                "label": "基准",
                "definition": "平台10至30根中选最长合格段，突破当日冻结；放量对比平台最后5根均量",
            },
            {
                "label": "默认",
                "definition": "缩量回踩或其后日线再转强；放量回踩单列，不默认解释为承接",
            },
            {
                "label": "刷新",
                "definition": "最后回踩/再转强距今<=3根交易日线，事件<=15根，"
                "现价位于冻结支点-2%至+8%",
            },
        ],
        "caveats": [
            "固定探索性阈值，未证明盈利；没有买卖指令。",
            "当前样本、股本未调整量、历史ST/退市覆盖与特殊复权排除仍有限。",
            "不是新收益回测；历史图含当前筛选，不能估计策略收益。",
        ],
    }
    snapshot = {
        "title": "大跌—缩量横盘—放量突破与回踩",
        "status": "ready",
        "asOf": as_of,
        "filters": [],
        "queries": {
            "trend_pool": {"rows": pool, "source": sources},
            "trend_history": {"rows": history, "source": sources},
            "trend_summary": {"rows": [totals], "source": sources},
            "pattern_loud": {"rows": loud, "source": sources},
            "pattern_waiting": {"rows": waiting, "source": sources},
            "pattern_events": {"rows": event_chain, "source": sources},
        },
    }
    (args.output / "reviewed_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    audit = {
        **totals,
        "stages": stages,
        "config": cfg.model_dump(),
        "snapshot_manifest": str(mp.resolve()),
        "snapshot_sha256": snapshot_hash,
        "source_code_hashes": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in (Path(__file__).resolve(), ROOT / "trend_start/drop_base_retest.py")
        },
        "warnings": sources["caveats"],
    }
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({**totals, "stages": stages}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
