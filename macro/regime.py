"""
================================================================================
牛熊状态机 — 双轴打分 + 九宫格
================================================================================
核心设计: **不做一维总分**。

一维总分的问题是"牛市过热"和"熊市反弹过热"会被压成同一个数字, 但这两种
状态的应对完全相反 (前者减仓兑现, 后者根本不该有仓)。因此拆成两根正交的轴:

    趋势轴 TrendScore (0-100)  ← 慢变量, 决定"主基调是牛是熊"
    温度轴 HeatScore  (0-100)  ← 快变量, 决定"当下是冷是热"

两轴各切三段, 得到 3x3 = 9 个 regime, 每格对应一个基准仓位和操作语义。

第三个量 **StressLevel** (应激度) 不进九宫格, 而是作为仓位的乘数下修 —
因为波动率飙升和恐慌跌停在牛熊两端都可能出现, 混进温度轴会污染语义。

最终仓位 = 九宫格基准仓位
          x Kelly 波动率缩放  (sentiment_engine.kelly_vol_adjustment)
          x 回撤弹性缩减      (sentiment_engine.drawdown_elastic_multiplier)
          x 龙头健康度否决    (leader_health 背离时封顶)

设计文档: docs/strategy_macro_regime.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List

import numpy as np
import pandas as pd

try:
    from sentiment_engine import kelly_vol_adjustment, drawdown_elastic_multiplier
except ImportError:  # 允许 macro 包被单独使用
    def kelly_vol_adjustment(v: float) -> float:
        return 1.0

    def drawdown_elastic_multiplier(d: float) -> float:
        return 1.0


# =============================================================================
# 因子 -> 分数的锚点映射
# =============================================================================
#
# 每个元组是 (低锚点, 高锚点): 因子值 <= 低锚点 记 0 分, >= 高锚点 记 100 分,
# 中间线性插值。
#
# 锚点不是拍脑袋 — 取自 A 股 2010 年以来的经验分布:
#   - 站上 MA200 占比: 熊市底部 (2012/12, 2018/12, 2024/1) 均 <15%,
#     牛市顶部 (2015/6, 2021/2) 均 >65%
#   - 麦克莱伦/NHNL 的 ±0.08 对应约 ±2 个标准差
#   - 成交额脉冲 1.8x 对应 2014/12、2019/2、2024/9 三次量能井喷

TREND_ANCHORS: Dict[str, Tuple[float, float]] = {
    "T1_breadth_ma200":   (0.15, 0.65),
    "T2_breadth_ma50":    (0.20, 0.70),
    "T3_ewi_vs_ma200":    (-0.20, 0.20),
    "T4_ewi_ma200_slope": (-0.15, 0.15),
    "T5_ma_stack":        (0.05, 0.45),
    "B1_nhnl":            (-0.08, 0.08),
    "B5_breadth_thrust":  (-0.25, 0.25),
}

TREND_WEIGHTS: Dict[str, float] = {
    "T1_breadth_ma200":   0.22,
    "T2_breadth_ma50":    0.13,
    "T3_ewi_vs_ma200":    0.17,
    "T4_ewi_ma200_slope": 0.20,   # 方向比位置更稳, 给最高权重之一
    "T5_ma_stack":        0.10,
    "B1_nhnl":            0.12,
    "B5_breadth_thrust":  0.06,
}

HEAT_ANCHORS: Dict[str, Tuple[float, float]] = {
    "H1_turnover_pulse":  (0.80, 1.80),
    "H2_extension":       (-0.05, 0.18),
    "H3_high_zone":       (0.02, 0.25),
    "H4_dispersion":      (0.08, 0.35),
    "H6_blowoff":         (0.002, 0.025),
    "B2_adv_ratio":       (0.42, 0.60),
}

HEAT_WEIGHTS: Dict[str, float] = {
    "H1_turnover_pulse":  0.22,
    "H2_extension":       0.20,
    "H3_high_zone":       0.16,
    "H4_dispersion":      0.18,   # 主升末期最灵敏
    "H6_blowoff":         0.14,
    "B2_adv_ratio":       0.10,
}

# 应激度 — 不进九宫格, 只做仓位下修
STRESS_ANCHORS: Dict[str, Tuple[float, float]] = {
    "H5_vol_annual":      (0.18, 0.50),
    "H7_panic":           (0.001, 0.020),
}
STRESS_WEIGHTS: Dict[str, float] = {
    "H5_vol_annual": 0.55,
    "H7_panic":      0.45,
}


# =============================================================================
# 九宫格
# =============================================================================
#
#              温度 冷 (<35)      温度 温 (35~65)     温度 热 (>65)
# 趋势 强(>62)  牛市回调 0.85      主升行情 1.00       牛市过热 0.50
# 趋势 中(38-62) 震荡筑底 0.35     震荡市   0.50       反弹过热 0.25
# 趋势 弱(<38)  熊市恐慌 0.15      熊市反弹 0.25       熊市诱多 0.05

REGIME_GRID: Dict[Tuple[str, str], Dict] = {
    ("strong", "cold"): dict(
        name="牛市回调", code="BULL_PULLBACK", position=0.85,
        action="趋势完好但短期缩量回调, 是加仓窗口。优先补强势板块内回踩 MA20 的票。",
        color="#1a7f37"),
    ("strong", "warm"): dict(
        name="主升行情", code="BULL_ADVANCE", position=1.00,
        action="趋势与温度共振, 满仓持有。让利润奔跑, 不做高抛低吸。",
        color="#0f5c28"),
    ("strong", "hot"): dict(
        name="牛市过热", code="BULL_OVERHEAT", position=0.50,
        action="趋势仍强但已过热。停止开新仓, 分批兑现涨幅最大的持仓, 保留龙头。",
        color="#c2410c"),

    ("neutral", "cold"): dict(
        name="震荡筑底", code="RANGE_BOTTOM", position=0.35,
        action="缩量磨底。轻仓试错, 只买首次触发火车轨/早期启涨的票, 严格止损。",
        color="#0369a1"),
    ("neutral", "warm"): dict(
        name="震荡市", code="RANGE", position=0.50,
        action="无主线的结构市。半仓, 只做板块白名单内的票, 快进快出。",
        color="#475569"),
    ("neutral", "hot"): dict(
        name="反弹过热", code="RANGE_OVERHEAT", position=0.25,
        action="震荡格局中的情绪高潮, 通常是反弹终点。兑现为主, 不追高。",
        color="#b45309"),

    ("weak", "cold"): dict(
        name="熊市恐慌", code="BEAR_PANIC", position=0.15,
        action="趋势弱且极度缩量。这是熊市中唯一值得留底仓的位置, 但需等宽度推力 B5 转正才加。",
        color="#334155"),
    ("weak", "warm"): dict(
        name="熊市反弹", code="BEAR_RALLY", position=0.25,
        action="下降趋势中的反抽。只做超跌反弹且严格设时间止损, 不过夜重仓。",
        color="#7c2d12"),
    ("weak", "hot"): dict(
        name="熊市诱多", code="BEAR_TRAP", position=0.05,
        action="最危险的一格 — 趋势已破而情绪亢奋。清仓观望, 任何理由都不该重仓。",
        color="#991b1b"),
}


@dataclass
class RegimeConfig:
    """状态机参数"""

    # --- 轴切分阈值 ---
    trend_weak_below: float = 38.0
    trend_strong_above: float = 62.0
    heat_cold_below: float = 35.0
    heat_hot_above: float = 65.0

    # --- 防抖 ---
    confirm_days: int = 3          # 新状态需连续 N 日成立才切换
    score_smooth_days: int = 3     # 分数本身的平滑窗口
    hysteresis: float = 3.0        # 阈值迟滞带 — 进入新档需多走 N 分, 离开需少走 N 分

    # --- 回撤基准 ---
    drawdown_window: int = 250     # 用 N 日滚动高点而非历史最高点算回撤
                                   # (见 build_regime_series 中的说明)

    # --- 龙头健康度否决 ---
    leader_health_warn: float = 0.55   # 低于此值且趋势强 -> 判定顶背离
    leader_health_cap: float = 0.50    # 背离时的仓位上限

    # --- 应激度 ---
    use_stress_multiplier: bool = True
    # 乘数下限 — 防止与趋势轴重复扣减。
    # 趋势轴已经把持续熊市压到 0.15 基准仓位了, 如果再让波动/回撤乘数
    # 自由跌到 0.1, 熊市仓位会变成 1.5% 这种没有意义的数字。这两个乘数的
    # 职责只是"比趋势轴更快地对急跌做出反应", 不该承担长期择时。
    vol_mult_floor: float = 0.45
    dd_mult_floor: float = 0.40

    # --- 仓位网格 ---
    position_grid: float = 0.05    # 最终仓位取整到 5%


# =============================================================================
# 打分
# =============================================================================

def _score_one(s: pd.Series, lo: float, hi: float) -> pd.Series:
    """把单因子线性映射到 0-100, 超出锚点截断"""
    if hi == lo:
        return pd.Series(50.0, index=s.index)
    return ((s - lo) / (hi - lo) * 100.0).clip(0.0, 100.0)


def _weighted_score(
    factors: pd.DataFrame,
    anchors: Dict[str, Tuple[float, float]],
    weights: Dict[str, float],
) -> pd.DataFrame:
    """
    加权合成分数。缺失因子自动剔除并把权重按比例分摊到其余因子上,
    因此 B4 龙头健康度或资金流因子缺失时体系仍能运行 (自动降权)。
    """
    parts = {}
    used_w = {}
    for name, (lo, hi) in anchors.items():
        if name not in factors.columns:
            continue
        col = factors[name]
        if col.notna().sum() == 0:
            continue
        parts[name] = _score_one(col, lo, hi)
        used_w[name] = weights.get(name, 0.0)

    if not parts:
        raise ValueError("没有任何可用因子, 无法打分")

    sub = pd.DataFrame(parts)
    w = pd.Series(used_w)

    # 逐行按"当日实际非空因子"重新归一化权重
    mask = sub.notna()
    wmat = mask.mul(w, axis=1)
    denom = wmat.sum(axis=1).replace(0, np.nan)
    score = (sub.fillna(0.0) * wmat).sum(axis=1) / denom

    sub["_score"] = score
    sub["_n_used"] = mask.sum(axis=1)
    return sub


def compute_scores(
    factors: pd.DataFrame,
    config: Optional[RegimeConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    从因子表算出三个分数。

    返回 DataFrame, 列:
        trend_score, heat_score, stress_score,
        trend_n, heat_n  (当日实际参与打分的因子数, 用于判断可信度)
    """
    cfg = config or RegimeConfig()

    trend_sub = _weighted_score(factors, TREND_ANCHORS, TREND_WEIGHTS)
    heat_sub = _weighted_score(factors, HEAT_ANCHORS, HEAT_WEIGHTS)

    out = pd.DataFrame(index=factors.index)
    k = cfg.score_smooth_days
    out["trend_score"] = trend_sub["_score"].rolling(k, min_periods=1).mean()
    out["heat_score"] = heat_sub["_score"].rolling(k, min_periods=1).mean()
    out["trend_n"] = trend_sub["_n_used"]
    out["heat_n"] = heat_sub["_n_used"]

    try:
        stress_sub = _weighted_score(factors, STRESS_ANCHORS, STRESS_WEIGHTS)
        out["stress_score"] = stress_sub["_score"].rolling(k, min_periods=1).mean()
    except ValueError:
        out["stress_score"] = np.nan

    if verbose:
        last = out.dropna(subset=["trend_score"]).iloc[-1] if len(out.dropna(subset=["trend_score"])) else None
        if last is not None:
            print(f"[SCORE] 最新 趋势={last['trend_score']:.1f}  "
                  f"温度={last['heat_score']:.1f}  应激={last['stress_score']:.1f}")
    return out


