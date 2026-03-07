from pathlib import Path

import pandas as pd
import pytest


def test_to_tdx_blk_code_converts_sh_and_sz():
    from csv_to_tdx_blk import to_tdx_blk_code

    assert to_tdx_blk_code("600519.SH") == "1600519"
    assert to_tdx_blk_code("300164.SZ") == "0300164"


def test_default_block_name_uses_rps_prefix():
    from csv_to_tdx_blk import default_block_name

    assert default_block_name(Path("output/tdx_screener_20260305.csv")) == "RPS_20260305"


def test_convert_csv_to_blk_writes_unique_codes(tmp_path):
    from csv_to_tdx_blk import convert_csv_to_blk

    csv_path = tmp_path / "tdx_screener_20260305.csv"
    out_dir = tmp_path / "blocknew"
    pd.DataFrame(
        {
            "ts_code": ["600519.SH", "300164.SZ", "600519.SH"],
            "close": [1, 2, 3],
        }
    ).to_csv(csv_path, index=False)

    blk_path, count = convert_csv_to_blk(csv_path, out_dir=out_dir)

    assert blk_path == out_dir / "RPS_20260305.blk"
    assert count == 2
    assert blk_path.read_text(encoding="gbk").splitlines() == ["1600519", "0300164"]


def test_convert_csv_to_blk_requires_ts_code(tmp_path):
    from csv_to_tdx_blk import convert_csv_to_blk

    csv_path = tmp_path / "bad.csv"
    pd.DataFrame({"code": ["600519.SH"]}).to_csv(csv_path, index=False)

    with pytest.raises(ValueError, match="ts_code"):
        convert_csv_to_blk(csv_path, out_dir=tmp_path)
