"""
================================================================================
早期启涨过滤器 (Layer 1.5) — "提前 2-3 周识别火车轨候选"
================================================================================
设计目标:
    在 RPS_120 / 火车轨亮灯之前，捕捉刚开始启动的票。基于 22 只 20260417
    火车轨入选股的逆向画像 (analyze_huoche_picks.py)：
      - 启涨距今中位数 13 天 (火车轨滞后 ~ 2.5 周)
      - 启涨前 20 日有缩量盘整 (pre_vol_ratio ~ 0.92)
      - 启涨日量能温和 (1.0~1.5x VOL_MA60)
      - 段内通常有 1 个放量突破日 (>=1.5x VOL_MA60, 涨幅 >=4%)
      - 启涨距 MA60 偏离仍较小 (今天偏离 ~ 39%, 启涨日时一般 < 20%)

5 条件 (默认严格档):
    EL1: RPS_120 在 [rps_min, rps_max] (默认 70-93, 排除已"涨高"的票)
    EL2: close > MA20, 且最近 (close > MA20) 持续天数 ∈ [3, 12]
    EL3: 该启涨段累计涨幅 ∈ [seg_ret_min, seg_ret_max] (默认 8% ~ 28%)
    EL4: 段内至少 1 个放量突破日 (vol >= bo_vol_x * VOL_MA60 且 涨幅 >= bo_pct_min)
    EL5: close vs MA60 偏离 ≤ vs_ma60_max (默认 25%)，控位

输出:
    通过 EL_ALL 的票 + 启涨日 + 累计涨幅 + 放量倍数等明细
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class EarlyLaunchConfig:
    """
    默认参数已通过反向回放校准 (verify_early_launch.py, 20260417):
    对 22 只火车轨入选股, D+7 召回率 68%, any_hit 73%.
    去掉 RPS 上限 (RPS 反应快, 启涨后 5 天就飙到 95+, 不能用作"早期"上限).
    """
    rps_min: float = 70.0
    rps_max: float = 100.0
    above_ma20_min_days: int = 3
    above_ma20_max_days: int = 12
    seg_ret_min: float = 0.05
    seg_ret_max: float = 0.45
    bo_vol_x: float = 1.3
    bo_pct_min: float = 0.025
    vs_ma60_max: float = 0.40
    near_high_period: int = 60
    near_high_min: float = 0.85
    pre_vol_lookback: int = 20
    pre_vol_ratio_max: float = 1.30
    min_amount: float = 5e7


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    g = df.groupby("ts_code", sort=False)
    for p in [5, 10, 20, 60]:
        df[f"MA{p}"] = g["close"].transform(lambda x: x.rolling(p, min_periods=p).mean())
    df["VOL_MA60"] = g["vol"].transform(lambda x: x.rolling(60, min_periods=30).mean())
    df["pct"] = g["close"].pct_change()
    return df


def _streak_above_ma20(closes: np.ndarray, ma20: np.ndarray) -> int:
    """从末尾倒数，连续 close > MA20 的天数"""
    n = len(closes)
    cnt = 0
    for i in range(n - 1, -1, -1):
        if pd.notna(ma20[i]) and closes[i] > ma20[i]:
            cnt += 1
        else:
            break
    return cnt


def _scan_one(sdf: pd.DataFrame, target_dt: pd.Timestamp,
              cfg: EarlyLaunchConfig) -> Optional[dict]:
    sdf = sdf[sdf["trade_date"] <= target_dt]
    if len(sdf) < 90:
        return None
    sdf = sdf.reset_index(drop=True)
    today = sdf.iloc[-1]

    # 个股必须在目标日有有效行情。停牌、退市等缺少目标日记录的股票，
    # 不能用更早的最后一条历史记录冒充当日信号。
    if pd.Timestamp(today["trade_date"]) != target_dt:
        return None

    # 数据完整性
    if pd.isna(today.get("RPS_120")) or pd.isna(today.get("MA60")) or pd.isna(today.get("MA20")):
        return None
    if today.get("amount", 0) < cfg.min_amount:
        return None

    # EL1: RPS 区间
    rps = today["RPS_120"]
    el1 = cfg.rps_min <= rps <= cfg.rps_max

    # EL2: close > MA20 持续天数
    closes = sdf["close"].values
    ma20 = sdf["MA20"].values
    streak = _streak_above_ma20(closes, ma20)
    el2 = (cfg.above_ma20_min_days <= streak <= cfg.above_ma20_max_days)

    # 启涨段切片 (最近 streak 天，含起点前一日作为基准)
    seg_start = len(sdf) - streak
    if seg_start < 1:
        return None
    seg_base_close = sdf.iloc[seg_start - 1]["close"]
    seg_ret = today["close"] / seg_base_close - 1
    el3 = (cfg.seg_ret_min <= seg_ret <= cfg.seg_ret_max)

    # EL4: 段内放量突破日
    seg = sdf.iloc[seg_start:]
    bo_mask = (
        (seg["vol"] >= cfg.bo_vol_x * seg["VOL_MA60"])
        & (seg["pct"] >= cfg.bo_pct_min)
    )
    bo_count = int(bo_mask.sum())
    el4 = bo_count >= 1
    bo_max_vol_x = float((seg["vol"] / seg["VOL_MA60"]).max()) if len(seg) else np.nan

    # EL5: 控位
    vs_ma60 = today["close"] / today["MA60"] - 1
    el5 = vs_ma60 <= cfg.vs_ma60_max

    # 辅助：近 N 日新高比
    nh_window = sdf.tail(cfg.near_high_period)
    nh_max = nh_window["high"].max()
    near_high_ratio = today["close"] / nh_max if nh_max > 0 else 0
    el6 = near_high_ratio >= cfg.near_high_min

    # 辅助：启涨前缩量
    if seg_start >= cfg.pre_vol_lookback + 60:
        pre = sdf.iloc[seg_start - cfg.pre_vol_lookback:seg_start]
        pre_base = sdf.iloc[seg_start - cfg.pre_vol_lookback - 60:seg_start - cfg.pre_vol_lookback]
        pre_ratio = pre["vol"].mean() / pre_base["vol"].mean() if pre_base["vol"].mean() > 0 else np.nan
    else:
        pre_ratio = np.nan
    el7 = pd.isna(pre_ratio) or pre_ratio <= cfg.pre_vol_ratio_max

    el_all = el1 and el2 and el3 and el4 and el5 and el6 and el7

    rec = {
        "ts_code": today["ts_code"],
        "trade_date": today["trade_date"],
        "close": today["close"],
        "RPS_120": rps,
        "streak_above_ma20": streak,
        "seg_ret": seg_ret,
        "bo_count": bo_count,
        "bo_max_vol_x": bo_max_vol_x,
        "vs_ma60": vs_ma60,
        "near_high_ratio": near_high_ratio,
        "pre_vol_ratio": pre_ratio,
        "EL1_rps": el1,
        "EL2_streak": el2,
        "EL3_seg_ret": el3,
        "EL4_bo_volume": el4,
        "EL5_pos": el5,
        "EL6_near_high": el6,
        "EL7_pre_quiet": el7,
        "EL_ALL": el_all,
    }
    if "RPS_50" in today.index and not pd.isna(today.get("RPS_50")):
        rec["RPS_50"] = today["RPS_50"]
    if "RPS_250" in today.index and not pd.isna(today.get("RPS_250")):
        rec["RPS_250"] = today["RPS_250"]
    return rec


def screen_early_launch(
    df_daily: pd.DataFrame,
    target_date: Optional[str] = None,
    config: Optional[EarlyLaunchConfig] = None,
    candidate_codes: Optional[list] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    全市场扫描早期启涨候选。

    参数
    ----
    df_daily : 全市场日线 (含 RPS_120 列)
    target_date : YYYYMMDD, None 取最新
    config : EarlyLaunchConfig
    candidate_codes : 限定股票池 (None=全市场)
    """
    cfg = config or EarlyLaunchConfig()
    df = df_daily
    if candidate_codes:
        df = df[df["ts_code"].isin(candidate_codes)].copy()

    df = _add_indicators(df)

    if target_date is None:
        target_dt = df["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)
        valid = df["trade_date"].unique()
        valid = valid[valid <= td]
        if len(valid) == 0:
            if verbose:
                print(f"[早期启涨] {target_date} 之前无数据")
            return pd.DataFrame()
        target_dt = pd.Timestamp(valid.max())

    if verbose:
        print(f"[早期启涨] 目标日: {target_dt.strftime('%Y-%m-%d')}, 股票池: {df['ts_code'].nunique()}")
        print(
            f"[早期启涨] 参数: RPS∈[{cfg.rps_min},{cfg.rps_max}]  "
            f"上MA20{cfg.above_ma20_min_days}-{cfg.above_ma20_max_days}天  "
            f"段涨幅 [{cfg.seg_ret_min:.0%},{cfg.seg_ret_max:.0%}]  "
            f"vs MA60≤{cfg.vs_ma60_max:.0%}  放量≥{cfg.bo_vol_x:.1f}x"
        )

    records = []
    for code, sdf in df.groupby("ts_code", sort=False):
        rec = _scan_one(sdf, target_dt, cfg)
        if rec is not None:
            records.append(rec)

    if not records:
        if verbose:
            print("[早期启涨] 无可计算样本")
        return pd.DataFrame()

    sigs = pd.DataFrame(records)
    cond_cols = ["EL1_rps", "EL2_streak", "EL3_seg_ret", "EL4_bo_volume",
                 "EL5_pos", "EL6_near_high", "EL7_pre_quiet"]
    if verbose:
        total = len(sigs)
        for c in cond_cols:
            n = int(sigs[c].sum())
            print(f"  {c}: {n}/{total} ({n/total*100:.1f}%)")
        all_n = int(sigs["EL_ALL"].sum())
        print(f"  EL_ALL (7条 AND): {all_n}/{total} ({all_n/total*100:.1f}%)")

    passed = sigs[sigs["EL_ALL"]].copy()
    # 排序：低位 (vs_ma60 小) + RPS 高 + 启涨更早 (streak 大)
    passed["_score"] = (
        -passed["vs_ma60"]
        + passed["RPS_120"] / 100
        + passed["streak_above_ma20"] / 20
    )
    passed = passed.sort_values("_score", ascending=False).drop(columns=["_score"])
    return passed.reset_index(drop=True)


def print_early_launch_summary(result: pd.DataFrame, top_n: int = 30) -> None:
    if result.empty:
        print("  无早期启涨候选")
        return
    print(f"\n  早期启涨候选: {len(result)} 只 (前 {min(top_n, len(result))} 只展示)")
    show = result.head(top_n)[[
        "ts_code", "close", "RPS_120", "streak_above_ma20",
        "seg_ret", "bo_count", "bo_max_vol_x",
        "vs_ma60", "near_high_ratio",
    ]].copy()
    show["seg_ret"] = (show["seg_ret"] * 100).round(1)
    show["vs_ma60"] = (show["vs_ma60"] * 100).round(1)
    show["near_high_ratio"] = (show["near_high_ratio"] * 100).round(1)
    show["RPS_120"] = show["RPS_120"].round(1)
    show["bo_max_vol_x"] = show["bo_max_vol_x"].round(2)
    show.columns = ["代码", "收盘", "RPS120", "上MA20天",
                    "段涨幅%", "放量天", "段内最大量比",
                    "vs60线%", "近高%"]
    print(show.to_string(index=False))
