#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪因子引擎 (F1-F7)

7 个因子从市场生态数据中提取不同维度的情绪信号:
  F1: 涨停生态活跃度 (正向)
  F2: 连板高度 (正向)
  F3: 涨跌比 (正向)
  F4: 跌停恐慌度 (反向)
  F5: 大面杀伤力 (反向)
  F6: 次日溢价率 (正向)
  F7: 炸板率 (反向)

每个因子输出 0-100 标准化分数。
"""

import logging
from typing import Dict, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Percentile-based normalisation windows (rolling days)
DEFAULT_ROLLING_WINDOW = 60


class EmotionFactors:
    """计算 F1-F7 情绪因子"""

    def __init__(self, rolling_window: int = DEFAULT_ROLLING_WINDOW):
        self.rolling_window = rolling_window

    # ------------------------------------------------------------------
    # Individual factors
    # ------------------------------------------------------------------

    @staticmethod
    def _clip_and_scale(series: pd.Series, low: float = 0, high: float = 100) -> pd.Series:
        return series.clip(low, high)

    def _rolling_percentile_rank(self, series: pd.Series) -> pd.Series:
        """将原始值映射到滚动窗口内的百分位排名 (0-100)，加入绝对值惩罚项以避免低波动期的虚假高分"""
        def _pct_rank(window):
            if len(window) < 2:
                return 50.0
            val = window.iloc[-1]
            # 基础排名
            rank = (window < val).sum()
            base_score = rank / (len(window) - 1) * 100
            
            # 加入绝对值惩罚：如果当前值低于历史中位数的一半，即使排名高也打折
            median_val = window.median()
            if val < median_val * 0.5 and median_val > 0:
                base_score *= (val / (median_val * 0.5))
                
            return base_score

        result = series.rolling(self.rolling_window, min_periods=10).apply(
            _pct_rank, raw=False
        )
        return result.fillna(50.0)

    def f1_limit_up_activity(self, ecology: pd.DataFrame) -> pd.Series:
        """F1: 涨停生态活跃度 — limit_up_count 的百分位"""
        return self._rolling_percentile_rank(ecology['limit_up_count'])

    def f2_board_height(self, ecology: pd.DataFrame) -> pd.Series:
        """F2: 连板高度 — max_board_height 的百分位"""
        return self._rolling_percentile_rank(ecology['max_board_height'])

    def f3_advance_decline(self, ecology: pd.DataFrame) -> pd.Series:
        """F3: 涨跌比 — ad_ratio 的百分位"""
        return self._rolling_percentile_rank(ecology['ad_ratio'])

    def f4_limit_down_panic(self, ecology: pd.DataFrame) -> pd.Series:
        """F4: 跌停恐慌度 (反向) — limit_down_count 越多分数越低"""
        raw = self._rolling_percentile_rank(ecology['limit_down_count'])
        return 100 - raw

    def f5_big_loss_damage(self, ecology: pd.DataFrame) -> pd.Series:
        """F5: 大面杀伤力 (反向) — big_loss_count 越多分数越低"""
        raw = self._rolling_percentile_rank(ecology['big_loss_count'])
        return 100 - raw

    def f6_next_day_premium(self, ecology: pd.DataFrame) -> pd.Series:
        """F6: 次日溢价率 — avg_return_limit_up_pct 的百分位"""
        return self._rolling_percentile_rank(ecology['avg_return_limit_up_pct'])

    def f7_broken_board_rate(self, ecology: pd.DataFrame) -> pd.Series:
        """F7: 炸板率 (反向) — broken / attempt 越高分数越低"""
        attempt = ecology['attempt_limit_up_count'].replace(0, np.nan)
        rate = ecology['broken_limit_count'] / attempt
        rate = rate.fillna(0.5)
        raw = self._rolling_percentile_rank(rate)
        return 100 - raw

    # --- New factors F8-F13, F15, F19, F20 ---

    def f8_realized_volatility(self, ecology: pd.DataFrame) -> pd.Series:
        """F8: 实现波动率 (反向) — 高波动 = 高恐慌, 分数低"""
        if 'realized_vol_20d' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        raw = self._rolling_percentile_rank(ecology['realized_vol_20d'])
        return 100 - raw

    def f9_turnover_anomaly(self, ecology: pd.DataFrame) -> pd.Series:
        """F9: 换手率异常 — 适度放量正向, 极端放量反向; 用倒U映射"""
        if 'turnover_anomaly' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        ta = ecology['turnover_anomaly'].fillna(1.0)
        # 0.8~1.3 is healthy; below 0.7 or above 2.0 is extreme
        score = 100 - ((ta - 1.05).abs() * 80).clip(0, 100)
        return self._clip_and_scale(score)

    def f10_volume_price_divergence(self, ecology: pd.DataFrame) -> pd.Series:
        """F10: 量价背离 (反向) — 放量下跌股越多分数越低"""
        if 'vol_price_diverge_pct' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        raw = self._rolling_percentile_rank(ecology['vol_price_diverge_pct'])
        return 100 - raw

    def f11_gap_frequency(self, ecology: pd.DataFrame) -> pd.Series:
        """F11: 跳空缺口 — 平均缺口方向, 正=乐观, 负=恐慌"""
        if 'avg_gap_pct' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        return self._rolling_percentile_rank(ecology['avg_gap_pct'])

    def f12_market_range(self, ecology: pd.DataFrame) -> pd.Series:
        """F12: 市场振幅 (反向) — 振幅越大市场越恐慌"""
        if 'avg_intraday_range' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        raw = self._rolling_percentile_rank(ecology['avg_intraday_range'])
        return 100 - raw

    def f13_rsi_extreme(self, ecology: pd.DataFrame) -> pd.Series:
        """F13: RSI极端占比 — 超买多=正向(但过高反转); 用超买-超卖差值"""
        if 'rsi_overbought_pct' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        net = ecology['rsi_overbought_pct'] - ecology['rsi_oversold_pct']
        return self._rolling_percentile_rank(net)

    def f15_margin_sentiment(self, ecology: pd.DataFrame) -> pd.Series:
        """F15: 融资余额变化率 — 加杠杆=乐观, 去杠杆=悲观"""
        if 'margin_balance_chg_pct' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        return self._rolling_percentile_rank(ecology['margin_balance_chg_pct'].fillna(0))

    def f19_sector_concentration(self, ecology: pd.DataFrame) -> pd.Series:
        """F19: 行业集中度 (反向) — 分化越大越不健康"""
        if 'sector_dispersion' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        raw = self._rolling_percentile_rank(ecology['sector_dispersion'])
        return 100 - raw

    def f20_new_high_low_ratio(self, ecology: pd.DataFrame) -> pd.Series:
        """F20: 创新高/创新低比 — 新高多=乐观"""
        if 'high_low_ratio' not in ecology.columns:
            return pd.Series(50.0, index=ecology.index)
        return self._rolling_percentile_rank(ecology['high_low_ratio'])

    # ------------------------------------------------------------------
    # Compute all
    # ------------------------------------------------------------------

    def compute_all(self, ecology: pd.DataFrame) -> pd.DataFrame:
        """
        计算全部 7 个因子

        Args:
            ecology: 生态特征 DataFrame (必须包含 trade_date 列)

        Returns:
            DataFrame with trade_date + F1..F7 columns (0-100)
        """
        ecology = ecology.sort_values('trade_date').reset_index(drop=True)

        factors = pd.DataFrame({
            'trade_date': ecology['trade_date'],
            'F1_limit_up': self._clip_and_scale(self.f1_limit_up_activity(ecology)),
            'F2_board_height': self._clip_and_scale(self.f2_board_height(ecology)),
            'F3_ad_ratio': self._clip_and_scale(self.f3_advance_decline(ecology)),
            'F4_limit_down': self._clip_and_scale(self.f4_limit_down_panic(ecology)),
            'F5_big_loss': self._clip_and_scale(self.f5_big_loss_damage(ecology)),
            'F6_premium': self._clip_and_scale(self.f6_next_day_premium(ecology)),
            'F7_broken_rate': self._clip_and_scale(self.f7_broken_board_rate(ecology)),
            'F8_realized_vol': self._clip_and_scale(self.f8_realized_volatility(ecology)),
            'F9_turnover': self._clip_and_scale(self.f9_turnover_anomaly(ecology)),
            'F10_vol_price_div': self._clip_and_scale(self.f10_volume_price_divergence(ecology)),
            'F11_gap': self._clip_and_scale(self.f11_gap_frequency(ecology)),
            'F12_mkt_range': self._clip_and_scale(self.f12_market_range(ecology)),
            'F13_rsi_extreme': self._clip_and_scale(self.f13_rsi_extreme(ecology)),
            'F15_margin': self._clip_and_scale(self.f15_margin_sentiment(ecology)),
            'F19_sector_conc': self._clip_and_scale(self.f19_sector_concentration(ecology)),
            'F20_new_hl_ratio': self._clip_and_scale(self.f20_new_high_low_ratio(ecology)),
        })

        logger.info(f"因子计算完成: {len(factors)} 天, {len(self.factor_columns())} 因子")
        return factors

    @staticmethod
    def factor_columns() -> list:
        return [
            'F1_limit_up', 'F2_board_height', 'F3_ad_ratio',
            'F4_limit_down', 'F5_big_loss', 'F6_premium', 'F7_broken_rate',
            'F8_realized_vol', 'F9_turnover', 'F10_vol_price_div',
            'F11_gap', 'F12_mkt_range', 'F13_rsi_extreme',
            'F15_margin', 'F19_sector_conc', 'F20_new_hl_ratio',
        ]
