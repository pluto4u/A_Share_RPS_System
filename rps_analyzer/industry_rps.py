"""
================================================================================
行业 RPS — 板块强度排名
================================================================================
从通达信本地 tdxhy.cfg 读取个股行业归属，计算板块级别 RPS，
筛选"强行业中的强个股"。

数据来源: C:/new_tdx/T0002/hq_cache/tdxhy.cfg
格式: market|code|industry_code|||secondary_code
  market: 0=SZ, 1=SH, 2=BJ
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional


DEFAULT_INDUSTRY_CFG = Path(r"C:/new_tdx/T0002/hq_cache/tdxhy.cfg")


def load_industry_map(
    cfg_path: Path = DEFAULT_INDUSTRY_CFG,
) -> pd.DataFrame:
    """
    读取 tdxhy.cfg，返回 ts_code -> industry 映射表。

    返回 DataFrame: ts_code, industry (如 T0706)
    """
    records = []
    market_map = {"0": "SZ", "1": "SH", "2": "BJ"}

    with open(cfg_path, "r", encoding="gbk") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) < 3 or not parts[2]:
                continue
            mkt_code = parts[0]
            stock_code = parts[1]
            industry = parts[2]

            mkt = market_map.get(mkt_code)
            if mkt is None or mkt == "BJ":
                continue

            ts_code = f"{stock_code}.{mkt}"
            records.append({"ts_code": ts_code, "industry": industry})

    return pd.DataFrame(records)


def calculate_industry_rps(
    df: pd.DataFrame,
    industry_map: pd.DataFrame,
    period: int = 120,
) -> pd.DataFrame:
    """
    计算行业 RPS。

    逻辑:
      1. 合并个股数据与行业归属
      2. 按行业+日期聚合，取行业内个股的平均收益率
      3. 对行业平均收益率做跨行业百分位排名 -> IND_RPS

    返回: 原始 df 增加 industry, IND_RPS 列
    """
    merged = df.merge(industry_map, on="ts_code", how="left")
    merged = merged.dropna(subset=["industry"])

    merged[f"_roc_{period}"] = merged.groupby("ts_code")["close"].transform(
        lambda x: x.pct_change(periods=period)
    )

    ind_avg = merged.groupby(["trade_date", "industry"])[f"_roc_{period}"].mean().reset_index()
    ind_avg.rename(columns={f"_roc_{period}": "_ind_roc"}, inplace=True)

    ind_avg["IND_RPS"] = ind_avg.groupby("trade_date")["_ind_roc"].rank(pct=True) * 100

    result = merged.merge(
        ind_avg[["trade_date", "industry", "IND_RPS"]],
        on=["trade_date", "industry"],
        how="left",
    )
    result.drop(columns=[f"_roc_{period}"], inplace=True)

    return result


def filter_strong_industry(
    df: pd.DataFrame,
    ind_rps_threshold: float = 70.0,
    target_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    筛选处于强势行业（IND_RPS >= 阈值）的个股。

    返回满足条件的个股 DataFrame。
    """
    if "IND_RPS" not in df.columns:
        raise ValueError("df 缺少 IND_RPS 列，请先调用 calculate_industry_rps")

    if target_date is not None:
        target_date = pd.Timestamp(target_date)
        sub = df[df["trade_date"] == target_date]
    else:
        latest = df["trade_date"].max()
        sub = df[df["trade_date"] == latest]

    return sub[sub["IND_RPS"] >= ind_rps_threshold].copy()
