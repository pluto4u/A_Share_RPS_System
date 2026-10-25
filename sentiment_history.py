"""
================================================================================
情绪历史记录 & 趋势分析    sentiment_history.py
================================================================================
功能:
  1. 每轮情绪评分后自动追加一行到 CSV
  2. 加载历史数据做趋势分析 (连涨/连跌天数, 移动均值)
  3. 输出趋势摘要供飞书卡片展示

文件位置:  outputs/signals/sentiment_history.csv
================================================================================
"""

import csv
import os
from datetime import datetime

from project_paths import SIGNALS_DIR

# ── 默认路径 ──
_DIR = str(SIGNALS_DIR)
_CSV = str(SIGNALS_DIR / "sentiment_history.csv")

_COLUMNS = [
    "date",           # 交易日 YYYY-MM-DD
    "time",           # 记录时刻 HH:MM
    "score",          # 综合评分 0-100
    "position",       # 最终仓位 %
    "status",         # 盘中/盘后/休市
    "涨跌比", "赚钱效应", "涨跌停比", "炸板率", "量比", "中小盘量能",  # 各维度分
]


def _ensure_csv(path=None):
    """确保 CSV 文件存在且有表头"""
    path = path or _CSV
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_COLUMNS)
    return path


def save_snapshot(date_str, time_str, score, position, status,
                  score_details=None, path=None):
    """
    追加一条情绪记录到 CSV。

    Parameters
    ----------
    date_str : str  交易日 "YYYY-MM-DD"
    time_str : str  时刻 "HH:MM"
    score    : int  综合评分
    position : int  最终仓位 %
    status   : str  盘中/盘后/休市
    score_details : dict  各维度评分 {"涨跌比": 80, ...}
    path     : str  自定义 CSV 路径 (测试用)
    """
    path = _ensure_csv(path)
    details = score_details or {}
    row = [
        date_str, time_str, score, position, status,
        details.get("涨跌比", ""),
        details.get("赚钱效应", ""),
        details.get("涨跌停比", ""),
        details.get("炸板率", ""),
        details.get("量比", ""),
        details.get("中小盘量能", ""),
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def load_history(days=30, path=None):
    """
    加载最近 N 天的情绪历史 (按日去重，取每天最后一条)。

    Returns
    -------
    list[dict]  按日期升序, 每项 {"date":..., "score":..., "position":...}
    """
    path = path or _CSV
    if not os.path.exists(path):
        return []

    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["score"] = int(float(row["score"]))
                row["position"] = int(float(row["position"]))
                rows.append(row)
            except (ValueError, KeyError):
                continue

    # 按日去重: 每天保留最后一条
    by_date = {}
    for r in rows:
        by_date[r["date"]] = r
    sorted_rows = sorted(by_date.values(), key=lambda x: x["date"])

    # 只保留最近 N 天
    return sorted_rows[-days:]


def analyze_trend(history=None, path=None):
    """
    分析情绪趋势。

    Returns
    -------
    dict
        streak : int      连续上升(+)/下降(-) 天数, 0=无趋势
        streak_label : str 如 "连涨3天" / "连跌2天" / "无明显趋势"
        ma3    : float    最近3天均值 (None if < 3 days)
        ma5    : float    最近5天均值 (None if < 5 days)
        prev_score : int  上一交易日评分 (None if no history)
        delta  : int      本日 vs 上一日差值 (None if no history)
        summary : str     一句话摘要
    """
    hist = history if history is not None else load_history(30, path)

    result = {
        "streak": 0,
        "streak_label": "无历史数据",
        "ma3": None,
        "ma5": None,
        "prev_score": None,
        "delta": None,
        "summary": "首次运行，暂无趋势",
    }

    if len(hist) < 2:
        return result

    scores = [r["score"] for r in hist]

    # 上一日
    result["prev_score"] = scores[-2] if len(scores) >= 2 else None

    # MA
    if len(scores) >= 3:
        result["ma3"] = round(sum(scores[-3:]) / 3, 1)
    if len(scores) >= 5:
        result["ma5"] = round(sum(scores[-5:]) / 5, 1)

    # 连续涨跌 (从最近一天往前看)
    streak = 0
    for i in range(len(scores) - 1, 0, -1):
        diff = scores[i] - scores[i - 1]
        if diff > 0:
            if streak >= 0:
                streak += 1
            else:
                break
        elif diff < 0:
            if streak <= 0:
                streak -= 1
            else:
                break
        else:
            break  # 持平打断

    result["streak"] = streak
    if streak > 0:
        result["streak_label"] = f"连涨{streak}天"
    elif streak < 0:
        result["streak_label"] = f"连跌{abs(streak)}天"
    else:
        result["streak_label"] = "无明显趋势"

    # 一句话摘要
    parts = []
    if result["prev_score"] is not None:
        delta = scores[-1] - result["prev_score"]
        result["delta"] = delta
        direction = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        parts.append(f"较上日 {direction}{abs(delta)}分")

    if abs(streak) >= 2:
        parts.append(result["streak_label"])

    if result["ma3"] is not None:
        parts.append(f"3日均值 {result['ma3']}")

    result["summary"] = "  |  ".join(parts) if parts else "暂无趋势"

    return result
