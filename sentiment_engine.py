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
#
# A股个股截面波动率 (ecology realized_vol_20d) 通常 0.15~0.40
# 只在异常高波 (>1.5x 中位值) 时才介入, 正常波动绝不干预

VOL_BASELINE_ANNUAL = 0.28   # A股截面波动率中位值 (偏高设置, 宽容正常波动)


def kelly_vol_adjustment(realized_vol_20d_annual: float) -> float:
    """
    波动率自适应缩放因子 (0.0 ~ 1.0)

    核心原则: 只管极端高波, 正常波动完全不干预。
      vol_ratio <= 1.5x  → 1.0 (不动)
      1.5x ~ 2.5x       → 线性衰减 1.0 → 0.6
      > 2.5x            → 指数衰减 (罕见的极端行情)
    """
    if realized_vol_20d_annual <= 0 or math.isnan(realized_vol_20d_annual):
        return 1.0

    vol_ratio = realized_vol_20d_annual / VOL_BASELINE_ANNUAL

    if vol_ratio <= 1.5:
        return 1.0
    elif vol_ratio <= 2.5:
        return 1.0 - (vol_ratio - 1.5) * 0.4
    else:
        return max(0.15, 0.6 * math.exp(-1.2 * (vol_ratio - 2.5)))


def score_to_target_kelly(score: float, realized_vol_20d_annual: float = 0.0) -> float:
    """
    S6 目标仓位: S5 线性映射 × Kelly 波动率缩放

    波动率正常 (<1.5x 基准) 时与 S5 完全一致, 仅极端高波才压仓。
    保持 S5 的 25% 仓位网格以保证防抖逻辑正常运作。
    """
    base_pos = round((score / 100.0) * 4) / 4  # S5 基础: 0/0.25/0.5/0.75/1.0

    vol_adj = kelly_vol_adjustment(realized_vol_20d_annual)
    adjusted = base_pos * vol_adj

    return round(adjusted * 4) / 4  # 保持 25% 网格, 与 S5 防抖兼容


# ======================================================================
# P0-2: 回撤弹性缩减
# ======================================================================
#
# A股正常回调 5~8%, 中级调整 10~15%, 熊市 20%+
# 只在中级调整以上才介入, 日常波动绝不干预

def drawdown_elastic_multiplier(current_drawdown_pct: float) -> float:
    """
    回撤弹性: 根据当前回撤深度返回仓位缩放因子

    A股校准:
      <=8%  → 1.0  (正常回调, 不干预)
      8~15% → 线性 1.0 → 0.5 (中级调整, 逐步减仓)
      >15%  → 指数衰减 (熊市保护剩余本金)
    """
    if current_drawdown_pct >= 0:
        return 1.0

    dd = abs(current_drawdown_pct) / 100.0  # 归一化: 0.08 = 8%

    if dd <= 0.08:
        return 1.0
    elif dd <= 0.15:
        # 8%~15%: 线性衰减 1.0 → 0.5
        return 1.0 - (dd - 0.08) / 0.07 * 0.5
    else:
        # >15%: 指数衰减
        return max(0.10, 0.5 * math.exp(-5.0 * (dd - 0.15)))


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
