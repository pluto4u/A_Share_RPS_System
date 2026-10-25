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
    rps50_min_premium : float
        Premium 等级要求的 RPS_50 阈值（短期强势确认）。
    vs_ma60_premium : float
        Premium 等级要求的 close/MA60 - 1 上限（更严格的位置控制）。
    ret20_premium : float
        Premium 等级要求的 20 日累计涨幅上限（更严格）。
    """

    rps120_min: float = 98.0
    rps250_min: float = 98.0
    vs_ma60_max: float = 0.60
    ret20_max: float = 0.60
    min_amount: float = 5e7
    rps50_min_premium: float = 90.0
    vs_ma60_premium: float = 0.25
    ret20_premium: float = 0.30


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

    if not passed.empty:
        rps50 = passed["RPS_50"] if "RPS_50" in passed.columns else pd.Series(
            [float("nan")] * len(passed), index=passed.index
        )
        premium_mask = (
            (rps50 >= cfg.rps50_min_premium)
            & (passed["vs_ma60"] <= cfg.vs_ma60_premium)
            & (passed["ret20"] <= cfg.ret20_premium)
        )
        passed["tier"] = premium_mask.map({True: "premium", False: "extended"})

    passed = passed.sort_values(
        ["RPS_120", "RPS_250"], ascending=[False, False]
    ).reset_index(drop=True)

    if verbose:
        n_premium = int((passed["tier"] == "premium").sum()) if not passed.empty else 0
        print(f"[极强榜] 目标日: {target_dt.strftime('%Y-%m-%d')}  L1 候选 {len(l1_codes)} 只")
        print(
            f"[极强榜] 参数: RPS120≥{cfg.rps120_min} RPS250≥{cfg.rps250_min} "
            f"vs MA60≤{cfg.vs_ma60_max:.0%}  ret20≤{cfg.ret20_max:.0%}"
        )
        print(
            f"[极强榜] Premium 阈值: RPS50≥{cfg.rps50_min_premium} "
            f"vs MA60≤{cfg.vs_ma60_premium:.0%}  ret20≤{cfg.ret20_premium:.0%}"
        )
        print(f"[极强榜] 通过: {len(passed)} 只 (premium={n_premium}, extended={len(passed)-n_premium})")

    return passed


def print_super_strong_summary(result: pd.DataFrame, top_n: int = 30) -> None:
    if result.empty:
        print("  无极强 RPS 榜入选")
        return
    extra_cols = []
    if "tier" in result.columns:
        extra_cols.append("tier")
    if "RPS_50" in result.columns:
        extra_cols.insert(0, "RPS_50")

    base_cols = ["ts_code", "close"] + extra_cols + ["RPS_120", "RPS_250", "ret20", "vs_ma60"]
    show = result.head(top_n)[base_cols].copy()
    show["ret20"] = (show["ret20"] * 100).round(1)
    show["vs_ma60"] = (show["vs_ma60"] * 100).round(1)
    show["RPS_120"] = show["RPS_120"].round(1)
    show["RPS_250"] = show["RPS_250"].round(1)
    if "RPS_50" in show.columns:
        show["RPS_50"] = show["RPS_50"].round(1)
    rename = {
        "ts_code": "代码", "close": "收盘", "tier": "档",
        "RPS_50": "RPS50", "RPS_120": "RPS120", "RPS_250": "RPS250",
        "ret20": "ret20%", "vs_ma60": "vs60%",
    }
    show = show.rename(columns=rename)
    n_premium = int((result["tier"] == "premium").sum()) if "tier" in result.columns else 0
    print(f"\n  极强 RPS 榜 Top {len(show)} (共 {len(result)} 只, premium={n_premium}):")
    print(show.to_string(index=False))
