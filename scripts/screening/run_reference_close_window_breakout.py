"""运行 8月3日至8月6日突破 7月21日收盘价的放宽策略。"""

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
from rps_analyzer.fixed_window_drawdown import (
    FixedWindowDrawdownConfig,
    screen_reference_close_window_breakout,
)
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from run_ma20_screener import DEFAULT_HQ_CACHE, _is_risk_name, _load_tdx_name_master


def main() -> None:
    parser = argparse.ArgumentParser(description="窗口内突破0721收盘价")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE))
    parser.add_argument("--breakout-start", default="2026-08-03")
    parser.add_argument("--target-date", default="2026-08-06")
    parser.add_argument("--include-st", action="store_true")
    args = parser.parse_args()

    started = time.time()
    daily = load_tdx_market(args.tdx_dir, min_days=60, verbose=True)
    names = _load_tdx_name_master(Path(args.hq_cache))
    risk_codes = {code for code, name in names.items() if _is_risk_name(name)}
    cfg = FixedWindowDrawdownConfig(target_date=args.target_date)
    result = screen_reference_close_window_breakout(
        daily,
        cfg,
        breakout_start=args.breakout_start,
        breakout_end=args.target_date,
        exclude_codes=None if args.include_st else risk_codes,
    )
    result.insert(2, "name", result["ts_code"].map(names).fillna(""))
    result.attrs["audit"]["name_master_size"] = len(names)
    result.attrs["audit"]["risk_name_codes_excluded"] = (
        0 if args.include_st else len(risk_codes & set(daily["ts_code"].unique()))
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = args.target_date.replace("-", "")
    prefix = f"reference_close_window_{args.breakout_start.replace('-', '')}_{stamp}"
    full_path = out / f"{prefix}.csv"
    core_path = out / f"{prefix}_CORE.csv"
    priority_path = out / f"{prefix}_PRIORITY.csv"
    codes_path = out / f"{prefix}_PRIORITY_codes.csv"
    strict_path = out / f"{prefix}_STRICT.csv"
    strict_priority_path = out / f"{prefix}_STRICT_PRIORITY.csv"
    strict_codes_path = out / f"{prefix}_STRICT_PRIORITY_codes.csv"
    audit_path = out / f"{prefix}_audit.json"
    result.to_csv(full_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    core = result[result["core_drawdown_30_60"]]
    priority = result[result["shape_verdict"].eq("A_形态优先")]
    core.to_csv(core_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    priority.to_csv(priority_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    write_codes_csv(codes_path, priority["ts_code"])
    strict = result[result["strict_original_pass"]]
    strict_priority = result[result["strict_priority"]]
    strict.to_csv(strict_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    strict_priority.to_csv(
        strict_priority_path, index=False, encoding="utf-8-sig", float_format="%.4f"
    )
    write_codes_csv(strict_codes_path, strict_priority["ts_code"])
    audit_path.write_text(
        json.dumps(result.attrs["audit"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    audit = result.attrs["audit"]
    print("\n结果摘要")
    for key in (
        "raw_window_breakouts", "quality_breakouts", "core_drawdown_breakouts",
        "shape_priority", "shape_observe", "shape_reject", "strict_original_pass",
        "strict_priority",
    ):
        print(f"  {key}: {audit[key]}")
    if not priority.empty:
        print("\n形态优先:")
        print(
            priority[
                [
                    "ts_code", "name", "break_date", "drawdown_grade", "breakout_pct",
                    "hold_above_reference_pct", "break_vol_ratio20", "close_vs_ma20_pct",
                    "trend_stage",
                ]
            ].to_string(index=False)
        )
    print("\n输出文件:")
    for path in (
        full_path, core_path, priority_path, codes_path, strict_path,
        strict_priority_path, strict_codes_path, audit_path,
    ):
        print(f"  {path}")
    print(f"总耗时: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
