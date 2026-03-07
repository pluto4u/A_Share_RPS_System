"""
Convert screener CSV output into a TDX custom block `.blk` file.

Default behavior:
  - Discover the latest `output/tdx_screener_*.csv`
  - Write `RPS_YYYYMMDD.blk` to `D:\\tdx\\T0002\\blocknew`

Optional CLI overrides:
  --csv      input CSV path
  --name     block name without extension
  --out-dir  output directory
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_OUTPUT_DIR = Path(r"D:\tdx\T0002\blocknew")


def to_tdx_blk_code(ts_code: str) -> str:
    code, market = ts_code.strip().split(".")
    market = market.upper()
    if market == "SH":
        return "1" + code
    if market == "SZ":
        return "0" + code
    raise ValueError(f"unsupported ts_code market: {ts_code}")


def default_block_name(csv_path: Path) -> str:
    stem = csv_path.stem
    prefix = "tdx_screener_"
    if stem.startswith(prefix):
        return f"RPS_{stem[len(prefix):]}"
    return f"RPS_{stem}"


def find_latest_screener_csv(output_dir: Path = Path("output")) -> Path:
    candidates = sorted(output_dir.glob("tdx_screener_*.csv"))
    if not candidates:
        raise FileNotFoundError(f"no screener csv found under {output_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _unique_in_order(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def convert_csv_to_blk(
    csv_path: Path | str,
    out_dir: Path | str = DEFAULT_OUTPUT_DIR,
    block_name: str | None = None,
) -> tuple[Path, int]:
    csv_path = Path(csv_path)
    out_dir = Path(out_dir)

    if not csv_path.exists():
        raise FileNotFoundError(f"csv not found: {csv_path}")

    df = pd.read_csv(csv_path)
    if "ts_code" not in df.columns:
        raise ValueError("input csv must contain 'ts_code' column")

    ts_codes = [str(x).strip() for x in df["ts_code"].dropna().tolist() if str(x).strip()]
    blk_codes = _unique_in_order(to_tdx_blk_code(ts_code) for ts_code in ts_codes)

    out_dir.mkdir(parents=True, exist_ok=True)
    block_name = block_name or default_block_name(csv_path)
    blk_path = out_dir / f"{block_name}.blk"
    blk_path.write_text("\n".join(blk_codes), encoding="gbk")
    return blk_path, len(blk_codes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert screener CSV to TDX .blk")
    parser.add_argument("--csv", dest="csv_path", default=None, help="input screener csv path")
    parser.add_argument("--name", dest="block_name", default=None, help="output block name")
    parser.add_argument(
        "--out-dir",
        dest="out_dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="TDX block output directory",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    csv_path = Path(args.csv_path) if args.csv_path else find_latest_screener_csv()
    blk_path, count = convert_csv_to_blk(
        csv_path=csv_path,
        out_dir=Path(args.out_dir),
        block_name=args.block_name,
    )
    print(f"input: {csv_path}")
    print(f"output: {blk_path}")
    print(f"count: {count}")


if __name__ == "__main__":
    main()
