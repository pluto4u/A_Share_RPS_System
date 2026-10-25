"""
================================================================================
宏观因子计算层 — 全部从 TDX 全市场日线派生
================================================================================
设计原则:
    1. 全部因子都是"全市场横截面统计量", 不依赖任何单一指数, 因此不会被
       上证指数的权重结构 (银行/石油) 扭曲, 更贴近个股实际体感。
    2. 用宽表矩阵 (date x code) 做向量化, 5000 只股票 x 10 年数据在
       几十秒内算完。
    3. 每个因子输出为 [-1, 1] 或 [0, 1] 的可比标度, 便于后续合成打分。

三组因子:
    TREND   趋势层 — 决定牛熊骨架 (慢, 200 日尺度)
    BREADTH 宽度层 — 决定参与度与背离 (中, 20-60 日尺度)
    HEAT    温度层 — 决定过热与否 (快, 5-20 日尺度)

依赖: pandas, numpy
输入: 含 trade_date/ts_code/open/high/low/close/vol/amount 的全市场日线,
      若已含 RPS_120 列则额外启用 B4 强势股因子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, List

import numpy as np
import pandas as pd


# =============================================================================
# 0. 宽表构建
# =============================================================================

@dataclass
class Matrices:
    """全市场宽表矩阵集合 (index=trade_date, columns=ts_code)"""

    close: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    vol: pd.DataFrame
    amount: pd.DataFrame
    rps120: Optional[pd.DataFrame] = None

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def n_alive(self) -> pd.Series:
        """每日有效交易的股票数 (用作各类占比的分母)"""
        return self.close.notna().sum(axis=1)


def build_matrices(
    df: pd.DataFrame,
    start: Optional[str] = None,
    dtype: str = "float32",
    verbose: bool = True,
) -> Matrices:
    """
    把长表日线转成宽表矩阵。

    参数
    ----
    df : DataFrame
        长表, 需含 trade_date/ts_code/close/high/low/vol/amount
    start : str, optional
        起始日期 YYYYMMDD, 用于限制内存占用。建议回测取 10 年, 日常取 3 年。
    dtype : str
        矩阵精度, float32 可把内存减半, 对统计量精度无实质影响。

    返回
    ----
    Matrices
    """
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if start:
        df = df[df["trade_date"] >= pd.Timestamp(start)]

    def _pivot(col: str) -> pd.DataFrame:
        m = df.pivot_table(index="trade_date", columns="ts_code",
                           values=col, aggfunc="last")
        return m.astype(dtype)

    close = _pivot("close")
    high = _pivot("high")
    low = _pivot("low")
    vol = _pivot("vol")
    amount = _pivot("amount")

    rps120 = _pivot("RPS_120") if "RPS_120" in df.columns else None

    if verbose:
        print(f"[Matrices] {close.shape[0]} 个交易日 x {close.shape[1]} 只股票")
        print(f"[Matrices] 日期范围: {close.index.min().date()} ~ {close.index.max().date()}")
        mem = sum(m.memory_usage(deep=True).sum() for m in
                  [close, high, low, vol, amount]) / 1e6
        print(f"[Matrices] 内存占用: ~{mem:.0f} MB")

    return Matrices(close=close, high=high, low=low, vol=vol,
                    amount=amount, rps120=rps120)


# =============================================================================
# 工具函数
# =============================================================================

def _ratio_true(mask: pd.DataFrame, valid: pd.DataFrame) -> pd.Series:
    """在有效样本内计算 mask 为真的占比 (0~1)"""
    num = (mask & valid).sum(axis=1)
    den = valid.sum(axis=1).replace(0, np.nan)
    return (num / den).astype("float64")


def _pct_rank_history(s: pd.Series, window: int = 750) -> pd.Series:
    """
    滚动历史分位 (0~100)。

    把任意量纲的原始因子转成"相对自己过去 N 年处于什么位置", 这是让
    不同因子可加权合成的关键一步。默认 750 日 ≈ 3 年。

    注意: 用 rank(pct) 而非 z-score, 因为 A 股很多因子分布厚尾且不对称。
    """
    return s.rolling(window, min_periods=max(60, window // 5)).apply(
        lambda x: (x[:-1] < x[-1]).mean() * 100 if len(x) > 1 else np.nan,
        raw=True,
    )


def _pct_rank_history_fast(s: pd.Series, window: int = 750,
                           min_periods: int = 250) -> pd.Series:
    """
    _pct_rank_history 的向量化近似 — 用 rolling.rank (pandas>=1.4)。
    速度比 apply 快 1~2 个数量级, 结果口径一致 (含自身, 差异可忽略)。
    """
    try:
        r = s.rolling(window, min_periods=min_periods).rank(pct=True) * 100
        return r
    except (AttributeError, NotImplementedError):
        return _pct_rank_history(s, window)


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def equal_weight_index(close: pd.DataFrame, base: float = 1000.0) -> pd.Series:
    """
    全市场等权合成指数 (EWI)。

    为什么不用上证指数:
      - 上证指数被大权重银行/石油扭曲, 2021-2024 多次出现"指数横盘, 个股
        腰斩"的背离, 用它判断牛熊会系统性滞后。
      - 等权指数 = 每天所有股票收益的算术平均累乘, 直接等价于"随机买一只
        股票的期望收益", 与个人持仓体感一致。

    实现: 每日截面收益中位数累乘 (用中位数而非均值, 抗个别妖股极值)。
    """
    ret = close.pct_change()
    # 剔除单日 |涨跌| > 25% 的异常值 (除权未复权导致的跳空)
    ret = ret.where(ret.abs() <= 0.25)
    daily = ret.median(axis=1, skipna=True).fillna(0.0)
    return base * (1.0 + daily).cumprod()


def realized_vol(series: pd.Series, window: int = 20) -> pd.Series:
    """年化已实现波动率"""
    r = np.log(series / series.shift(1))
    return r.rolling(window, min_periods=window // 2).std() * np.sqrt(244)


# =============================================================================
# 1. TREND 趋势层 — 牛熊骨架
# =============================================================================

def compute_trend_factors(m: Matrices, verbose: bool = True) -> pd.DataFrame:
    """
    趋势层因子 (慢变量, 200 日尺度)。

    T1 breadth_ma200 : 全市场站上 MA200 的占比
        牛熊分水岭最经典的宽度指标。>60% 牛, <30% 熊。
    T2 breadth_ma50  : 全市场站上 MA50 的占比
        中期趋势, 领先 T1 约 1-2 个月。
    T3 ewi_vs_ma200  : 等权指数相对自身 MA200 的偏离
        个股体感版的"牛熊线"。
    T4 ewi_ma200_slope : 等权指数 MA200 的 20 日斜率 (年化 %)
        方向比位置更重要 — 跌破 MA200 但 MA200 仍上行, 多为回调而非转熊。
    T5 ma_stack      : 全市场 MA20>MA60>MA200 完整多头排列的占比
        最严格的趋势确认, 底部区域会长期趋近 0。
    """
    close = m.close
    valid = close.notna()

    ma20 = close.rolling(20, min_periods=20).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    ma200 = close.rolling(200, min_periods=200).mean()

    out = pd.DataFrame(index=close.index)

    out["T1_breadth_ma200"] = _ratio_true(close > ma200, valid & ma200.notna())
    out["T2_breadth_ma50"] = _ratio_true(close > ma50, valid & ma50.notna())

    ewi = equal_weight_index(close)
    ewi_ma200 = ewi.rolling(200, min_periods=200).mean()
    out["T3_ewi_vs_ma200"] = ewi / ewi_ma200 - 1.0
    out["T4_ewi_ma200_slope"] = (ewi_ma200 / ewi_ma200.shift(20) - 1.0) * (244 / 20)

    stack = (ma20 > ma60) & (ma60 > ma200)
    out["T5_ma_stack"] = _ratio_true(stack, valid & ma200.notna())

    out["_ewi"] = ewi
    out["_ewi_ma200"] = ewi_ma200

    if verbose:
        print(f"[TREND] 完成 5 个因子, 有效起始日: "
              f"{out['T1_breadth_ma200'].first_valid_index()}")
    return out


# =============================================================================
# 2. BREADTH 宽度层 — 参与度与背离
# =============================================================================

def compute_breadth_factors(m: Matrices, verbose: bool = True) -> pd.DataFrame:
    """
    宽度层因子 (中变量, 20-60 日尺度)。

    B1 nhnl        : (52周新高数 - 52周新低数) / 有效数, 10 日平滑
        顶背离最灵敏的指标 — 指数创新高而 NHNL 转负, 通常是中期顶。
    B2 adv_ratio   : 上涨家数占比的 10 日均
    B3 mcclellan   : 麦克莱伦振荡器 = EMA19(净涨跌比) - EMA39(净涨跌比)
        经典短中期超买超卖, 正常区间 ±0.05, 极值 ±0.10。
    B4 leader_health : RPS120>=90 的强势股中站上 MA20 的比例
        龙头健康度。主升浪 >80%, 退潮期跌破 50% 是明确的撤退信号。
        (需要输入数据含 RPS_120 列, 否则为 NaN)
    B5 breadth_thrust : 站上 MA50 占比的 10 日变化
        Zweig Breadth Thrust 简化版, 底部反转的强确认。
    """
    close = m.close
    high = m.high
    low = m.low
    valid = close.notna()

    out = pd.DataFrame(index=close.index)

    # --- B1 新高新低 ---
    #
    # 注意: 这里用 **收盘价** 的 52 周最高/最低作为基准, 而不是盘中最高价。
    # 如果拿 close 去比 high.rolling().max(), 由于当天 close 几乎总是低于
    # 当天 high, 条件 close >= max(high)*0.999 几乎永远不成立 —— 自测时
    # 这个因子全程恒等于 0, 白白浪费了一份权重。
    # 收盘价口径也与 rps_analyzer 里 near_high 的算法保持一致。
    hh52 = close.rolling(244, min_periods=200).max()
    ll52 = close.rolling(244, min_periods=200).min()
    is_nh = close >= hh52 * 0.999
    is_nl = close <= ll52 * 1.001
    base = (valid & hh52.notna()).sum(axis=1).replace(0, np.nan)
    nhnl_raw = ((is_nh.sum(axis=1) - is_nl.sum(axis=1)) / base).astype("float64")
    out["B1_nhnl"] = nhnl_raw.rolling(10, min_periods=5).mean()

    # --- B2 涨跌家数 ---
    ret = close.pct_change()
    adv = (ret > 0) & valid
    dec = (ret < 0) & valid
    n_adv = adv.sum(axis=1)
    n_dec = dec.sum(axis=1)
    denom = (n_adv + n_dec).replace(0, np.nan)
    adv_ratio = (n_adv / denom).astype("float64")
    out["B2_adv_ratio"] = adv_ratio.rolling(10, min_periods=5).mean()

    # --- B3 麦克莱伦 ---
    net = ((n_adv - n_dec) / denom).astype("float64")
    out["B3_mcclellan"] = _ema(net, 19) - _ema(net, 39)

    # --- B4 龙头健康度 ---
    if m.rps120 is not None:
        ma20 = close.rolling(20, min_periods=20).mean()
        strong = (m.rps120 >= 90) & valid
        healthy = strong & (close > ma20)
        s_den = strong.sum(axis=1).replace(0, np.nan)
        out["B4_leader_health"] = (healthy.sum(axis=1) / s_den).astype("float64")
    else:
        out["B4_leader_health"] = np.nan

    # --- B5 宽度推力 ---
    ma50 = close.rolling(50, min_periods=50).mean()
    b_ma50 = _ratio_true(close > ma50, valid & ma50.notna())
    out["B5_breadth_thrust"] = b_ma50 - b_ma50.shift(10)

    if verbose:
        n_ok = out["B4_leader_health"].notna().sum()
        print(f"[BREADTH] 完成 5 个因子, B4 龙头健康度有效天数: {n_ok}")
    return out


# =============================================================================
# 3. HEAT 温度层 — 过热判断
# =============================================================================

def compute_heat_factors(m: Matrices, verbose: bool = True) -> pd.DataFrame:
    """
    温度层因子 (快变量, 5-20 日尺度)。全部为"越高越热"方向。

    H1 turnover_pulse : 全市场成交额 5 日均 / 60 日均
        量能脉冲。>1.5 是明显放量, >2.0 通常已是阶段性高潮。
    H2 extension      : 全市场 close/MA60-1 的中位数
        整体位置。>15% 说明大多数股票已远离成本区。
    H3 high_zone      : close >= 250 日最高 * 0.9 的股票占比
        高位股拥挤度。
    H4 dispersion     : 20 日收益的 P90 - P50
        赚钱效应集中度。数值飙升 = 少数票疯涨而多数不涨 = 典型的
        主升末期特征 (2015/6, 2021/2 均出现)。
    H5 vol_annual     : 等权指数 20 日已实现波动率 (年化)
        直接对接 sentiment_engine.kelly_vol_adjustment()。
    H6 blowoff        : 单日涨幅 >9.5% 的股票占比, 5 日均
        涨停温度的离线代理 (无需联网)。真实涨停数可由 akshare 覆盖。
    H7 gap_stress     : 单日跌幅 >9.5% 的股票占比, 5 日均
        恐慌代理。注意这是"冷"的极值, 在打分时做双向处理。
    """
    close = m.close
    high = m.high
    amount = m.amount
    valid = close.notna()

    out = pd.DataFrame(index=close.index)

    # --- H1 量能脉冲 ---
    total_amt = amount.sum(axis=1, skipna=True).astype("float64")
    out["H1_turnover_pulse"] = (
        total_amt.rolling(5, min_periods=3).mean()
        / total_amt.rolling(60, min_periods=40).mean()
    )

    # --- H2 整体偏离 ---
    ma60 = close.rolling(60, min_periods=60).mean()
    ext = (close / ma60 - 1.0).where(valid & ma60.notna())
    out["H2_extension"] = ext.median(axis=1, skipna=True).astype("float64")

    # --- H3 高位股拥挤 --- (同样用收盘价口径, 理由见 B1)
    hh250 = close.rolling(250, min_periods=200).max()
    out["H3_high_zone"] = _ratio_true(close >= hh250 * 0.90,
                                      valid & hh250.notna())

    # --- H4 收益离散度 ---
    ret20 = (close / close.shift(20) - 1.0).where(valid)
    p90 = ret20.quantile(0.90, axis=1)
    p50 = ret20.quantile(0.50, axis=1)
    out["H4_dispersion"] = (p90 - p50).astype("float64")

    # --- H5 波动率 ---
    ewi = equal_weight_index(close)
    out["H5_vol_annual"] = realized_vol(ewi, 20)

    # --- H6/H7 涨跌停代理 ---
    ret1 = close.pct_change()
    limit_up = (ret1 > 0.095) & valid
    limit_dn = (ret1 < -0.095) & valid
    den = valid.sum(axis=1).replace(0, np.nan)
    out["H6_blowoff"] = (limit_up.sum(axis=1) / den).astype("float64") \
        .rolling(5, min_periods=3).mean()
    out["H7_panic"] = (limit_dn.sum(axis=1) / den).astype("float64") \
        .rolling(5, min_periods=3).mean()

    if verbose:
        print(f"[HEAT] 完成 7 个因子")
    return out


# =============================================================================
# 4. 汇总
# =============================================================================

def compute_all_factors(
    df: pd.DataFrame,
    start: Optional[str] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    一次性算出全部 17 个宏观因子。

    返回 DataFrame, index=trade_date, 列为 T1..T5 / B1..B5 / H1..H7
    以及内部辅助列 _ewi / _ewi_ma200。
    """
    m = build_matrices(df, start=start, verbose=verbose)
    trend = compute_trend_factors(m, verbose=verbose)
    breadth = compute_breadth_factors(m, verbose=verbose)
    heat = compute_heat_factors(m, verbose=verbose)

    result = pd.concat([trend, breadth, heat], axis=1)
    result.index.name = "trade_date"
    return result
