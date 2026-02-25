import math


def final_position(score_position, trend_gate, breadth_cap):
    return min(score_position, trend_gate, breadth_cap)


def score_to_position(score):
    if score >= 80:
        return 80
    if score >= 60:
        return 60
    if score >= 40:
        return 30
    if score >= 20:
        return 10
    return 0


def apply_breadth_cap(position, breadth_pct, freeze_threshold=10.0):
    if breadth_pct < freeze_threshold:
        return min(position, 10)
    return position


# ======================================================================
# P0-1: Kelly-波动率自适应仓位
# ======================================================================

VOL_BASELINE_ANNUAL = 0.22   # A股年化波动率基准 (~22%)
VOL_BASELINE_DAILY = VOL_BASELINE_ANNUAL / math.sqrt(250)


def kelly_vol_adjustment(realized_vol_20d_annual: float) -> float:
    """
    波动率自适应缩放因子 (0.0 ~ 1.0)

    数学原理:
      几何增长率 G(f) = f*μ - f²*σ²/2
      最优仓位 f* ∝ 1/σ² (Kelly公式)
      高波动时应压低仓位以保护复利

    Parameters
    ----------
    realized_vol_20d_annual : float
        近20日实现波动率 (年化), 如 0.25 表示25%

    Returns
    -------
    float : 仓位缩放因子, 1.0 = 正常, <1.0 = 压低
    """
    if realized_vol_20d_annual <= 0 or math.isnan(realized_vol_20d_annual):
        return 1.0

    vol_ratio = realized_vol_20d_annual / VOL_BASELINE_ANNUAL

    if vol_ratio <= 1.0:
        return 1.0
    elif vol_ratio <= 1.8:
        # 1.0x~1.8x 基准: 线性衰减 1.0 → 0.55
        return 1.0 - (vol_ratio - 1.0) * 0.5625
    else:
        # >1.8x 基准: 指数衰减 (极端高波严厉压仓)
        return max(0.10, 0.55 * math.exp(-1.5 * (vol_ratio - 1.8)))


def score_to_target_kelly(score: float, realized_vol_20d_annual: float = 0.0) -> float:
    """
    S6 目标仓位: S5 线性映射 × Kelly 波动率缩放

    在 S5 的基础上, 当市场波动率高于基准时自动压低仓位。
    波动率正常或偏低时, 与 S5 完全一致。

    Parameters
    ----------
    score : float
        情绪评分 0-100
    realized_vol_20d_annual : float
        近20日年化波动率. 传 0 或 nan 则退化为纯 S5

    Returns
    -------
    float : 目标仓位 (量化到 5% 精度)
    """
    base_pos = round((score / 100.0) * 4) / 4  # S5 基础: 0/0.25/0.5/0.75/1.0

    vol_adj = kelly_vol_adjustment(realized_vol_20d_annual)
    adjusted = base_pos * vol_adj

    return round(adjusted * 20) / 20  # 量化到 5% 精度


# ======================================================================
# P0-2: 回撤弹性缩减
# ======================================================================

def drawdown_elastic_multiplier(current_drawdown_pct: float) -> float:
    """
    回撤弹性: 根据当前回撤深度返回仓位缩放因子

    数学原理:
      亏损 d% 后需要 d/(1-d) 才能回本, 这个代价是非线性的。
      10% 回撤需 11.1% 回本, 30% 需 42.9%, 50% 需 100%。
      因此回撤越深, 仓位应该指数级压低以保护剩余本金的复利能力。

    Parameters
    ----------
    current_drawdown_pct : float
        当前从最高点的回撤百分比 (负数, 如 -5.0 表示回撤5%)

    Returns
    -------
    float : 仓位缩放因子 (0.05 ~ 1.0)
    """
    if current_drawdown_pct >= 0:
        return 1.0

    dd = abs(current_drawdown_pct) / 100.0  # 归一化: 0.05 = 5%

    if dd <= 0.03:
        # 3%以内: 正常波动, 不干预
        return 1.0
    elif dd <= 0.08:
        # 3%~8%: 线性衰减, 从 1.0 降到 0.5
        return 1.0 - (dd - 0.03) / 0.05 * 0.5
    else:
        # >8%: 指数衰减, 保护剩余本金
        return max(0.05, 0.5 * math.exp(-8.0 * (dd - 0.08)))


def volume_clearing_score(clearing_data):
    """
    中小盘ETF量能清空评分 (0-100)。

    量能清空（地量）是卖压枯竭的信号，通常出现在底部区域。
    评分逻辑:
      - 全部量能清空 → 100 (极度缩量，卖压枯竭)
      - 部分量能清空 → 80
      - 全部缩量      → 65
      - 部分缩量      → 50
      - 正常          → 50 (中性)
      - 全部放量      → 30 (可能资金出逃)

    Parameters
    ----------
    clearing_data : list[dict]
        每个 dict 包含 status ('量能清空'|'缩量'|'正常'|'放量'|'数据不足')
        和 vol_ratio (float)

    Returns
    -------
    (int, str)
        评分, 状态描述
    """
    if not clearing_data:
        return 50, "无数据"

    valid = [d for d in clearing_data if not d.get("error")]
    if not valid:
        return 50, "无数据"

    statuses = [d["status"] for d in valid]
    clearing_count = statuses.count("量能清空")
    shrink_count = statuses.count("缩量")
    surge_count = statuses.count("放量")
    total = len(valid)

    if clearing_count == total:
        return 100, "全面清空"
    if clearing_count > 0:
        return 80, "部分清空"
    if shrink_count == total:
        return 65, "全面缩量"
    if shrink_count > 0:
        return 55, "部分缩量"
    if surge_count == total:
        return 30, "全面放量"
    if surge_count > 0:
        return 40, "部分放量"
    return 50, "量能正常"
