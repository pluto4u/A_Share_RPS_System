#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪驱动回测模拟器

核心逻辑:
  1. 固定基准策略 S: 用沪深300/创业板等指数日线模拟持仓
  2. 情绪开关: 当 regime == 'high' 时满仓; 'medium' 半仓; 'low' 空仓
  3. A/B 对比: 无过滤 vs 有过滤
  4. T-1 驱动 T: 用昨日情绪分数决定今日仓位
"""

import logging
from typing import Dict, Optional, List

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

COST_RATE = 0.003  # 单边万三佣金 + 印花税 + 滑点


class EmotionBacktestSimulator:
    """情绪驱动的回测模拟器"""

    def __init__(self, initial_capital: float = 1_000_000,
                 cost_rate: float = COST_RATE):
        self.initial_capital = initial_capital
        self.cost_rate = cost_rate

    # ------------------------------------------------------------------
    # Index-tracking strategy (benchmark and emotion-filtered)
    # ------------------------------------------------------------------

    def run_index_strategy(self,
                           index_daily: pd.DataFrame,
                           emotion_df: pd.DataFrame = None,
                           use_emotion_filter: bool = False,
                           label: str = 'strategy') -> pd.DataFrame:
        """
        在指数日线上模拟持仓策略

        Args:
            index_daily: 指数日线 (trade_date, close, pct_chg)
            emotion_df: 情绪评分表 (trade_date, emotion_score, regime, position_pct)
            use_emotion_filter: 是否启用情绪仓位控制
            label: 策略标签

        Returns:
            DataFrame: trade_date, daily_return, cumulative_return, position, nav
        """
        index_daily = index_daily.sort_values('trade_date').reset_index(drop=True)

        if 'pct_chg' not in index_daily.columns:
            index_daily['pct_chg'] = index_daily['close'].pct_change() * 100

        index_daily = index_daily.dropna(subset=['pct_chg']).reset_index(drop=True)

        records = []
        nav = self.initial_capital
        position = 1.0

        # T-1 emotion lookup
        emotion_map = {}
        if emotion_df is not None and use_emotion_filter:
            for _, row in emotion_df.iterrows():
                emotion_map[row['trade_date']] = {
                    'score': row['emotion_score'],
                    'regime': row['regime'],
                    'position_pct': row['position_pct'],
                }

        prev_date = None
        for _, row in index_daily.iterrows():
            date = row['trade_date']
            daily_ret_pct = row['pct_chg']

            if use_emotion_filter and prev_date is not None:
                emo = emotion_map.get(prev_date, {})
                new_pos = emo.get('position_pct', 0.5)

                if new_pos != position:
                    nav *= (1 - self.cost_rate * abs(new_pos - position))
                position = new_pos
            elif not use_emotion_filter:
                position = 1.0

            strategy_return = daily_ret_pct / 100 * position
            nav *= (1 + strategy_return)

            records.append({
                'trade_date': date,
                'daily_return': round(strategy_return * 100, 4),
                'position': position,
                'nav': round(nav, 2),
                'emotion_score': emotion_map.get(date, {}).get('score', np.nan),
                'regime': emotion_map.get(date, {}).get('regime', ''),
            })
            prev_date = date

        result = pd.DataFrame(records)
        result['cumulative_return'] = (result['nav'] / self.initial_capital - 1) * 100
        result['label'] = label

        logger.info(f"[{label}] 回测完成: NAV={nav:.0f}, "
                    f"累计={result['cumulative_return'].iloc[-1]:.2f}%")
        return result

    # ------------------------------------------------------------------
    # Run A/B comparison
    # ------------------------------------------------------------------

    def run_reversal_strategy(self,
                              index_daily: pd.DataFrame,
                              emotion_df: pd.DataFrame,
                              label: str = 'reversal_strategy') -> pd.DataFrame:
        """
        拐点回升策略: 用 regime_reversal + position_pct_reversal 做仓位控制

        核心区别: 极度悲观后回升 → 重仓进场 (而非空仓)
        """
        index_daily = index_daily.sort_values('trade_date').reset_index(drop=True)
        if 'pct_chg' not in index_daily.columns:
            index_daily['pct_chg'] = index_daily['close'].pct_change() * 100
        index_daily = index_daily.dropna(subset=['pct_chg']).reset_index(drop=True)

        pos_col = 'position_pct_reversal' if 'position_pct_reversal' in emotion_df.columns else 'position_pct'
        regime_col = 'regime_reversal' if 'regime_reversal' in emotion_df.columns else 'regime'

        emotion_map = {}
        for _, row in emotion_df.iterrows():
            emotion_map[row['trade_date']] = {
                'score': row['emotion_score'],
                'regime': row[regime_col],
                'position_pct': row[pos_col],
            }

        records = []
        nav = self.initial_capital
        position = 0.5
        prev_date = None

        for _, row in index_daily.iterrows():
            date = row['trade_date']
            daily_ret_pct = row['pct_chg']

            if prev_date is not None:
                emo = emotion_map.get(prev_date, {})
                new_pos = emo.get('position_pct', 0.5)
                if new_pos != position:
                    nav *= (1 - self.cost_rate * abs(new_pos - position))
                position = new_pos

            strategy_return = daily_ret_pct / 100 * position
            nav *= (1 + strategy_return)

            emo_today = emotion_map.get(date, {})
            records.append({
                'trade_date': date,
                'daily_return': round(strategy_return * 100, 4),
                'position': position,
                'nav': round(nav, 2),
                'emotion_score': emo_today.get('score', np.nan),
                'regime': emo_today.get('regime', ''),
            })
            prev_date = date

        result = pd.DataFrame(records)
        result['cumulative_return'] = (result['nav'] / self.initial_capital - 1) * 100
        result['label'] = label

        logger.info(f"[{label}] 回测完成: NAV={nav:.0f}, "
                    f"累计={result['cumulative_return'].iloc[-1]:.2f}%")
        return result

    def run_emergency_exit_strategy(self,
                                    index_daily: pd.DataFrame,
                                    emotion_df: pd.DataFrame,
                                    exit_threshold: float = 30,
                                    reentry_threshold: float = 40,
                                    confirm_days: int = 3,
                                    label: str = 'emergency_exit') -> pd.DataFrame:
        """
        紧急撤退策略: 平时满仓，只在情绪极端低迷时清仓

        防抖机制: 连续 confirm_days 天低于 exit_threshold 才清仓;
                  回升到 reentry_threshold 以上才回场
        """
        index_daily = index_daily.sort_values('trade_date').reset_index(drop=True)
        if 'pct_chg' not in index_daily.columns:
            index_daily['pct_chg'] = index_daily['close'].pct_change() * 100
        index_daily = index_daily.dropna(subset=['pct_chg']).reset_index(drop=True)

        score_map = {}
        for _, row in emotion_df.iterrows():
            score_map[row['trade_date']] = row['emotion_score']

        records = []
        nav = self.initial_capital
        position = 1.0
        low_streak = 0
        prev_date = None

        for _, row in index_daily.iterrows():
            date = row['trade_date']
            daily_ret_pct = row['pct_chg']

            if prev_date is not None:
                prev_score = score_map.get(prev_date, 50)

                if prev_score < exit_threshold:
                    low_streak += 1
                else:
                    low_streak = 0

                new_pos = position
                if position > 0 and low_streak >= confirm_days:
                    new_pos = 0.0
                elif position == 0 and prev_score >= reentry_threshold:
                    new_pos = 1.0

                if new_pos != position:
                    nav *= (1 - self.cost_rate * abs(new_pos - position))
                position = new_pos

            strategy_return = daily_ret_pct / 100 * position
            nav *= (1 + strategy_return)

            records.append({
                'trade_date': date,
                'daily_return': round(strategy_return * 100, 4),
                'position': position,
                'nav': round(nav, 2),
                'emotion_score': score_map.get(date, np.nan),
                'regime': 'exit' if position == 0 else 'hold',
            })
            prev_date = date

        result = pd.DataFrame(records)
        result['cumulative_return'] = (result['nav'] / self.initial_capital - 1) * 100
        result['label'] = label

        exit_days = (result['position'] == 0).sum()
        transitions = sum(1 for i in range(1, len(result))
                          if result['position'].iloc[i] != result['position'].iloc[i-1])
        logger.info(f"[{label}] NAV={nav:.0f}, 累计={result['cumulative_return'].iloc[-1]:.2f}%, "
                    f"清仓{exit_days}天, 切换{transitions}次")
        return result

    def run_ab_comparison(self,
                          index_daily: pd.DataFrame,
                          emotion_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        A/B/C 对比: 无过滤 vs 简单情绪过滤 vs 拐点策略

        Returns:
            {'baseline': df, 'emotion_filtered': df, 'reversal': df, 'buy_and_hold': df}
        """
        baseline = self.run_index_strategy(
            index_daily, emotion_df,
            use_emotion_filter=False,
            label='baseline_no_filter'
        )

        filtered = self.run_index_strategy(
            index_daily, emotion_df,
            use_emotion_filter=True,
            label='emotion_filtered'
        )

        reversal = self.run_reversal_strategy(
            index_daily, emotion_df,
            label='reversal_strategy'
        )

        emergency = self.run_emergency_exit_strategy(
            index_daily, emotion_df,
            label='emergency_exit'
        )

        # buy-and-hold benchmark
        bh = index_daily.sort_values('trade_date').copy()
        if 'pct_chg' not in bh.columns:
            bh['pct_chg'] = bh['close'].pct_change() * 100
        bh = bh.dropna(subset=['pct_chg']).reset_index(drop=True)
        bh['daily_return'] = bh['pct_chg']
        bh['nav'] = (1 + bh['pct_chg'] / 100).cumprod() * self.initial_capital
        bh['cumulative_return'] = (bh['nav'] / self.initial_capital - 1) * 100
        bh['label'] = 'buy_and_hold'
        bh['position'] = 1.0

        return {
            'baseline': baseline,
            'emotion_filtered': filtered,
            'reversal': reversal,
            'emergency_exit': emergency,
            'buy_and_hold': bh,
        }

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    @staticmethod
    def calc_metrics(result: pd.DataFrame, trading_days_per_year: int = 250) -> Dict:
        """计算绩效指标"""
        daily_ret = result['daily_return'] / 100
        n = len(daily_ret)

        total_ret = result['cumulative_return'].iloc[-1] / 100 if n > 0 else 0
        years = n / trading_days_per_year if n > 0 else 1
        annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0

        std = daily_ret.std()
        annual_vol = std * np.sqrt(trading_days_per_year) if std > 0 else 0
        sharpe = annual_ret / annual_vol if annual_vol > 0 else 0

        # max drawdown
        nav = result['nav']
        peak = nav.cummax()
        drawdown = (nav - peak) / peak
        max_dd = drawdown.min()

        calmar = annual_ret / abs(max_dd) if abs(max_dd) > 0 else 0

        # win rate
        win_rate = (daily_ret > 0).sum() / n * 100 if n > 0 else 0

        # profit/loss ratio
        wins = daily_ret[daily_ret > 0]
        losses = daily_ret[daily_ret < 0]
        pl_ratio = abs(wins.mean() / losses.mean()) if len(losses) > 0 and losses.mean() != 0 else 0

        return {
            'label': result['label'].iloc[0] if 'label' in result.columns else '',
            'total_return_pct': round(total_ret * 100, 2),
            'annual_return_pct': round(annual_ret * 100, 2),
            'annual_volatility_pct': round(annual_vol * 100, 2),
            'sharpe_ratio': round(sharpe, 3),
            'max_drawdown_pct': round(max_dd * 100, 2),
            'calmar_ratio': round(calmar, 3),
            'win_rate_pct': round(win_rate, 1),
            'profit_loss_ratio': round(pl_ratio, 2),
            'trading_days': n,
        }

    # ------------------------------------------------------------------
    # Multi-benchmark comparison
    # ------------------------------------------------------------------

    def run_multi_benchmark(self,
                            benchmarks: Dict[str, pd.DataFrame],
                            emotion_df: pd.DataFrame) -> pd.DataFrame:
        """
        多基准对比

        Args:
            benchmarks: {'沪深300': df, '创业板指': df, ...}
            emotion_df: 情绪评分

        Returns:
            汇总绩效对比表
        """
        all_metrics = []

        for name, idx_df in benchmarks.items():
            # buy and hold
            bh = self.run_index_strategy(idx_df, label=f'{name}_buyhold')
            all_metrics.append(self.calc_metrics(bh))

            # emotion filtered
            ef = self.run_index_strategy(idx_df, emotion_df,
                                         use_emotion_filter=True,
                                         label=f'{name}_emotion')
            all_metrics.append(self.calc_metrics(ef))

        return pd.DataFrame(all_metrics)
