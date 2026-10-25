"""运行0721参考线潜在突破龙头筛选。"""

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
import time
from pathlib import Path

from rps_analyzer.codes_csv import write_codes_csv
from rps_analyzer.prebreak_leader import PrebreakLeaderConfig, screen_prebreak_leaders
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from run_ma20_screener import DEFAULT_HQ_CACHE, _is_risk_name, _load_tdx_name_master


def main() -> None:
    parser = argparse.ArgumentParser(description="0721收盘参考线潜在突破龙头")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--target-date", default="2026-08-06")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE))
    parser.add_argument("--include-st", action="store_true")
    args = parser.parse_args()

    started = time.time()
    daily = load_tdx_market(args.tdx_dir, min_days=125, verbose=True)
    names = _load_tdx_name_master(Path(args.hq_cache))
    risk_codes = {code for code, name in names.items() if _is_risk_name(name)}
    cfg = PrebreakLeaderConfig(target_date=args.target_date)
    result = screen_prebreak_leaders(
        daily,
        cfg,
        exclude_codes=None if args.include_st else risk_codes,
    )
    if not result.empty:
        result.insert(2, "name", result["ts_code"].map(names).fillna(""))
    audit = result.attrs.get("audit", {})
    audit["name_master_size"] = len(names)
    audit["risk_name_codes_excluded"] = (
        0 if args.include_st else len(risk_codes & set(daily["ts_code"].unique()))
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = args.target_date.replace("-", "")
    full_path = out / f"prebreak_leader_{stamp}.csv"
    leader_path = out / f"prebreak_leader_{stamp}_A.csv"
    codes_path = out / f"prebreak_leader_{stamp}_A_codes.csv"
    audit_path = out / f"prebreak_leader_{stamp}_audit.json"
    result.to_csv(full_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    leaders = result[result["leader_tier"].eq("A_技术龙胚")] if not result.empty else result
    leaders.to_csv(leader_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    write_codes_csv(codes_path, leaders["ts_code"] if "ts_code" in leaders else [])
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n结果摘要")
    for key in ("prebreak_candidates", "leader_tier", "potential_tier", "watch_tier"):
        print(f"  {key}: {audit.get(key, 0)}")
    if not result.empty:
        print("\nTop候选:")
        cols = [
            "ts_code", "name", "setup_type", "leader_tier", "leader_score",
            "gap_to_reference_pct", "rps20", "ma5_above_ma20_pct",
            "ma20_slope5_pct", "vol_contraction3", "trigger_price", "structure_stop",
        ]
        print(result.head(20)[cols].to_string(index=False))
    print("\n输出文件:")
    for path in (full_path, leader_path, codes_path, audit_path):
        print(f"  {path}")
    print(f"总耗时: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
