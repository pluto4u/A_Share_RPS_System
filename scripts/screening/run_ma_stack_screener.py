"""把指定日期收盘价站上 MA5/MA10/MA20 的股票代码导出到一个文本文件。

数据源为本地通达信 ``vipdoc/{sh,sz}/lday/*.day``，未复权原始价。

用法示例：
    python run_ma_stack_screener.py
    python run_ma_stack_screener.py --date 20260814
    python run_ma_stack_screener.py --date 20260814 --code-format tdx --out output/ma_stack.txt
"""

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

from rps_analyzer.codes_csv import read_codes_csv
from rps_analyzer.ma_stack_screener import MaStackConfig, screen_ma_stack
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from run_ma20_screener import DEFAULT_HQ_CACHE, _is_risk_name, _load_tdx_name_master


def _format_code(ts_code: str, style: str) -> str:
    """plain=600519；ts=600519.SH；tdx=1600519（通达信自选股/板块格式）。"""
    code, _, market = ts_code.strip().upper().partition(".")
    if style == "plain":
        return code
    if style == "ts":
        return f"{code}.{market}"
    if style == "tdx":
        prefix = {"SH": "1", "SZ": "0", "BJ": "2"}.get(market)
        if prefix is None:
            raise ValueError(f"不支持的市场后缀: {ts_code}")
        return prefix + code
    raise ValueError(f"不支持的代码格式: {style}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="收盘价站上 MA5/MA10/MA20 选股（本地通达信日线）")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 或 sh/sz lday 路径")
    parser.add_argument("--date", default=None, help="目标日期 YYYYMMDD；非交易日自动回退，默认最新可靠交易日")
    parser.add_argument("--markets", default="sh,sz", help="读取的市场，逗号分隔")
    parser.add_argument("--ma", default="5,10,20", help="均线周期，逗号分隔")
    parser.add_argument("--min-days", type=int, default=60, help="读取时要求的最少历史日线数")
    parser.add_argument("--min-amount", type=float, default=0.0, help="20 日平均成交额下限（元），默认不过滤")
    parser.add_argument("--require-ma-order", action="store_true", help="额外要求 MA5>MA10>MA20 多头排列")
    parser.add_argument("--exclude-boards", default=None, help="排除代码前缀，逗号分隔，例如 688,300")
    parser.add_argument("--exclude-codes", default=None, help="排除代码清单 CSV")
    parser.add_argument("--include-st", action="store_true", help="允许 ST/退市名称；默认从本地名称表排除")
    parser.add_argument("--keep-ex-rights", action="store_true", help="保留窗口内疑似除权的股票（其均线不可靠）")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE), help="通达信本地证券名称目录")
    parser.add_argument("--code-format", default="plain", choices=("plain", "ts", "tdx"), help="文本内代码格式")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--out", default=None, help="代码文本路径；默认 <output-dir>/ma_stack_<日期>.txt")
    parser.add_argument("--no-detail", action="store_true", help="只写代码文本，不写明细 CSV 与审计 JSON")
    return parser


def _parse_periods(raw: str) -> tuple[int, ...]:
    periods = tuple(sorted({int(value.strip()) for value in raw.split(",") if value.strip()}))
    if not periods or any(period < 1 for period in periods):
        raise ValueError(f"均线周期非法: {raw}")
    return periods


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    periods = _parse_periods(args.ma)
    markets = [value.strip().lower() for value in args.markets.split(",") if value.strip()]

    print("读取本地通达信个股日线 ...")
    daily = load_tdx_market(args.tdx_dir, markets=markets, min_days=args.min_days, verbose=True)

    name_master = _load_tdx_name_master(Path(args.hq_cache))
    exclude_codes = set(read_codes_csv(args.exclude_codes)) if args.exclude_codes else set()
    risk_codes = {code for code, name in name_master.items() if _is_risk_name(name)}
    if not args.include_st:
        exclude_codes.update(risk_codes)
    exclude_prefixes = None
    if args.exclude_boards:
        exclude_prefixes = [value.strip() for value in args.exclude_boards.split(",") if value.strip()]

    config = MaStackConfig(
        ma_periods=periods,
        require_ma_order=args.require_ma_order,
        min_amount20=args.min_amount,
        ex_rights_guard=not args.keep_ex_rights,
    )
    result = screen_ma_stack(
        daily,
        target_date=args.date,
        config=config,
        exclude_codes=exclude_codes,
        exclude_prefixes=exclude_prefixes,
        verbose=True,
    )
    result.insert(2, "name", result["ts_code"].map(name_master).fillna(""))
    audit = result.attrs["audit"]
    audit["name_master_size"] = len(name_master)
    source_codes = set(daily["ts_code"].astype(str).str.upper().unique())
    audit["risk_name_codes_excluded"] = 0 if args.include_st else len(risk_codes & source_codes)
    audit["code_format"] = args.code_format

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(audit["target_date"]).replace("-", "")
    txt_path = Path(args.out) if args.out else output_dir / f"ma_stack_{stamp}.txt"
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    codes = [_format_code(code, args.code_format) for code in result["ts_code"]]
    # 通达信读取自定义板块用 GBK，纯数字代码在两种编码下字节一致
    txt_path.write_text("\n".join(codes) + ("\n" if codes else ""), encoding="utf-8")
    written = [txt_path]

    if not args.no_detail:
        detail_path = output_dir / f"ma_stack_{stamp}.csv"
        result.to_csv(detail_path, index=False, encoding="utf-8-sig", float_format="%.4f")
        audit_path = output_dir / f"ma_stack_{stamp}_audit.json"
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        written.extend([detail_path, audit_path])

    label = "/".join(f"MA{period}" for period in periods)
    print(f"\n目标日 {audit['target_date']} 收盘价站上 {label} 的股票: {len(codes)} 只（未复权价）")
    if not result.empty:
        preview_columns = ["ts_code", "name", "close"] + [f"ma{period}" for period in periods]
        preview_columns += ["ma_order_ok", "amount20_yi"]
        print("\n成交额前 20 只:")
        print(result[preview_columns].head(20).to_string(index=False))

    print("\n输出文件:")
    for path in written:
        print(f"  {path}")
    print(f"总耗时: {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
