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
        简化版评分：移除过度拟合的闪崩和衰竭逻辑，使用平滑均线动量判断趋势。
        
        五档信号:
          high                 : 情绪 >= 65, 满仓进攻
          medium               : 40 <= 情绪 < 65, 半仓
          low_stable           : 情绪 < 40, 且短期趋势向上 → 轻仓观望
          low_falling          : 情绪 < 40, 且短期趋势向下 → 空仓清仓
          reversal_buy         : 极度悲观且趋势强烈向上 → 重仓抄底
        """
        base = self.compute(ecology)
        params = reversal_params or REVERSAL_PARAMS.copy()

        score = base['emotion_score']
        
        # 使用 3日平滑均线 来判断趋势，而不是单日跳动
        ema_3 = score.ewm(span=3, adjust=False).mean()
        chg_ema = ema_3.diff()
        
        # 判断是否处于低位
        is_low = score < params['low_threshold']
        
        # 极度悲观回升
        was_extreme = score.rolling(5, min_periods=1).min() < params['extreme_low_threshold']
        reversal_signal = is_low & was_extreme & (chg_ema > 0) & (score.diff(3) > params['rising_delta_3d'])

        # 仍在恶化
        still_falling = is_low & (chg_ema <= 0) & ~reversal_signal
        
        # 低位企稳
        low_stable = is_low & (chg_ema > 0) & ~reversal_signal

        regime_rev = base['regime'].copy()
        regime_rev[reversal_signal] = 'reversal_buy'
        regime_rev[still_falling] = 'low_falling'
        regime_rev[low_stable] = 'low_stable'

        base['regime_reversal'] = regime_rev
        base['position_pct_reversal'] = regime_rev.map(POSITION_MAP_REVERSAL).fillna(base['position_pct'])
        base['score_chg_1d'] = score.diff().round(2)
        base['score_chg_3d'] = score.diff(3).round(2)
        
        # 补齐为了兼容旧接口的列
        chg_ema_is_pos = chg_ema > 0
        base['rising_streak'] = chg_ema_is_pos.groupby((~chg_ema_is_pos).cumsum()).cumsum()
        
        is_extreme_low = score < params['exhaustion_threshold']
        base['freeze_streak'] = is_extreme_low.groupby((~is_extreme_low).cumsum()).cumsum()

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