# =============================================================================
# 状态判定
# =============================================================================

def _bucket_trend(v: float, cfg: RegimeConfig) -> str:
    if np.isnan(v):
        return "neutral"
    if v < cfg.trend_weak_below:
        return "weak"
    if v > cfg.trend_strong_above:
        return "strong"
    return "neutral"


def _bucket_heat(v: float, cfg: RegimeConfig) -> str:
    if np.isnan(v):
        return "warm"
    if v < cfg.heat_cold_below:
        return "cold"
    if v > cfg.heat_hot_above:
        return "hot"
    return "warm"


_TREND_ORDER = {"weak": 0, "neutral": 1, "strong": 2}
_HEAT_ORDER = {"cold": 0, "warm": 1, "hot": 2}


def _bucket_series_hysteresis(
    scores: pd.Series,
    lo_th: float,
    hi_th: float,
    labels: Tuple[str, str, str],
    band: float,
) -> pd.Series:
    """
    带迟滞的三档分桶。

    没有迟滞时, 分数在阈值上下 0.1 分的抖动就会让状态来回翻, 而每翻一次
    在实盘上就是一次调仓。迟滞的做法是: 向上升档要多走 band 分, 向下降档
    要多跌 band 分 —— 等价于给每个档位加了一个"粘性区间"。

    labels = (低档名, 中档名, 高档名)
    """
    low, mid, high = labels
    out: List[str] = []
    cur: Optional[str] = None

    for v in scores:
        if np.isnan(v):
            out.append(cur if cur is not None else mid)
            continue

        if cur is None:
            cur = low if v < lo_th else (high if v > hi_th else mid)
            out.append(cur)
            continue

        if cur == low:
            # 升到中档需要越过 lo_th + band
            nxt = mid if v >= lo_th + band else low
            if v > hi_th + band:
                nxt = high
        elif cur == high:
            nxt = mid if v <= hi_th - band else high
            if v < lo_th - band:
                nxt = low
        else:  # mid
            if v > hi_th + band:
                nxt = high
            elif v < lo_th - band:
                nxt = low
            else:
                nxt = mid
        cur = nxt
        out.append(cur)

    return pd.Series(out, index=scores.index)


