#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 月度动量轮动策略 + 情绪过滤

设计原则:
  - 情绪因子决定"投什么类型": FREEZE→宽基/现金, COLD→防御, NORMAL+→全池
  - 动量因子决定"选哪只": 20日+60日动量加权评分，取前 top_n
  - 月末换仓: 新排名与当前持仓有差异才实际换

回测表明 (S5 策略):
  情绪因子的正确用法是风险过滤器，而不是进出场开关
  → 这里用情绪分收窄候选池，而不是直接清仓
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ETF 分类 (与 etf_data.py ETF_POOL 对应)
SECTOR_DEFENSE  = {'防御', '宽基'}         # 极冷/偏冷时只允许持有
SECTOR_BALANCED = {'防御', '宽基', '消费', '金融'}  # 偏冷时扩展候选池


def score_to_emotion_regime(score: float) -> str:
    """情绪分 → 轮动档位"""
    if score >= 62.5:
        return 'NORMAL_PLUS'   # 正常/偏热/过热: 全池
    elif score >= 37.5:
        return 'NORMAL'        # 正常: 全池 (与 NORMAL_PLUS 合并)
    elif score >= 12.5:
        return 'COLD'          # 偏冷: 仅防御+宽基+消费+金融
    else:
        return 'FREEZE'        # 极冷: 仅沪深300 ETF (510300)


class ETFRotationStrategy:
    """
    月度 ETF 动量轮动 + 情绪过滤

    Args:
        top_n        : 持有 ETF 数量 (默认 2)
        fast_window  : 短期动量窗口，天 (默认 20)
        slow_window  : 长期动量窗口，天 (默认 60)
        fast_weight  : 短期动量权重 (默认 0.6)
    """

    SAFE_HARBOR = '510300'   # 极冷时的避风港: 沪深300 ETF

    def __init__(self,
                 top_n: int = 2,
                 fast_window: int = 20,
                 slow_window: int = 60,
                 fast_weight: float = 0.6):
        self.top_n        = top_n
        self.fast_window  = fast_window
        self.slow_window  = slow_window
        self.fast_weight  = fast_weight
        self.slow_weight  = 1.0 - fast_weight

    def _momentum_score(self, df: pd.DataFrame, as_of_date) -> Optional[float]:
        """
        计算单只 ETF 在 as_of_date 时的动量得分

        得分 = fast_weight * return_20d + slow_weight * return_60d

        预热期降级逻辑 (避免冷启动空仓):
          数据 >= slow_window  → 完整双窗口得分
          数据 >= fast_window  → 仅用快线（单一 fast 得分，不参与双窗口权重）
          数据 < fast_window   → None，不纳入候选
        """
        hist = df[df['trade_date'] <= as_of_date].sort_values('trade_date')
        n    = len(hist)

        if n < self.fast_window:
            return None   # 数据太少，彻底排除

        close    = hist['close'].values
        ret_fast = close[-1] / close[-self.fast_window] - 1

        if n >= self.slow_window:
            ret_slow = close[-1] / close[-self.slow_window] - 1
            return self.fast_weight * ret_fast + self.slow_weight * ret_slow

        # 预热期：只用快线，权重自动变为 1.0
        return ret_fast

    def compute_scores(self,
                       etf_data: Dict[str, pd.DataFrame],
                       as_of_date,
                       emotion_score: float) -> pd.DataFrame:
        """
        计算全部 ETF 的动量得分，并按情绪档位过滤候选池

        Returns:
            DataFrame with columns: symbol, name, sector, momentum_score, eligible
        """
        regime = score_to_emotion_regime(emotion_score)

        rows = []
        for symbol, df in etf_data.items():
            if df.empty or 'close' not in df.columns:
                continue
            name   = df['name'].iloc[0]   if 'name'   in df.columns else symbol
            sector = df['sector'].iloc[0] if 'sector' in df.columns else ''

            score = self._momentum_score(df, as_of_date)
            if score is None:
                continue

            # 按情绪档位决定是否进入候选池
            if regime == 'FREEZE':
                eligible = (symbol == self.SAFE_HARBOR)
            elif regime == 'COLD':
                eligible = (sector in SECTOR_BALANCED)
            else:
                eligible = True

            rows.append({
                'symbol'         : symbol,
                'name'           : name,
                'sector'         : sector,
                'momentum_score' : round(score * 100, 2),  # 转成百分比方便阅读
                'eligible'       : eligible,
                'regime'         : regime,
            })

        df = pd.DataFrame(rows)
        if df.empty or 'momentum_score' not in df.columns:
            # 数据不足时返回空 DataFrame，兜底逻辑在 get_holdings 处理
            return pd.DataFrame(columns=['symbol', 'name', 'sector',
                                         'momentum_score', 'eligible', 'regime'])
        return df.sort_values('momentum_score', ascending=False).reset_index(drop=True)

    def get_holdings(self,
                     etf_data: Dict[str, pd.DataFrame],
                     emotion_score: float,
                     as_of_date=None) -> List[dict]:
        """
        返回建议持仓 ETF 列表

        Args:
            etf_data      : {symbol: DataFrame} 字典
            emotion_score : 当日情绪分
            as_of_date    : 决策日期 (默认今日)

        Returns:
            list of {symbol, name, sector, momentum_score, weight}
            等权分配, weight = 1 / top_n
        """
        if as_of_date is None:
            as_of_date = pd.Timestamp.today()
        as_of_date = pd.Timestamp(as_of_date)

        scores = self.compute_scores(etf_data, as_of_date, emotion_score)
        candidates = scores[scores['eligible']].head(self.top_n)

        if candidates.empty:
            # 兜底: 强制持有沪深300 ETF
            candidates = scores[scores['symbol'] == self.SAFE_HARBOR].head(1)
            if candidates.empty:
                logger.warning("没有任何 ETF 数据，返回空持仓")
                return []

        weight = 1.0 / len(candidates)
        holdings = []
        for _, row in candidates.iterrows():
            holdings.append({
                'symbol'         : row['symbol'],
                'name'           : row['name'],
                'sector'         : row['sector'],
                'momentum_score' : row['momentum_score'],
                'weight'         : round(weight, 4),
                'regime'         : row['regime'],
            })
        return holdings

    def should_rebalance(self,
                         current_holdings: List[str],
                         new_holdings: List[str]) -> bool:
        """
        是否需要换仓: 新持仓与当前持仓有差异才换
        """
        return set(current_holdings) != set(new_holdings)


