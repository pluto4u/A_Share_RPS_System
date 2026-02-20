#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪综合评分与 Regime 分类 (含拐点回升检测)

将 F1-F16 因子加权汇总为 0-100 情绪分数，并划分为五档信号:
  reversal_buy : 极度悲观后回升拐点 → 进场信号
  low_falling  : 悲观且仍在恶化 → 清仓信号
  low_stable   : 低位企稳 → 观望
  medium       : 中性 → 轻仓
  high         : 乐观 → 进攻
"""

import logging
from typing import Dict, Optional, Tuple

import pandas as pd
import numpy as np

from .emotion_factors import EmotionFactors

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    # Original 7 factors (55% total)
    'F1_limit_up':      0.10,
    'F2_board_height':  0.05,
    'F3_ad_ratio':      0.12,
    'F4_limit_down':    0.08,
    'F5_big_loss':      0.05,
    'F6_premium':       0.07,
    'F7_broken_rate':   0.08,
    # New factors (45% total)
    'F8_realized_vol':  0.08,   # VIX proxy
    'F9_turnover':      0.04,   # turnover anomaly
    'F10_vol_price_div': 0.06,  # volume-price divergence
    'F11_gap':          0.04,   # overnight gap
    'F12_mkt_range':    0.04,   # intraday range
    'F13_rsi_extreme':  0.05,   # RSI extremes
    'F15_margin':       0.06,   # margin sentiment
    'F19_sector_conc':  0.03,   # sector concentration
    'F20_new_hl_ratio': 0.05,   # new high/low
}

DEFAULT_REGIME_THRESHOLDS = {
    'high': 65,
    'low': 40,
}

POSITION_MAP = {
    'high': 1.0,
    'medium': 0.8,
    'low': 0.0,
}

POSITION_MAP_REVERSAL = {
    'high':         1.0,
    'medium':       0.8,    # medium 有正收益，不该大幅减仓
    'low_stable':   0.3,    # 低位企稳，轻仓观望
    'low_falling':  0.0,    # 仍在恶化，空仓
    'reversal_buy': 0.8,    # 极度悲观后拐点回升，重仓进场
    'flash_crash_reversal': 1.0, # 闪崩后快速反转，满仓吃修复
    'exhaustion_reversal':  0.8, # 连续冰点博弈反转，重仓
}

REVERSAL_PARAMS = {
    'extreme_low_threshold': 25,  # 极度悲观线
    'low_threshold': 40,          # 悲观区
    'rising_delta_3d': 5,         # 3 日回升幅度
    'rising_delta_1d': 2,         # 单日回升幅度
    'consecutive_rise': 2,        # 连续回升天数
    'flash_crash_drop_2d': -40,   # 闪崩型: 2日内得分剧烈跳水阈值
    'flash_crash_threshold': 15,  # 闪崩型: 砸破的绝对极寒线
    'exhaustion_days': 3,         # 衰竭型: 极寒天数要求 (事不过三)
    'exhaustion_threshold': 15,   # 衰竭型: 极寒线
}


class EmotionScorer:
    """情绪综合评分 + Regime 分类"""

    def __init__(self,
                 weights: Dict[str, float] = None,
                 regime_thresholds: Dict[str, float] = None,
                 rolling_window: int = 60):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.thresholds = regime_thresholds or DEFAULT_REGIME_THRESHOLDS.copy()
        self.factor_engine = EmotionFactors(rolling_window=rolling_window)

        total_w = sum(self.weights.values())
        if abs(total_w - 1.0) > 0.01:
            logger.warning(f"权重之和 {total_w:.3f} != 1.0, 将自动归一化")
            for k in self.weights:
                self.weights[k] /= total_w

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def compute(self, ecology: pd.DataFrame) -> pd.DataFrame:
        """
        端到端计算: 生态 → 因子 → 分数 → regime → 仓位建议

        Args:
            ecology: 生态特征 DataFrame

        Returns:
            DataFrame: trade_date, F1..F7, emotion_score, regime, position_pct
        """
        factors = self.factor_engine.compute_all(ecology)

        factor_cols = EmotionFactors.factor_columns()
        score = pd.Series(0.0, index=factors.index)
        for col in factor_cols:
            w = self.weights.get(col, 0)
            score += factors[col] * w

        factors['emotion_score'] = score.clip(0, 100).round(2)

        high_th = self.thresholds['high']
        low_th = self.thresholds['low']
        factors['regime'] = np.where(
            factors['emotion_score'] >= high_th, 'high',
            np.where(factors['emotion_score'] < low_th, 'low', 'medium')
        )

        factors['position_pct'] = factors['regime'].map(POSITION_MAP)

        logger.info(
            f"评分完成: "
            f"high={( factors['regime'] == 'high').sum()}, "
            f"medium={( factors['regime'] == 'medium').sum()}, "
            f"low={( factors['regime'] == 'low').sum()}"
        )
        return factors

    # ------------------------------------------------------------------
        # Reversal-aware regime (五档信号 + 两种特殊冰点)
    # ------------------------------------------------------------------

    def compute_with_reversal(self, ecology: pd.DataFrame,
                              reversal_params: dict = None) -> pd.DataFrame:
        """
        带拐点检测的增强版评分

        五档信号:
          high                 : 情绪 >= 65, 满仓进攻
          medium               : 40 <= 情绪 < 65, 半仓
          low_stable           : 情绪 < 40, 但已企稳 → 轻仓观望
          low_falling          : 情绪 < 40, 且仍在下降 → 空仓清仓
          reversal_buy         : 情绪从极低位开始回升 → 重仓抄底
          flash_crash_reversal : 情绪2日内闪崩并出现右侧火种 → 满仓吃修复
          exhaustion_reversal  : 情绪连续3天极寒并出现微弱火种 → 重仓博弈
        """
        base = self.compute(ecology)
        params = reversal_params or REVERSAL_PARAMS.copy()

        score = base['emotion_score']
        
        chg_1d = score.diff()
        chg_2d = score.diff(2)
        chg_3d = score.diff(3)
        rising_streak = pd.Series(0, index=score.index)
        streak = 0
        for i in range(len(chg_1d)):
            if i > 0 and chg_1d.iloc[i] > 0:
                streak += 1
            else:
                streak = 0
            rising_streak.iloc[i] = streak
            
        # Freeze streak counter for exhaustion logic (事不过三)
        freeze_streak = pd.Series(0, index=score.index)
        f_streak = 0
        for i in range(len(score)):
            if score.iloc[i] < params['exhaustion_threshold']:
                f_streak += 1
            else:
                f_streak = 0
            freeze_streak.iloc[i] = f_streak

        # Was recently at extreme low (within last 5 days)
        rolling_min_5d = score.rolling(5, min_periods=1).min()
        was_extreme = rolling_min_5d < params['extreme_low_threshold']

        is_low = score < params['low_threshold']
        is_rising_3d = chg_3d > params['rising_delta_3d']
        is_rising_1d = chg_1d > params['rising_delta_1d']
        has_rising_streak = rising_streak >= params['consecutive_rise']

        # 1. 闪崩型反转 (Flash Crash)
        # 近2日暴跌且跌破极寒线，但今日已企稳或回升(右侧火种)
        is_flash_crash = (chg_2d <= params['flash_crash_drop_2d']) & (score < params['flash_crash_threshold'])
        is_flash_crash_reversal = is_flash_crash & (chg_1d > 0)
        
        # 2. 衰竭型反转 (Exhaustion / 事不过三)
        # 连续 >=3 天在极寒线下，且今天出现环比回升 (微弱火种)
        is_exhaustion = freeze_streak >= params['exhaustion_days']
        is_exhaustion_reversal = is_exhaustion & (chg_1d > 0)

        # 3. 常规反转 (普通左侧回升)
        reversal_signal = (
            is_low
            & was_extreme
            & (is_rising_3d | (is_rising_1d & has_rising_streak))
            & ~is_flash_crash_reversal
            & ~is_exhaustion_reversal
        )

        still_falling = is_low & (chg_3d < 0) & ~reversal_signal & ~is_flash_crash_reversal & ~is_exhaustion_reversal
        low_stable = is_low & ~still_falling & ~reversal_signal & ~is_flash_crash_reversal & ~is_exhaustion_reversal

        regime_rev = base['regime'].copy()
        regime_rev[reversal_signal] = 'reversal_buy'
        regime_rev[still_falling] = 'low_falling'
        regime_rev[low_stable] = 'low_stable'
        regime_rev[is_flash_crash_reversal] = 'flash_crash_reversal'
        regime_rev[is_exhaustion_reversal] = 'exhaustion_reversal'

        base['regime_reversal'] = regime_rev
        base['position_pct_reversal'] = regime_rev.map(POSITION_MAP_REVERSAL)
        base['score_chg_1d'] = chg_1d.round(2)
        base['score_chg_3d'] = chg_3d.round(2)
        base['rising_streak'] = rising_streak
        base['freeze_streak'] = freeze_streak

        counts = base['regime_reversal'].value_counts()
        logger.info(f"拐点Regime: {counts.to_dict()}")

        return base

    # ------------------------------------------------------------------
    # Threshold sensitivity
    # ------------------------------------------------------------------

    def sensitivity_analysis(self, ecology: pd.DataFrame,
                             threshold_range: list = None) -> pd.DataFrame:
        """
        阈值敏感性分析: 不同 high/low 阈值下的 regime 分布

        Args:
            ecology: 生态特征
            threshold_range: [(high, low), ...] 阈值组合

        Returns:
            DataFrame: high_th, low_th, pct_high, pct_medium, pct_low
        """
        if threshold_range is None:
            threshold_range = [
                (60, 35), (60, 40), (65, 35), (65, 40),
                (65, 45), (70, 40), (70, 45),
            ]

        factors = self.factor_engine.compute_all(ecology)
        factor_cols = EmotionFactors.factor_columns()

        score = pd.Series(0.0, index=factors.index)
        for col in factor_cols:
            score += factors[col] * self.weights.get(col, 0)
        score = score.clip(0, 100)

        results = []
        n = len(score)
        for high_th, low_th in threshold_range:
            pct_h = (score >= high_th).sum() / n * 100
            pct_l = (score < low_th).sum() / n * 100
            pct_m = 100 - pct_h - pct_l
            results.append({
                'high_threshold': high_th,
                'low_threshold': low_th,
                'pct_high': round(pct_h, 1),
                'pct_medium': round(pct_m, 1),
                'pct_low': round(pct_l, 1),
            })

        return pd.DataFrame(results)