def classify_regime(
    trend_score: float,
    heat_score: float,
    config: Optional[RegimeConfig] = None,
) -> Dict:
    """单点分类 — 返回九宫格中对应格子的字典 (含 name/code/position/action)"""
    cfg = config or RegimeConfig()
    key = (_bucket_trend(trend_score, cfg), _bucket_heat(heat_score, cfg))
    cell = dict(REGIME_GRID[key])
    cell["trend_bucket"], cell["heat_bucket"] = key
    return cell


def _apply_confirmation(raw_codes: pd.Series, n: int) -> pd.Series:
    """
    防抖: 只有当新状态连续出现 n 天, 才真正切换。

    这一步很重要 — 原始状态在阈值附近会每天翻, 而每翻一次就意味着一次
    调仓。实测 n=3 能把年换手次数压掉 60% 以上而几乎不损失择时效果。
    """
    out = []
    current = None
    streak = 0
    prev_raw = None
    for c in raw_codes:
        if c == prev_raw:
            streak += 1
        else:
            streak = 1
        prev_raw = c

        if current is None:
            current = c
        elif c != current and streak >= n:
            current = c
        out.append(current)
    return pd.Series(out, index=raw_codes.index)


def build_regime_series(
    factors: pd.DataFrame,
    config: Optional[RegimeConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    完整流水线: 因子 -> 分数 -> 状态 -> 目标仓位。

    返回 DataFrame, 列:
        trend_score, heat_score, stress_score,
        regime_raw, regime            (防抖前/后的状态码)
        regime_name, action
        base_position                 (九宫格基准仓位)
        vol_mult, dd_mult, leader_cap (三个下修因子)
        target_position               (最终目标仓位 0~1)
        divergence                    (顶背离标记)
    """
    cfg = config or RegimeConfig()
    scores = compute_scores(factors, cfg, verbose=verbose)

    df = scores.copy()

    # --- 原始状态 (带阈值迟滞) ---
    t_bucket = _bucket_series_hysteresis(
        df["trend_score"], cfg.trend_weak_below, cfg.trend_strong_above,
        ("weak", "neutral", "strong"), cfg.hysteresis,
    )
    h_bucket = _bucket_series_hysteresis(
        df["heat_score"], cfg.heat_cold_below, cfg.heat_hot_above,
        ("cold", "warm", "hot"), cfg.hysteresis,
    )
    cells = [REGIME_GRID[(t, h)] for t, h in zip(t_bucket, h_bucket)]
    df["trend_bucket"] = t_bucket
    df["heat_bucket"] = h_bucket
    df["regime_raw"] = [c["code"] for c in cells]
    df["base_position_raw"] = [c["position"] for c in cells]

    # --- 防抖 ---
    df["regime"] = _apply_confirmation(df["regime_raw"], cfg.confirm_days)
    code_to_cell = {c["code"]: c for c in REGIME_GRID.values()}
    df["regime_name"] = df["regime"].map(lambda c: code_to_cell[c]["name"])
    df["action"] = df["regime"].map(lambda c: code_to_cell[c]["action"])
    df["base_position"] = df["regime"].map(lambda c: code_to_cell[c]["position"])

    # --- 应激度下修 (波动率) ---
    if cfg.use_stress_multiplier and "H5_vol_annual" in factors.columns:
        df["vol_mult"] = factors["H5_vol_annual"].reindex(df.index).map(
            lambda v: kelly_vol_adjustment(float(v)) if pd.notna(v) else 1.0
        ).clip(lower=cfg.vol_mult_floor)
    else:
        df["vol_mult"] = 1.0

    # --- 回撤弹性下修 ---
    #
    # 关键: 用 **滚动 N 日高点** 而不是历史最高点 (cummax)。
    #
    # 自测时踩到的坑: 用 cummax 会让 2021 年的高点永久压制 2024 年的仓位 ——
    # 一轮熊市之后指数即使已经反转向上、趋势分回到 80+, 只要还没收复几年前
    # 的历史高位, drawdown 就永远 >15%, 弹性乘数被钉死在 0.10, 目标仓位
    # 永远只有 10%。自测里"再牛段"仓位只有 9.8% 而"牛市段"是 81%,
    # 就是这个 bug 的表现。
    #
    # 语义上我们想问的是"当前离**近期**高点多远", 而不是"离历史最高点多远",
    # 后者是 portfolio 层面的净值回撤概念, 不该套在市场指数上。
    if "_ewi" in factors.columns:
        ewi = factors["_ewi"].reindex(df.index)
        peak = ewi.rolling(cfg.drawdown_window, min_periods=60).max()
        dd_pct = (ewi / peak - 1.0) * 100.0
        df["ewi_drawdown_pct"] = dd_pct
        df["dd_mult"] = dd_pct.map(
            lambda d: drawdown_elastic_multiplier(float(d)) if pd.notna(d) else 1.0
        ).clip(lower=cfg.dd_mult_floor)
    else:
        df["ewi_drawdown_pct"] = np.nan
        df["dd_mult"] = 1.0

    # --- 龙头健康度背离否决 ---
    # 趋势分仍高但强势股已跌破 MA20 -> 典型的顶部背离 (指数还在涨,
    # 领涨股先崩)。此时无论九宫格给多少仓位, 一律封顶。
    if "B4_leader_health" in factors.columns:
        lh = factors["B4_leader_health"].reindex(df.index)
        diverge = (df["trend_score"] > cfg.trend_strong_above) & (lh < cfg.leader_health_warn)
        df["divergence"] = diverge.fillna(False)
        df["leader_cap"] = np.where(df["divergence"], cfg.leader_health_cap, 1.0)
    else:
        df["divergence"] = False
        df["leader_cap"] = 1.0

    # --- 最终仓位 ---
    pos = df["base_position"] * df["vol_mult"] * df["dd_mult"]
    pos = np.minimum(pos, df["leader_cap"])
    g = cfg.position_grid
    df["target_position"] = (pos / g).round() * g
    df["target_position"] = df["target_position"].clip(0.0, 1.0)

    if verbose:
        valid = df.dropna(subset=["trend_score"])
        if len(valid):
            last = valid.iloc[-1]
            print(f"[REGIME] 最新状态: {last['regime_name']} ({last['regime']})  "
                  f"目标仓位 {last['target_position']:.0%}")
            n_switch = (valid["regime"] != valid["regime"].shift()).sum()
            yrs = max(1e-9, len(valid) / 244)
            print(f"[REGIME] 历史切换 {n_switch} 次, 平均 {n_switch/yrs:.1f} 次/年")

    return df


# =============================================================================
# 报告
# =============================================================================

def print_regime_report(regime_df: pd.DataFrame, factors: pd.DataFrame,
                        n_recent: int = 10) -> None:
    """终端可读的当日宏观读数"""
    valid = regime_df.dropna(subset=["trend_score"])
    if valid.empty:
        print("无有效状态数据")
        return
    last = valid.iloc[-1]
    dt = valid.index[-1]

    print("\n" + "=" * 74)
    print(f"宏观状态读数    {pd.Timestamp(dt).date()}")
    print("=" * 74)
    print(f"  趋势轴 TrendScore : {last['trend_score']:6.1f}   "
          f"({_bucket_trend(last['trend_score'], RegimeConfig())})")
    print(f"  温度轴 HeatScore  : {last['heat_score']:6.1f}   "
          f"({_bucket_heat(last['heat_score'], RegimeConfig())})")
    print(f"  应激度 Stress     : {last['stress_score']:6.1f}")
    print("-" * 74)
    print(f"  状态: 【{last['regime_name']}】  {last['regime']}")
    print(f"  操作: {last['action']}")
    print("-" * 74)
    print(f"  基准仓位 {last['base_position']:.0%}"
          f"  x 波动 {last['vol_mult']:.2f}"
          f"  x 回撤 {last['dd_mult']:.2f}"
          f"  x 龙头封顶 {last['leader_cap']:.2f}")
    print(f"  >>> 目标仓位 {last['target_position']:.0%}")
    if last.get("divergence"):
        print("  [!] 顶背离警告: 趋势分仍高, 但强势股已大面积跌破 MA20")
    print("=" * 74)

    # 关键因子明细
    show = [
        ("T1 站上MA200占比", "T1_breadth_ma200", "{:.1%}"),
        ("T4 MA200斜率(年化)", "T4_ewi_ma200_slope", "{:+.1%}"),
        ("B1 新高新低差", "B1_nhnl", "{:+.2%}"),
        ("B4 龙头健康度", "B4_leader_health", "{:.1%}"),
        ("H1 量能脉冲", "H1_turnover_pulse", "{:.2f}x"),
        ("H2 整体偏离MA60", "H2_extension", "{:+.1%}"),
        ("H3 高位股占比", "H3_high_zone", "{:.1%}"),
        ("H4 收益离散度", "H4_dispersion", "{:.1%}"),
        ("H5 年化波动率", "H5_vol_annual", "{:.1%}"),
    ]
    print("\n  关键因子:")
    for label, col, fmt in show:
        if col in factors.columns:
            v = factors[col].reindex([dt]).iloc[0]
            txt = fmt.format(v) if pd.notna(v) else "N/A"
            print(f"    {label:<18} {txt:>10}")

    # 近期状态轨迹
    tail = valid.tail(n_recent)
    print(f"\n  近 {len(tail)} 个交易日轨迹:")
    for dt_i, row in tail.iterrows():
        print(f"    {pd.Timestamp(dt_i).date()}  趋势{row['trend_score']:5.1f} "
              f"温度{row['heat_score']:5.1f}  {row['regime_name']:<6} "
              f"仓位{row['target_position']:>5.0%}")
    print()