# ======================================================================
# CLI — 快速查看今日建议
# ======================================================================

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    parser = argparse.ArgumentParser(description='ETF 轮动策略 — 今日建议')
    parser.add_argument('--score', type=float, default=None, help='手动指定情绪分 (默认从 emotion_scores.csv 读取)')
    parser.add_argument('--date',  default=None, help='决策日期 YYYY-MM-DD (默认今日)')
    parser.add_argument('--top-n', type=int, default=2, help='持有 ETF 数量 (默认 2)')
    args = parser.parse_args()

    from .etf_data import ETFDataManager
    from pathlib import Path

    # 加载 ETF 数据
    mgr = ETFDataManager()
    etf_data = mgr.load_all()
    if not etf_data:
        print("ERROR: 本地无 ETF 数据，请先运行:")
        print("  python -m emotion_validation.etf_rotation.etf_data")
        return

    # 读取情绪分
    emotion_score = args.score
    if emotion_score is None:
        score_csv = Path(__file__).resolve().parent.parent.parent / 'backtest' / 'reports' / 'emotion_validation' / 'emotion_scores.csv'
        if score_csv.exists():
            df = pd.read_csv(score_csv)
            emotion_score = float(df['emotion_score'].iloc[-1])
            print(f"情绪分 (最新): {emotion_score:.1f}")
        else:
            emotion_score = 50.0
            print(f"未找到情绪评分文件，使用默认值 {emotion_score}")

    as_of_date = pd.Timestamp(args.date) if args.date else pd.Timestamp.today()
    strategy   = ETFRotationStrategy(top_n=args.top_n)
    holdings   = strategy.get_holdings(etf_data, emotion_score, as_of_date)

    regime = score_to_emotion_regime(emotion_score)
    print()
    print(f"  日期      : {as_of_date.strftime('%Y-%m-%d')}")
    print(f"  情绪分    : {emotion_score:.1f}  (档位: {regime})")
    print(f"  建议持仓  : {len(holdings)} 只 ETF")
    print()
    print(f"  {'代码':<10} {'名称':<14} {'板块':<10} {'动量得分':>8}  {'权重':>6}")
    print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*8}  {'-'*6}")
    for h in holdings:
        print(f"  {h['symbol']:<10} {h['name']:<14} {h['sector']:<10} "
              f"{h['momentum_score']:>7.1f}%  {h['weight']*100:>5.0f}%")
    print()


if __name__ == '__main__':
    main()
