"""
龙回头策略的股票池筛选 — 与 ETF 轮动完全独立

支持两种模式:
  - rps: 按 RPS_120、RPS_250 阈值筛选 (相对强度选股)
  - amount: 按成交额前 N 名筛选 (活跃度选股)
"""

import pandas as pd
from typing import List, Tuple


def get_pool_by_rps(
    df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    rps_min_120: float = 90.0,
    rps_min_250: float = 90.0,
    use_prev_day: bool = True,
) -> List[str]:
    """
    从已含 RPS 列的数据中，筛选高 RPS 股票池。

    use_prev_day: 若 True，用前一日 RPS 判断（回踩日多为阴线，当日 RPS 易回落）
    """
    if use_prev_day:
        # 用前一日 RPS，避免回踩阴线日把 RPS 打下去
        prev = df[df["trade_date"] < as_of_date]["trade_date"].max()
        if pd.isna(prev):
            check_date = as_of_date
        else:
            check_date = prev
    else:
        check_date = as_of_date

    day_df = df[df["trade_date"] == check_date].copy()
    if day_df.empty:
        return []
    mask = (day_df["RPS_120"] > rps_min_120) & (day_df["RPS_250"] > rps_min_250)
    return day_df.loc[mask, "ts_code"].unique().tolist()


def get_pool_by_amount(
    df: pd.DataFrame,
    top_n: int = 500,
    as_of_date: pd.Timestamp = None,
) -> List[str]:
    """
    按成交额均值取前 N 名活跃股。

    as_of_date 若提供，则只用该日期之前的数据计算。
    """
    if as_of_date is not None:
        df = df[df["trade_date"] <= as_of_date]
    if df.empty:
        return []
    top = df.groupby("ts_code")["amount"].mean().nlargest(top_n).index.tolist()
    return top


def get_universe_pool(
    df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    config: dict,
) -> Tuple[List[str], str]:
    """
    根据配置获取龙回头扫描的股票池。

    Returns:
        (pool: list of ts_code, mode: "rps"|"amount")
    """
    universe = config.get("universe", {})
    source = universe.get("source", "amount")

    if source == "rps":
        pool = get_pool_by_rps(
            df,
            as_of_date,
            rps_min_120=universe.get("rps_min_120", 90.0),
            rps_min_250=universe.get("rps_min_250", 90.0),
            use_prev_day=universe.get("rps_use_prev_day", True),
        )
        return pool, "rps"
    else:
        pool = get_pool_by_amount(
            df,
            top_n=universe.get("top_active_stocks", 500),
            as_of_date=as_of_date,
        )
        return pool, "amount"
