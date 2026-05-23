"""
================================================================================
顺向火车轨（ZHUTU）周线叠加过滤
================================================================================
在 L1 / L2 日线候选基础上，叠加 **周线 SXHCG** 5 条件过滤，等价于
docs/tdx_rps_extension_zhutu.md 中 `DRAWICON(PERIOD=5 AND BARSSINCEN(SXHCG,8)=0,...,13)`
所表达的"小圆球"信号语义。

输出列:
    ts_code, close, W_RPS_120, MA_120W,
    W_SXHCG1, W_SXHCG2, W_SXHCG3, W_SXHCG4, W_SXHCG,
    HUOCHE_FIRST_W

HUOCHE_FIRST_W = True  当周触发，且过去 N 周内首次（默认 8 周）

注意:
    - 周线 250 周 RPS 数据要求 ~5 年，A 股多数票不可得，仅用 W_RPS_120
      并以 top10% (>=90) 作为 SXHCG1
    - 周线换手 (W_SXHCG5) 默认跳过（CAPITAL 不在标准数据中）
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, List

from .weekly_rps_screener import daily_to_weekly, calculate_weekly_rps


@dataclass
class HuocheConfig:
    """周线火车轨参数"""

    rps120w_min: float = 90.0           # W_RPS_120 阈值（top10%）
    ma60w_lookback: int = 30            # 近 N 周
    ma60w_min_days: int = 25            # 至少 K 周站上 MA60W
    ma20w_short_lookback: int = 10
    ma20w_short_min_days: int = 9
    drawdown_lookback_w: int = 20       # 周内最大回撤窗口
    max_drawdown_pct: float = 0.25
    near_high_period_w: int = 120       # 120 周近高
    near_high_ratio: float = 0.80
    ma_trend_strict_weeks: int = 5      # MA20W 连续上升周数
    first_signal_weeks: int = 8         # 首次触发回看窗口


# =============================================================================
# 内部工具
# =============================================================================


def _add_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    """补齐周线 MA10W / MA20W / MA60W / MA120W"""
    weekly = weekly.copy()
    for p in [10, 20, 60, 120]:
        col = f"MA_{p}W"
        if col not in weekly.columns:
            weekly[col] = weekly.groupby("ts_code")["close"].transform(
                lambda x: x.rolling(p, min_periods=p).mean()
            )
    return weekly


def _sxhcg1(today: pd.Series, cfg: HuocheConfig) -> bool:
    rps = today.get("W_RPS_120")
    if pd.isna(rps):
        return False
    return rps >= cfg.rps120w_min


def _sxhcg2(window: pd.DataFrame, cfg: HuocheConfig) -> bool:
    """周线均线多头：站上MA20W + 长期均线支撑 + 短期高频站上"""
    today = window.iloc[-1]
    if pd.isna(today["MA_20W"]) or today["close"] <= today["MA_20W"]:
        return False

    tail = window.tail(cfg.ma60w_lookback)
    above = (tail["close"] > tail["MA_60W"]).sum()
    if above < cfg.ma60w_min_days:
        return False

    tail_short = window.tail(cfg.ma20w_short_lookback)
    if (tail_short["close"] > tail_short["MA_20W"]).sum() < cfg.ma20w_short_min_days:
        return False

    return True


def _sxhcg3(window: pd.DataFrame, cfg: HuocheConfig) -> bool:
    """周线回撤可控 + 接近 120 周新高"""
    today = window.iloc[-1]

    tail = window.tail(cfg.drawdown_lookback_w)
    highs = tail["high"].values
    lows = tail["low"].values
    new_high_idx = int(np.argmax(highs))
    new_high_price = highs[new_high_idx]
    new_low_price = (
        np.min(lows[new_high_idx:]) if new_high_idx < len(highs) - 1 else lows[-1]
    )
    drawdown = (new_high_price - new_low_price) / new_high_price if new_high_price > 0 else 0
    if drawdown > cfg.max_drawdown_pct:
        return False

    full = window.tail(cfg.near_high_period_w)
    hhv = full["close"].max()
    if hhv > 0 and today["close"] / hhv <= cfg.near_high_ratio:
        return False

    return True


def _sxhcg4(window: pd.DataFrame, cfg: HuocheConfig) -> bool:
    """周线均线趋势：MA20W 连续 N 周上行 且 MA10W>=MA20W；或宽松：今周三条件齐"""
    n = cfg.ma_trend_strict_weeks

    if len(window) >= n + 1:
        tail = window.tail(n + 1)
        ma20 = tail["MA_20W"].values
        ma10 = tail["MA_10W"].values
        ma20_rising = all(ma20[i + 1] >= ma20[i] for i in range(len(ma20) - n, len(ma20) - 1))
        ma10_above = all(ma10[i] >= ma20[i] for i in range(len(ma10) - n, len(ma10)))
        if ma20_rising and ma10_above:
            return True

    if len(window) < 2:
        return False
    today = window.iloc[-1]
    yesterday = window.iloc[-2]
    return (
        today["MA_10W"] >= yesterday["MA_10W"]
        and today["MA_20W"] >= yesterday["MA_20W"]
        and today["MA_10W"] >= today["MA_20W"]
    )


def _compute_signals_for_code(
    sdf: pd.DataFrame,
    target_dt: pd.Timestamp,
    cfg: HuocheConfig,
) -> Optional[dict]:
    """对单股票计算 W_SXHCG 当周状态及首次触发标记"""
    sdf = sdf[sdf["trade_date"] <= target_dt]
    lookback = max(
        cfg.ma60w_lookback,
        cfg.near_high_period_w,
        cfg.first_signal_weeks + cfg.drawdown_lookback_w,
    ) + 5
    window = sdf.tail(lookback)
    if len(window) < cfg.drawdown_lookback_w + cfg.first_signal_weeks:
        return None

    today = window.iloc[-1]
    if pd.isna(today.get("W_RPS_120")) or pd.isna(today.get("MA_120W")):
        return None

    s1 = _sxhcg1(today, cfg)
    s2 = _sxhcg2(window, cfg)
    s3 = _sxhcg3(window, cfg)
    s4 = _sxhcg4(window, cfg)
    sxhcg = s1 and s2 and s3 and s4

    rec = {
        "ts_code": today.get("ts_code"),
        "trade_date": today["trade_date"],
        "close": today["close"],
        "W_RPS_120": today["W_RPS_120"],
        "MA_120W": today["MA_120W"],
        "W_SXHCG1": s1,
        "W_SXHCG2": s2,
        "W_SXHCG3": s3,
        "W_SXHCG4": s4,
        "W_SXHCG": sxhcg,
        "HUOCHE_FIRST_W": False,
    }

    if sxhcg:
        recent = window.tail(cfg.first_signal_weeks + 1).iloc[:-1]
        first = True
        for _, row in recent.iterrows():
            sub_window = window[window["trade_date"] <= row["trade_date"]]
            if len(sub_window) < cfg.drawdown_lookback_w:
                continue
            past_today = sub_window.iloc[-1]
            if (
                _sxhcg1(past_today, cfg)
                and _sxhcg2(sub_window, cfg)
                and _sxhcg3(sub_window, cfg)
                and _sxhcg4(sub_window, cfg)
            ):
                first = False
                break
        rec["HUOCHE_FIRST_W"] = first

    return rec


# =============================================================================
# 公共 API
# =============================================================================


def screen_huoche(
    df_daily: pd.DataFrame,
    l1_codes: List[str],
    target_date: Optional[str] = None,
    config: Optional[HuocheConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    在 L1 候选基础上叠加周线火车轨过滤。

    参数
    ----------
    df_daily : pd.DataFrame
        全市场日线（含 trade_date / ts_code / OHLCV），至少 2 年数据
    l1_codes : list[str]
        L1 候选 ts_code 列表
    target_date : str, optional
        目标日期 YYYYMMDD，None 取最新；周线对齐到当周周五
    config : HuocheConfig, optional
        参数；不传用默认
    verbose : bool

    返回
    -------
    pd.DataFrame
        通过 W_SXHCG 的股票 + HUOCHE_FIRST_W 标签
    """
    cfg = config or HuocheConfig()

    if not l1_codes:
        if verbose:
            print("[火车轨] L1 候选为空，跳过")
        return pd.DataFrame()

    sub = df_daily[df_daily["ts_code"].isin(l1_codes)].copy()
    if sub.empty:
        if verbose:
            print("[火车轨] L1 候选在数据中无对应行")
        return pd.DataFrame()

    if verbose:
        print(f"[火车轨] L1 候选: {len(l1_codes)} 只 -> 日转周")

    weekly = daily_to_weekly(sub)
    weekly = calculate_weekly_rps(weekly, period=120)
    weekly = _add_weekly_indicators(weekly)

    if target_date is None:
        target_dt = weekly["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)
        valid = weekly["trade_date"].unique()
        valid = valid[valid <= td]
        if len(valid) == 0:
            if verbose:
                print(f"[火车轨] 目标日期 {target_date} 之前无周线数据")
            return pd.DataFrame()
        target_dt = pd.Timestamp(valid.max())

    if verbose:
        print(f"[火车轨] 目标周(对齐周五): {target_dt.strftime('%Y-%m-%d')}")
        print(
            f"[火车轨] 参数: RPS120W>={cfg.rps120w_min}, 近高 {cfg.near_high_period_w}周>={cfg.near_high_ratio:.2f}, "
            f"回撤<= {cfg.max_drawdown_pct:.0%}, 首次触发回看 {cfg.first_signal_weeks} 周"
        )

    records: List[dict] = []
    for code, sdf in weekly.groupby("ts_code"):
        rec = _compute_signals_for_code(sdf, target_dt, cfg)
        if rec is not None:
            records.append(rec)

    if not records:
        if verbose:
            print("[火车轨] 无可计算样本")
        return pd.DataFrame()

    signals = pd.DataFrame(records)
    cond_cols = ["W_SXHCG1", "W_SXHCG2", "W_SXHCG3", "W_SXHCG4"]

    if verbose:
        total = len(signals)
        for col in cond_cols:
            cnt = int(signals[col].sum())
            print(f"  {col}: {cnt}/{total} ({cnt / total * 100:.1f}%)")
        sx_cnt = int(signals["W_SXHCG"].sum())
        first_cnt = int(signals["HUOCHE_FIRST_W"].sum())
        print(f"  W_SXHCG (4 条件 AND): {sx_cnt}/{total} ({sx_cnt / total * 100:.1f}%)")
        print(f"  其中近 {cfg.first_signal_weeks} 周首次触发: {first_cnt}")

    passed = signals[signals["W_SXHCG"]].copy()
    passed = passed.sort_values(
        ["HUOCHE_FIRST_W", "W_RPS_120"], ascending=[False, False]
    ).reset_index(drop=True)
    return passed


def print_huoche_summary(result: pd.DataFrame) -> None:
    """格式化打印火车轨过滤结果"""
    if result.empty:
        print("  无股票通过周线火车轨")
        return

    first_n = int(result["HUOCHE_FIRST_W"].sum())
    print(f"\n  通过周线火车轨: {len(result)} 只 (首次触发 {first_n} 只)")

    display = result[
        ["ts_code", "close", "W_RPS_120", "HUOCHE_FIRST_W"]
    ].copy()
    display.columns = ["代码", "收盘价", "周RPS120", "首次触发"]
    print(display.to_string(index=False, float_format="%.2f"))
