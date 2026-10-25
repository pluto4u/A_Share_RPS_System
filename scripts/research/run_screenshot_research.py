"""CLI for the screenshot-to-industry-chain research workflow."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
from pathlib import Path

from industry_chain.database import DEFAULT_DB_PATH
from research_pipeline.screenshot_research import (
    DEFAULT_CLASSIFICATION_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_POOL_PATH,
    run_batch,
)
from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="截图证券池产业链与RPS研究")
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL_PATH)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--price-date", help="覆盖行情日期，格式YYYY-MM-DD")
    args = parser.parse_args()

    result = run_batch(
        pool_path=args.pool,
        classification_path=args.classification,
        db_path=args.db,
        tdx_dir=args.tdx_dir,
        output_dir=args.output_dir,
        price_target_date=args.price_date,
    )
    print(f"批次: {result['batch_id']}")
    print(f"行情日期: {result['price_date']}")
    print(f"去重证券: {result['member_count']}")
    print(f"产业链摆放: {result['placement_count']}")
    print(f"状态分布: {result['states']}")
    print(f"简报: {result['brief_path']}")
    print(f"详细报告: {result['details_path']}")
    print(f"全量数据: {result['csv_path']}")


if __name__ == "__main__":
    main()
