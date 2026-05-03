"""
================================================================================
极强 RPS 榜 (Super Strong) — 抓"强者恒强"型牛股
================================================================================
在 L1 候选基础上再筛出 RPS_120 与 RPS_250 双双极高（默认 ≥98）、
且位置/段涨幅未过分追高的票。定位与 early launch 互补：

  - early launch  : 抓"刚启动"的准火车轨候选
  - super strong  : 抓"已经被市场长期证明很强、且未崩溃"的牛股

源杰科技 (688498.SH) 这类票在 1 月初 RPS120/250 就已 ≥99，本榜可
在 early 给出 buy 信号之前 1-2 月就将其纳入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class SuperStrongConfig:
    """极强 RPS 榜参数。

    Attributes
    ----------
    rps120_min, rps250_min : float
        RPS_120 / RPS_250 阈值（top 2% 默认 ≥98）。
    vs_ma60_max : float
        close/MA60 - 1 上限，控制不在过度延伸位置入榜。
    ret20_max : float
        20 日累计涨幅上限，避免抓刚加速完的票。
    min_amount : float
        当日成交额下限（元），过滤流动性差的票。
    """

    rps120_min: float = 98.0
    rps250_min: float = 98.0
    vs_ma60_max: float = 0.60
    ret20_max: float = 0.60
    min_amount: float = 5e7


def screen_super_strong(
    df_daily: pd.DataFrame,
    l1_codes: List[str],
    target_date: Optional[str] = None,
    config: Optional[SuperStrongConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    在 L1 候选基础上筛出极强 RPS 榜。

    参数
    ----
    df_daily : DataFrame
        全市场日线（含 RPS_120 / RPS_250 列）
    l1_codes : list[str]
        L1 候选 ts_code（仅在该列表内筛选）
    target_date : str, optional
        YYYYMMDD；None 取数据最新交易日。若该日无数据则回退最近一个交易日。
    config : SuperStrongConfig, optional
    verbose : bool

    返回
    ----
    DataFrame，按 RPS_120 / RPS_250 降序排序
    """
    cfg = config or SuperStrongConfig()

    if not l1_codes:
        if verbose:
            print("[极强榜] L1 候选为空, 跳过")
        return pd.DataFrame()

    sub = df_daily[df_daily["ts_code"].isin(l1_codes)].copy()
    if sub.empty:
        if verbose:
            print("[极强榜] L1 候选在数据中无对应行, 跳过")
        return pd.DataFrame()

    sub = sub.sort_values(["ts_code", "trade_date"])
    g = sub.groupby("ts_code", sort=False)
    sub["MA60"] = g["close"].transform(
        lambda x: x.rolling(60, min_periods=60).mean()
    )
    sub["ret20"] = g["close"].transform(lambda x: x / x.shift(20) - 1)

    if target_date is None:
        target_dt = sub["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)
        valid = sub[sub["trade_date"] <= td]["trade_date"].unique()
        if len(valid) == 0:
            if verbose:
                print(f"[极强榜] {target_date} 之前无数据")
            return pd.DataFrame()
        target_dt = pd.Timestamp(valid.max())

    today = sub[sub["trade_date"] == target_dt].copy()
    today["vs_ma60"] = today["close"] / today["MA60"] - 1

    amount_col = today["amount"] if "amount" in today.columns else 0

    cond = (
        (today["RPS_120"] >= cfg.rps120_min)
        & (today["RPS_250"] >= cfg.rps250_min)
        & (today["vs_ma60"] <= cfg.vs_ma60_max)
        & (today["ret20"] <= cfg.ret20_max)
        & (amount_col >= cfg.min_amount)
    )
    passed = today[cond].copy()
    passed = passed.sort_values(
        ["RPS_120", "RPS_250"], ascending=[False, False]
    ).reset_index(drop=True)

    if verbose:
        print(f"[极强榜] 目标日: {target_dt.strftime('%Y-%m-%d')}  L1 候选 {len(l1_codes)} 只")
        print(
            f"[极强榜] 参数: RPS120≥{cfg.rps120_min} RPS250≥{cfg.rps250_min} "
            f"vs MA60≤{cfg.vs_ma60_max:.0%}  ret20≤{cfg.ret20_max:.0%}"
        )
        print(f"[极强榜] 通过: {len(passed)} 只")

    return passed


def print_super_strong_summary(result: pd.DataFrame, top_n: int = 30) -> None:
    if result.empty:
        print("  无极强 RPS 榜入选")
        return
    show = result.head(top_n)[
        ["ts_code", "close", "RPS_120", "RPS_250", "ret20", "vs_ma60"]
    ].copy()
    show["ret20"] = (show["ret20"] * 100).round(1)
    show["vs_ma60"] = (show["vs_ma60"] * 100).round(1)
    show["RPS_120"] = show["RPS_120"].round(1)
    show["RPS_250"] = show["RPS_250"].round(1)
    show.columns = ["代码", "收盘", "RPS120", "RPS250", "ret20%", "vs60%"]
    print(f"\n  极强 RPS 榜 Top {len(show)} (共 {len(result)} 只):")
    print(show.to_string(index=False))
