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
