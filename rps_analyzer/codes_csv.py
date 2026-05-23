"""
股票代码清单 CSV — 单列、无表头（不写 ts_code 列名），便于通达信等工具导入。
读取兼容：无表头 / 旧版带 ts_code 或「代码」表头。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Union

import pandas as pd


def write_codes_csv(path: Union[str, Path], codes: Union[pd.Series, pd.DataFrame, Iterable[str]]) -> None:
    """写入单列代码，无 header（不出现 ts_code 列名）。"""
    path = Path(path)
    if isinstance(codes, pd.DataFrame):
        if "ts_code" in codes.columns:
            s = codes["ts_code"]
        else:
            s = codes.iloc[:, 0]
    elif isinstance(codes, pd.Series):
        s = codes
    else:
        s = pd.Series(list(codes))
    s = s.dropna().astype(str).str.strip()
    s = s[s != ""]
    s = s.drop_duplicates()
    s.to_csv(path, index=False, header=False, encoding="utf-8-sig")


def read_codes_csv(path: Union[str, Path]) -> List[str]:
    """读取代码清单：首列为代码；若首行是 ts_code/代码/code 则跳过。"""
    path = Path(path)
    if not path.exists():
        return []
    raw = pd.read_csv(path, header=None, encoding="utf-8-sig", dtype=str)
    if raw.empty or raw.shape[1] < 1:
        return []
    col0 = raw.iloc[:, 0].dropna().astype(str).str.strip()
    if len(col0) == 0:
        return []
    first = col0.iloc[0].lower()
    if first in ("ts_code", "代码", "code"):
        col0 = col0.iloc[1:]
    return col0[col0 != ""].tolist()
