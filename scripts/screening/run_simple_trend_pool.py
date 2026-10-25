"""Read-only daily trend observation pool, not a trading or return model."""

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
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from run_daily_rps import names_from_tnf
from trend_start.panel import load_panel
from trend_start.research import sha256, source_manifest, validate_panel
from trend_start.simple_trend import TrendConfig, trend_state

ROOT = PROJECT_ROOT


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    as_of: str
    stage: str
    trend: bool
    aligned: bool
    close: float
    ma20: float
    past_ma20: float
    ma60: float
    bias20_pct: float
    volume_ratio5: float
    volume_change5: float
    reference_high: float
    breakout_flag: bool
    quiet_flag: bool
    pullback_flag: bool
    warning_flag: bool
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cfg = TrendConfig()
    manifest_path = args.snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    initial_hash = sha256(manifest_path)
    old_audit = json.loads((args.snapshot / "panel_audit.json").read_text(encoding="utf-8"))
    tdx_root = Path(old_audit["tdx_root"])
    print("[VERIFY] local sources and frozen artifacts", flush=True)
    if source_manifest(tdx_root) != manifest["signature"]["sources"]:
        raise ValueError("Local source changed; rebuild snapshot before refreshing this pool")
    for name, digest in manifest["artifacts"].items():
        if sha256(args.snapshot / name) != digest:
            raise ValueError(f"Changed snapshot artifact: {name}")
    for name, digest in manifest["signature"]["code"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError(f"Changed snapshot builder: {name}")
    panel = load_panel(args.snapshot)
    validate_panel(panel)
    with np.load(args.snapshot / "rps.npz", allow_pickle=False) as z:
        rps = {k: z[k] for k in ("RPS50", "RPS120", "RPS250")}
    names = names_from_tnf(tdx_root / "T0002/hq_cache")
    latest = str(panel.dates[-1])
    valid: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    for i, raw_code in enumerate(panel.codes):
        if i % 1000 == 0:
            print(f"[STATE] {i}/{len(panel.codes)}", flush=True)
        code = str(raw_code)
        name = names.get(code, "缺")
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        reason = ""
        if len(cols) < cfg.minimum_bars:
            reason = "insufficient_traded_history"
        elif cols[-1] != len(panel.dates) - 1:
            reason = "no_latest_traded_bar"
        elif "ST" in name.upper().replace(" ", "") or "退" in name:
            reason = "current_ST_or_delisting_name"
        if reason:
            exclusions.append({"code": code, "name": name, "reason": reason})
            continue
        d = pd.DataFrame({k: panel.field(k)[i, cols].astype(float) for k in panel.data})
        state = trend_state(d, cfg)
        last = state.iloc[-1]
        source_path = tdx_root / "vipdoc" / code[-2:].lower() / "lday"
        source_path = source_path / f"{code[-2:].lower()}{code[:6]}.day"
        observation = Observation.model_validate(
            {
                "code": code,
                "name": name,
                "as_of": latest,
                "close": float(d.close.iloc[-1]),
                **last.to_dict(),
                **{k: optional_number(v[i, -1]) for k, v in rps.items()},
                "source_file": str(source_path),
            }
        ).model_dump()
        valid.append(observation)
        if not observation["trend"]:
            continue
        pool.append(observation)
        for t in range(max(0, len(d) - 80), len(d)):
            history.append(
                {
                    "code": code,
                    "name": name,
                    "date": str(panel.dates[cols[t]]),
                    "close": float(d.close.iloc[t]),
                    "ma20": float(state.ma20.iloc[t]),
                    "ma60": float(state.ma60.iloc[t]),
                    "volume_wan": float(d.vol.iloc[t]) / 10000,
                    "stage": str(state.stage.iloc[t]),
                }
            )
    if (
        sha256(manifest_path) != initial_hash
        or source_manifest(tdx_root) != manifest["signature"]["sources"]
    ):
        raise RuntimeError("Sources changed during calculation; no completed output written")
    counts = Counter(r["stage"] for r in pool)
    totals = {
        "as_of": latest,
        "valid_stocks": len(valid),
        "trend_stocks": len(pool),
        "aligned_stocks": sum(bool(r["aligned"]) for r in pool),
        "excluded_current": len(exclusions),
        "snapshot_excluded": old_audit["excluded"],
        "missing_names": sum(r["name"] == "缺" for r in valid),
    }
    out = args.output or ROOT / "output" / f"simple_trend_pool_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    for name, records in (
        ("observations", pool),
        ("valid_universe", valid),
        ("history", history),
        ("exclusions", exclusions),
    ):
        pd.DataFrame(records).to_csv(out / f"{name}.csv", index=False, encoding="utf-8-sig")
    warnings = [
        "这是当前观察池，不是买卖指令、启动策略回测或盈利证明。",
        "历史ST、退市覆盖缺失；本地快照排除特殊复权事件，不能称为完整历史全市场。",
        "价格为前复权研究价格；量为未做股本调整的原始成交量，单位股。",
        "量价标签为透明的描述规则；回踩观察不等于成功回踩冻结支点。",
        "图表只展示当前入池股票的最近80根交易日线，不能用于证明历史筛选收益。",
    ]
    source = {
        "name": "本地通达信日K／冻结研究快照",
        "files": [str((args.snapshot / "panel.npz").resolve()), str(manifest_path.resolve())],
        "evidenceFlow": [
            {"title": "本地来源核对", "detail": f"源目录 {tdx_root}; 快照 {initial_hash}"},
            {"title": "复现", "detail": "py -3.12 run_simple_trend_pool.py --output <new-folder>"},
            {
                "title": "口径",
                "detail": "最低251根日线，最新日有成交，剔除当前ST/退市名称；不筛RPS或成交额。",
            },
        ],
        "metricDefinitions": [
            {"label": "趋势入池", "definition": "收盘>MA20 且 MA20>5根股票交易日线前的MA20"},
            {"label": "结构较强", "definition": "入池且MA20>MA60；分组字段，不是额外剔除条件"},
            {"label": "量比5", "definition": "今日成交量／此前5根均量，分母不包含今日"},
            {"label": "量能5对5", "definition": "最近5根均量／紧邻此前5根均量"},
            {"label": "阶段优先级", "definition": "转弱警示>突破扩展>回踩观察>缩量整理>趋势延续"},
            {
                "label": "转弱警示",
                "definition": "收盘低于此前5根最低价且成交量高于此前5根均量；仍可在趋势池",
            },
            {
                "label": "突破扩展",
                "definition": "收盘超过此前10根最高价；是否放量由量比单独描述，不硬性否决",
            },
            {
                "label": "回踩观察",
                "definition": "较昨日收盘下降且量低于此前5根均量；仅趋势内缩量回落",
            },
            {"label": "缩量整理", "definition": "最近5根均量和高低价宽度均小于紧邻此前5根"},
            {
                "label": "RPS",
                "definition": "沿用冻结快照的有效本地股票排名；只展示，不参与准入、标签或默认排序",
            },
        ],
        "caveats": warnings,
    }
    snapshot = {
        "title": "日线趋势观察池",
        "status": "ready",
        "asOf": latest,
        "filters": [],
        "queries": {
            "trend_pool": {"rows": pool, "source": source},
            "trend_history": {"rows": history, "source": source},
            "trend_summary": {"rows": [totals], "source": source},
            "trend_stages": {
                "rows": [{"stage": k, "count": v} for k, v in counts.items()],
                "source": source,
            },
        },
    }
    (out / "reviewed_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    audit = {
        **totals,
        "stages": dict(counts),
        "config": cfg.model_dump(),
        "snapshot_sha256": initial_hash,
        "snapshot_manifest": str(manifest_path.resolve()),
        "source_code_hashes": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in (Path(__file__).resolve(), ROOT / "trend_start/simple_trend.py")
        },
        "warnings": warnings,
    }
    (out / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(totals, ensure_ascii=False), flush=True)
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
