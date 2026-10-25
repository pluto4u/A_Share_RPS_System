#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场情绪温度表 (Market Thermometer)

用途: 判断市场冷热程度，辅助仓位决策
      配合 S5 LinearThrottle 策略: 情绪分 → 25% 步进仓位
      (回测验证: Sharpe 1.34, 最大回撤 -9.2%, 优于买持 Sharpe 0.918/-15.7%)

五档温度与 S5 目标仓位:
  过热 (>=87.5): 目标 100% — 趋势强，满仓，注意顶部
  偏热 (62.5-87.5): 目标  75% — 情绪高涨，偏高仓位
  正常 (37.5-62.5): 目标  50% — 中性，标准仓位
  偏冷 (12.5-37.5): 目标  25% — 情绪低迷，轻仓观望
  极冷 (<12.5):  目标   0% — 市场恐慌，空仓等待

调仓规则 (S5): ① 距上次调仓 ≥5天 ② 仓位变化 ≥15% — 同时满足才执行

CLI:
    python -m emotion_validation.market_thermometer
    python -m emotion_validation.market_thermometer --days 30
"""

import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

import pandas as pd
import numpy as np

from project_paths import MARKET_DATA_DIR, REPORTS_DIR

logger = logging.getLogger(__name__)

DATA_DIR = MARKET_DATA_DIR
REPORT_DIR = REPORTS_DIR / 'thermometer'

# 温度档位 (阈值与 S5 量化步进保持一致)
TEMPERATURE_LEVELS = [
    (87.5, 100,  '[!!!] 过热', 'OVERHEAT', 'S5仓位100%。趋势强劲，注意顶部风险，不追高'),
    (62.5,  87.5,'[** ] 偏热', 'HOT',      'S5仓位 75%。情绪高涨，偏高仓位，正常持有'),
    (37.5,  62.5,'[-- ] 正常', 'NORMAL',   'S5仓位 50%。市场中性，标准仓位，按既定策略'),
    (12.5,  37.5,'[   ] 偏冷', 'COLD',     'S5仓位 25%。情绪低迷，轻仓，等待情绪回暖'),
    ( 0,    12.5,'[xxx] 极冷', 'FREEZE',   'S5仓位  0%。市场恐慌，空仓，等待情绪筑底'),
]


def get_temperature(score: float) -> dict:
    """根据情绪分数返回温度档位信息 (含 S5 目标仓位)"""
    # 从高到低遍历，score >= lo 时命中 (最后一档 lo=0 兜底)
    for lo, hi, label, code, advice in TEMPERATURE_LEVELS:
        if score >= lo:
            s5_pos = {
                'OVERHEAT': 1.00,
                'HOT'     : 0.75,
                'NORMAL'  : 0.50,
                'COLD'    : 0.25,
                'FREEZE'  : 0.00,
            }[code]
            return {
                'label'  : label,
                'code'   : code,
                'advice' : advice,
                'lo'     : lo,
                'hi'     : hi,
                's5_pos' : s5_pos,
            }
    return {
        'label'  : '[-- ] 正常',
        'code'   : 'NORMAL',
        'advice' : 'S5仓位 50%。市场中性，标准仓位，按既定策略',
        'lo'     : 37.5,
        'hi'     : 62.5,
        's5_pos' : 0.50,
    }


class MarketThermometer:
    """市场情绪温度表"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._scores: Optional[pd.DataFrame] = None

    def _load_scores(self) -> pd.DataFrame:
        """加载情绪评分数据 (优先从缓存, 否则重算)"""
        if self._scores is not None:
            return self._scores

        score_csv = REPORT_DIR.parent / 'emotion_validation' / 'emotion_scores.csv'
        if score_csv.exists():
            df = pd.read_csv(score_csv)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            self._scores = df.sort_values('trade_date').reset_index(drop=True)
            logger.info(f"加载情绪评分: {len(df)} 天")
            return self._scores

        # 没有缓存就重算
        logger.info("无缓存, 重新计算情绪评分...")
        from .data_layer.historical_ecology_builder import HistoricalEcologyBuilder
        from .emotion_engine.emotion_score import EmotionScorer

        builder = HistoricalEcologyBuilder(data_dir=str(self.data_dir))
        ecology = builder.load_ecology()
        scorer = EmotionScorer()
        self._scores = scorer.compute_with_reversal(ecology)
        return self._scores

    def today(self) -> dict:
        """返回最新一天的温度读数"""
        scores = self._load_scores()
        latest = scores.iloc[-1]
        score = latest['emotion_score']
        temp = get_temperature(score)

        # 趋势: 近 3 日变化
        chg_3d = latest.get('score_chg_3d', scores['emotion_score'].diff(3).iloc[-1])
        if pd.isna(chg_3d):
            chg_3d = 0

        trend = '↑ 回暖' if chg_3d > 3 else '↓ 降温' if chg_3d < -3 else '→ 平稳'

        # 连续几天同温度
        recent = scores.tail(20)
        streak = 0
        cur_code = temp['code']
        for sc in reversed(recent['emotion_score'].values):
            t = get_temperature(sc)
            if t['code'] == cur_code:
                streak += 1
            else:
                break

        return {
            'date'       : latest['trade_date'].strftime('%Y-%m-%d'),
            'score'      : round(score, 1),
            'temperature': temp,
            's5_pos_pct' : int(temp['s5_pos'] * 100),  # S5 目标仓位 %
            'trend_3d'   : round(chg_3d, 1),
            'trend_label': trend,
            'streak_days': streak,
            'regime'     : latest.get('regime_reversal', latest.get('regime', '')),
        }

    def history(self, days: int = 30) -> pd.DataFrame:
        """最近 N 天的温度历史"""
        scores = self._load_scores()
        recent = scores.tail(days).copy()
        recent['temp_label'] = recent['emotion_score'].apply(
            lambda s: get_temperature(s)['label']
        )
        recent['temp_code'] = recent['emotion_score'].apply(
            lambda s: get_temperature(s)['code']
        )
        cols = ['trade_date', 'emotion_score', 'temp_label', 'temp_code']
        for c in ['score_chg_3d', 'regime_reversal', 'regime']:
            if c in recent.columns:
                cols.append(c)
                break
        return recent[cols].reset_index(drop=True)

    def summary_stats(self, days: int = 90) -> dict:
        """近 N 天的温度分布统计"""
        scores = self._load_scores().tail(days)
        codes = scores['emotion_score'].apply(lambda s: get_temperature(s)['code'])
        dist = codes.value_counts()
        total = len(scores)
        return {
            'period_days': days,
            'avg_score': round(scores['emotion_score'].mean(), 1),
            'distribution': {k: f"{v}/{total} ({v/total*100:.0f}%)" for k, v in dist.items()},
        }

    def print_dashboard(self, days: int = 30):
        """打印可视化温度仪表盘"""
        today = self.today()
        score = today['score']
        temp = today['temperature']
        hist = self.history(days)
        stats = self.summary_stats(90)

        bar = self._score_bar(score)

        print()
        print("=" * 60)
        print("       A-Share Market Sentiment Thermometer")
        print(f"       {today['date']}")
        print("=" * 60)
        print()
        print(f"  Score    : {score:5.1f} / 100")
        print(f"  Status   : {temp['label']}")
        print(f"  S5 仓位  : {today['s5_pos_pct']:>3}%  (情绪分 → 25% 步进目标仓位)")
        print(f"  3D Trend : {today['trend_label']} ({today['trend_3d']:+.1f}pts)")
        print(f"  Streak   : {today['streak_days']} days in same zone")
        print()
        print(f"  |{bar}|")
        print(f"   FREEZE  COLD   NORMAL   HOT   OVER")
        print(f"   0     12.5   37.5    62.5  87.5  100")
        print()
        print(f"  Advice: {temp['advice']}")
        print()
        print("-" * 60)
        print(f"  Last 90 days distribution (avg {stats['avg_score']}):")
        for code, pct in stats['distribution'].items():
            label_str = next((lv[2] for lv in TEMPERATURE_LEVELS if lv[3] == code), code)
            print(f"    {label_str:<14}  {pct}")
        print()
        print("-" * 60)
        print(f"  Last {days} days sparkline:")
        self._print_sparkline(hist['emotion_score'].values)
        print()

        # Recent dates table
        print("  Last 10 days detail:")
        recent10 = hist.tail(10)
        print(f"  {'Date':<12} {'Score':>6}  Status")
        print(f"  {'-'*12} {'-'*6}  {'-'*18}")
        for _, row in recent10.iterrows():
            date_str = row['trade_date'].strftime('%m-%d')
            sc = row['emotion_score']
            label = row['temp_label']
            marker = ' <- TODAY' if date_str == today['date'][-5:] else ''
            print(f"  {date_str:<12} {sc:>6.1f}  {label}{marker}")
        print()
        print("=" * 60)

    @staticmethod
    def _score_bar(score: float, width: int = 40) -> str:
        """ASCII progress bar"""
        filled = int(score / 100 * width)
        return '#' * filled + '.' * (width - filled)

    @staticmethod
    def _print_sparkline(scores, width: int = 50):
        """ASCII sparkline using digits"""
        if len(scores) == 0:
            return
        # Map 0-100 to 0-9
        line = ''
        for s in scores[-width:]:
            line += str(min(9, int(s / 10)))
        print(f"  [0]{line}[100]")
        print("   low" + " " * (width - 4) + "high")

    def save_csv(self, days: int = 90):
        """保存近期温度历史到 CSV"""
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        hist = self.history(days)
        out = REPORT_DIR / f'thermometer_{datetime.now().strftime("%Y%m%d")}.csv'
        hist.to_csv(out, index=False, encoding='utf-8-sig')
        logger.info(f"温度历史已保存: {out}")
        return out


# ======================================================================
# CLI
# ======================================================================

def main():
    import argparse
    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    parser = argparse.ArgumentParser(description='市场情绪温度表')
    parser.add_argument('--days', type=int, default=30, help='显示最近 N 天 (默认 30)')
    parser.add_argument('--save', action='store_true', help='保存 CSV')
    parser.add_argument('--data-dir', default=None)
    args = parser.parse_args()

    t = MarketThermometer(data_dir=args.data_dir)
    t.print_dashboard(days=args.days)

    if args.save:
        path = t.save_csv(days=args.days)
        print(f"已保存: {path}")


if __name__ == '__main__':
    main()
