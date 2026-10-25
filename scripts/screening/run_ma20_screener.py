"""从通达信本地个股日线运行 20 日均线选股。"""

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

import pandas as pd

from rps_analyzer.codes_csv import read_codes_csv, write_codes_csv
from rps_analyzer.ma20_screener import Ma20Config, screen_ma20
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market


DEFAULT_HQ_CACHE = Path(r"C:\new_tdx\T0002\hq_cache")


def _load_tdx_name_master(hq_cache: Path) -> dict[str, str]:
    """读取通达信本地证券名称表；名称只用于展示和排除 ST，不参与信号计算。"""
    result: dict[str, str] = {}
    for market, filename in (("SH", "shs.tnf"), ("SZ", "szs.tnf")):
        path = hq_cache / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        if len(raw) < 50 or (len(raw) - 50) % 360:
            continue
        for offset in range(50, len(raw), 360):
            record = raw[offset : offset + 360]
            code = record[:6].decode("ascii", errors="ignore")
            name = record[31:63].split(b"\0", 1)[0].decode("gbk", errors="ignore").strip()
            if code.isdigit() and name:
                result[f"{code}.{market}"] = name
    return result


def _is_risk_name(name: str) -> bool:
    compact = str(name).replace(" ", "").upper()
    return compact.startswith(("ST", "*ST")) or "退" in compact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="20 日均线趋势选股（本地通达信日线）")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 或 sh/sz lday 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD；周末自动回退，默认最新可靠交易日")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE), help="通达信本地证券名称目录")
    parser.add_argument("--min-days", type=int, default=80, help="读取时要求的最少历史日线数")
    parser.add_argument("--min-amount", type=float, default=8e7, help="20 日平均成交额下限，默认 8000 万元")
    parser.add_argument("--max-vs-ma20", type=float, default=0.06, help="收盘价高于 MA20 的最大乖离，默认 6%%")
    parser.add_argument("--exclude-boards", default=None, help="排除代码前缀，逗号分隔，例如 688,300")
    parser.add_argument("--exclude-codes", default=None, help="排除代码清单 CSV，例如 ST/退市股")
    parser.add_argument("--include-st", action="store_true", help="允许 ST/退市名称；默认从本地名称表排除")
    parser.add_argument("--candidates-only", action="store_true", help="只输出可交易候选，不输出趋势观察档")
    return parser


def _write_outputs(result: pd.DataFrame, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = result.attrs.get("audit", {})
    stamp = str(audit.get("target_date", "unknown")).replace("-", "")
    written: list[Path] = []

    full_path = output_dir / f"ma20_screener_{stamp}.csv"
    result.to_csv(full_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    written.append(full_path)

    for action, suffix in (("候选", "BUY"), ("观察", "WATCH")):
        part = result[result["action"] == action]
        if part.empty:
            continue
        part_path = output_dir / f"ma20_screener_{stamp}_{suffix}.csv"
        codes_path = output_dir / f"ma20_screener_{stamp}_{suffix}_codes.csv"
        part.to_csv(part_path, index=False, encoding="utf-8-sig", float_format="%.4f")
        write_codes_csv(codes_path, part["ts_code"])
        written.extend([part_path, codes_path])

    audit_path = output_dir / f"ma20_screener_{stamp}_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(audit_path)
    return written


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    print("读取本地通达信个股日线 ...")
    daily = load_tdx_market(args.tdx_dir, min_days=args.min_days, verbose=True)

    name_master = _load_tdx_name_master(Path(args.hq_cache))
    exclude_codes = set(read_codes_csv(args.exclude_codes)) if args.exclude_codes else set()
    risk_codes = {code for code, name in name_master.items() if _is_risk_name(name)}
    if not args.include_st:
        exclude_codes.update(risk_codes)
    exclude_prefixes = None
    if args.exclude_boards:
        exclude_prefixes = [value.strip() for value in args.exclude_boards.split(",") if value.strip()]

    config = Ma20Config(
        min_amount20=args.min_amount,
        max_vs_ma20=args.max_vs_ma20,
        min_bars=max(args.min_days, 80),
    )
    result = screen_ma20(
        daily,
        target_date=args.date,
        config=config,
        exclude_codes=exclude_codes,
        exclude_prefixes=exclude_prefixes,
        include_watch=not args.candidates_only,
        verbose=True,
    )
    result.insert(2, "name", result["ts_code"].map(name_master).fillna(""))
    result.attrs["audit"]["name_master_size"] = len(name_master)
    source_codes = set(daily["ts_code"].astype(str).str.upper().unique())
    result.attrs["audit"]["risk_name_codes_excluded"] = (
        0 if args.include_st else len(risk_codes & source_codes)
    )
    written = _write_outputs(result, Path(args.output_dir))

    print("\n选股摘要")
    if result.empty:
        print("  没有符合条件的股票")
    else:
        print(result.groupby(["action", "signal"]).size().to_string())
        candidates = result[result["action"] == "候选"].head(20)
        if not candidates.empty:
            print("\n前 20 个候选:")
            print(
                candidates[
                    ["ts_code", "name", "signal", "score", "close", "ma20", "vs_ma20_pct", "vol_ratio20"]
                ].to_string(index=False)
            )

    print("\n输出文件:")
    for path in written:
        print(f"  {path}")
    print(f"总耗时: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
