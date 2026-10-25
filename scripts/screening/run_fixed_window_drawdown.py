"""运行 2026 年 7 月最大回撤 / 8 月 6 日修复策略。"""

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
    screen_fixed_window_drawdown,
    screen_reference_high_breakout,
)
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from run_ma20_screener import DEFAULT_HQ_CACHE, _is_risk_name, _load_tdx_name_master


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="固定窗口最大回撤修复选股")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE))
    parser.add_argument("--window-start", default="2026-07-01")
    parser.add_argument("--window-end", default="2026-07-31")
    parser.add_argument("--reference-date", default="2026-07-21")
    parser.add_argument("--target-date", default="2026-08-06")
    parser.add_argument("--min-drawdown", type=float, default=0.30)
    parser.add_argument("--max-drawdown", type=float, default=0.45)
    parser.add_argument("--include-st", action="store_true")
    return parser


def _write_outputs(result, breakout, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(result.attrs["audit"]["target_date"]).replace("-", "")
    csv_path = output_dir / f"fixed_window_drawdown_{stamp}.csv"
    codes_path = output_dir / f"fixed_window_drawdown_{stamp}_codes.csv"
    audit_path = output_dir / f"fixed_window_drawdown_{stamp}_audit.json"
    recommended_path = output_dir / f"fixed_window_drawdown_{stamp}_RECOMMENDED.csv"
    recommended_codes_path = output_dir / f"fixed_window_drawdown_{stamp}_RECOMMENDED_codes.csv"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    write_codes_csv(codes_path, result["ts_code"] if "ts_code" in result else [])
    recommended = result[result["recommended"]] if "recommended" in result else result.iloc[0:0]
    recommended.to_csv(
        recommended_path,
        index=False,
        encoding="utf-8-sig",
        float_format="%.4f",
    )
    write_codes_csv(
        recommended_codes_path,
        recommended["ts_code"] if "ts_code" in recommended else [],
    )
    audit_path.write_text(
        json.dumps(result.attrs["audit"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    breakout_path = output_dir / f"reference_high_first_breakout_{stamp}.csv"
    breakout_codes_path = output_dir / f"reference_high_first_breakout_{stamp}_codes.csv"
    core_path = output_dir / f"reference_high_first_breakout_{stamp}_CORE.csv"
    core_codes_path = output_dir / f"reference_high_first_breakout_{stamp}_CORE_codes.csv"
    breakout_audit_path = output_dir / f"reference_high_first_breakout_{stamp}_audit.json"
    breakout.to_csv(breakout_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    write_codes_csv(breakout_codes_path, breakout["ts_code"] if "ts_code" in breakout else [])
    core = breakout[breakout["core_breakout"]] if "core_breakout" in breakout else breakout.iloc[0:0]
    core.to_csv(core_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    write_codes_csv(core_codes_path, core["ts_code"] if "ts_code" in core else [])
    breakout_audit_path.write_text(
        json.dumps(breakout.attrs["audit"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [
        csv_path, codes_path, recommended_path, recommended_codes_path, audit_path,
        breakout_path, breakout_codes_path, core_path, core_codes_path, breakout_audit_path,
    ]


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    config = FixedWindowDrawdownConfig(
        window_start=args.window_start,
        window_end=args.window_end,
        reference_date=args.reference_date,
        target_date=args.target_date,
        min_drawdown=args.min_drawdown,
        max_drawdown=args.max_drawdown,
    )
    print("读取本地通达信个股日线 ...")
    daily = load_tdx_market(args.tdx_dir, min_days=40, verbose=True)

    name_master = _load_tdx_name_master(Path(args.hq_cache))
    risk_codes = {code for code, name in name_master.items() if _is_risk_name(name)}
    result = screen_fixed_window_drawdown(
        daily,
        config,
        exclude_codes=None if args.include_st else risk_codes,
    )
    result.insert(2, "name", result["ts_code"].map(name_master).fillna(""))
    result.attrs["audit"]["name_master_size"] = len(name_master)
    result.attrs["audit"]["risk_name_codes_excluded"] = (
        0 if args.include_st else len(risk_codes & set(daily["ts_code"].unique()))
    )
    breakout = screen_reference_high_breakout(
        daily,
        config,
        exclude_codes=None if args.include_st else risk_codes,
    )
    breakout.insert(2, "name", breakout["ts_code"].map(name_master).fillna(""))
    breakout.attrs["audit"]["name_master_size"] = len(name_master)
    breakout.attrs["audit"]["risk_name_codes_excluded"] = (
        0 if args.include_st else len(risk_codes & set(daily["ts_code"].unique()))
    )
    written = _write_outputs(result, breakout, Path(args.output_dir))

    print("\n数据审计:")
    for key, value in result.attrs["audit"].items():
        if key != "config":
            print(f"  {key}: {value}")
    print("\n选股结果:")
    if result.empty:
        print("  没有符合四项原始条件的股票")
    else:
        display = [
            "ts_code", "name", "recommended", "close", "reference_high_20260721",
            "above_reference_high_pct", "ma5", "ma20", "july_max_drawdown_pct",
            "close_vs_ma20_pct", "ma20_slope5_pct", "ret5_pct", "amount20_yi",
            "drawdown_peak_date", "drawdown_trough_date", "correction_flags",
        ]
        print(result[display].to_string(index=False))

    print("\n0721最高价今日首次突破:")
    if breakout.empty:
        print("  没有首次突破股票")
    else:
        core = breakout[breakout["core_breakout"]]
        print(
            f"  全部首次突破: {len(breakout)}；核心S/A: {len(core)}；"
            f"S级: {(breakout['breakout_grade'] == 'S_原始回撤30-45').sum()}；"
            f"A级: {(breakout['breakout_grade'] == 'A_深跌回撤45-60').sum()}"
        )
        if not core.empty:
            display = [
                "ts_code", "name", "breakout_grade", "close", "prev_close",
                "reference_high_20260721", "above_reference_high_pct", "ma5", "ma20",
                "vol_ratio20", "amount20_yi", "july_max_drawdown_pct",
            ]
            print(core[display].to_string(index=False))
    print("\n输出文件:")
    for path in written:
        print(f"  {path}")
    print(f"总耗时: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
