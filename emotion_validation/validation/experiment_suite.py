#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证实验套件

4 类核心实验:
  Exp1: Regime 分组表现对比 (high/medium/low 期间的指数收益)
  Exp2: A/B 对比 (无过滤 vs 情绪过滤)
  Exp3: 滞后预测能力 (Rank IC / Spearman corr)
  Exp4: ROC-AUC (情绪分数对正负收益的分类能力)

附加:
  - 周期验证: 日/周级别
  - 阈值扰动敏感性
  - 时间分段 (牛/震荡/熊) 稳健性
"""

import logging
from typing import Dict, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class ExperimentSuite:
    """验证实验套件"""

    def __init__(self, emotion_df: pd.DataFrame, index_daily: pd.DataFrame):
        """
        Args:
            emotion_df: 情绪评分表 (trade_date, emotion_score, regime, F1..F7)
            index_daily: 指数日线 (trade_date, close, pct_chg)
        """
        self.emotion_df = emotion_df.sort_values('trade_date').reset_index(drop=True)
        self.index_daily = index_daily.sort_values('trade_date').reset_index(drop=True)

        if 'pct_chg' not in self.index_daily.columns:
            self.index_daily['pct_chg'] = self.index_daily['close'].pct_change() * 100

        self._merged = self._merge_data()

    def _merge_data(self) -> pd.DataFrame:
        """合并情绪分数与指数收益，确保 T-1 对齐"""
        emo = self.emotion_df[['trade_date', 'emotion_score', 'regime']].copy()
        idx = self.index_daily[['trade_date', 'pct_chg', 'close']].copy()

        # T-1 emotion -> T return
        emo['next_trade_date'] = emo['trade_date'].shift(-1)
        merged = idx.merge(
            emo[['next_trade_date', 'emotion_score', 'regime']],
            left_on='trade_date', right_on='next_trade_date',
            how='inner'
        )
        merged = merged.drop(columns=['next_trade_date']).dropna()
        return merged

    # ------------------------------------------------------------------
    # Exp 1: Regime 分组对比
    # ------------------------------------------------------------------

    def exp1_regime_compare(self) -> pd.DataFrame:
        """
        按 regime 分组, 统计各组期间的指数日均收益、胜率、波动率
        """
        df = self._merged
        results = []

        for regime in ['high', 'medium', 'low']:
            group = df[df['regime'] == regime]
            if len(group) == 0:
                continue
            daily_ret = group['pct_chg']
            results.append({
                'regime': regime,
                'trading_days': len(group),
                'pct_of_total': round(len(group) / len(df) * 100, 1),
                'avg_daily_return': round(daily_ret.mean(), 4),
                'cumulative_return': round((1 + daily_ret / 100).prod() - 1, 4) * 100,
                'win_rate': round((daily_ret > 0).sum() / len(daily_ret) * 100, 1),
                'volatility': round(daily_ret.std(), 4),
                'avg_emotion_score': round(group['emotion_score'].mean(), 1),
            })

        result = pd.DataFrame(results)
        logger.info(f"Exp1 Regime分组:\n{result.to_string(index=False)}")
        return result

    # ------------------------------------------------------------------
    # Exp 2: A/B 对比
    # ------------------------------------------------------------------

    def exp2_ab_comparison(self) -> Dict:
        """
        A/B 对比: 始终满仓(baseline) vs 按regime调仓(filtered)
        """
        from ..backtest_engine.simulator import EmotionBacktestSimulator

        sim = EmotionBacktestSimulator()
        results = sim.run_ab_comparison(self.index_daily, self.emotion_df)

        metrics = {}
        for key, df in results.items():
            metrics[key] = sim.calc_metrics(df)

        logger.info("Exp2 A/B对比:")
        for k, m in metrics.items():
            logger.info(f"  [{k}] 年化={m['annual_return_pct']}%, "
                        f"回撤={m['max_drawdown_pct']}%, Sharpe={m['sharpe_ratio']}")

        return {'nav_series': results, 'metrics': metrics}

    # ------------------------------------------------------------------
    # Exp 3: 滞后预测能力 (Rank IC)
    # ------------------------------------------------------------------

    def exp3_lag_prediction(self, max_lag: int = 5) -> pd.DataFrame:
        """
        检验 emotion_score(T) 对 return(T+k) 的预测能力

        Returns:
            DataFrame: lag, rank_ic, p_value
        """
        from scipy.stats import spearmanr

        df = self._merged.copy()
        results = []

        for lag in range(1, max_lag + 1):
            df[f'ret_lag{lag}'] = df['pct_chg'].shift(-lag)
            valid = df.dropna(subset=[f'ret_lag{lag}', 'emotion_score'])

            if len(valid) < 20:
                continue

            corr, p_value = spearmanr(valid['emotion_score'], valid[f'ret_lag{lag}'])
            results.append({
                'lag': lag,
                'rank_ic': round(corr, 4),
                'p_value': round(p_value, 4),
                'significant': p_value < 0.05,
                'n': len(valid),
            })

        result = pd.DataFrame(results)
        logger.info(f"Exp3 Rank IC:\n{result.to_string(index=False)}")
        return result

    # ------------------------------------------------------------------
    # Exp 4: ROC-AUC
    # ------------------------------------------------------------------

    def exp4_roc_auc(self) -> Dict:
        """
        情绪分数对次日正/负收益的二分类能力

        Returns:
            dict: auc, accuracy, confusion matrix
        """
        from sklearn.metrics import roc_auc_score, accuracy_score, confusion_matrix

        df = self._merged.copy()
        df['label'] = (df['pct_chg'] > 0).astype(int)

        score = df['emotion_score'].values
        label = df['label'].values

        if len(np.unique(label)) < 2:
            logger.warning("标签无区分度, 无法计算 AUC")
            return {'auc': 0.5, 'accuracy': 0, 'n': len(df)}

        auc = roc_auc_score(label, score)

        pred = (score >= 50).astype(int)
        acc = accuracy_score(label, pred)
        cm = confusion_matrix(label, pred)

        result = {
            'auc': round(auc, 4),
            'accuracy': round(acc, 4),
            'confusion_matrix': cm.tolist(),
            'n': len(df),
            'positive_rate': round(label.mean(), 3),
        }

        logger.info(f"Exp4 ROC-AUC: AUC={auc:.4f}, Accuracy={acc:.4f}")
        return result

    # ------------------------------------------------------------------
    # Time-period robustness
    # ------------------------------------------------------------------

    def exp_time_robustness(self, periods: Dict[str, tuple] = None) -> pd.DataFrame:
        """
        时间分段稳健性: 对不同市场阶段重复 regime 实验

        Args:
            periods: {'牛市': ('2024-09-01', '2024-12-31'), '震荡': (...), ...}
        """
        if periods is None:
            periods = {
                'H1_2024': ('2024-01-01', '2024-06-30'),
                'H2_2024': ('2024-07-01', '2024-12-31'),
                'H1_2025': ('2025-01-01', '2025-06-30'),
                'H2_2025': ('2025-07-01', '2025-12-31'),
            }

        all_results = []
        for name, (start, end) in periods.items():
            sub = self._merged[
                (self._merged['trade_date'] >= pd.to_datetime(start)) &
                (self._merged['trade_date'] <= pd.to_datetime(end))
            ]
            if len(sub) < 10:
                continue

            for regime in ['high', 'medium', 'low']:
                g = sub[sub['regime'] == regime]
                if len(g) == 0:
                    continue
                all_results.append({
                    'period': name,
                    'regime': regime,
                    'days': len(g),
                    'avg_return': round(g['pct_chg'].mean(), 4),
                    'cumulative': round((1 + g['pct_chg'] / 100).prod() - 1, 4) * 100,
                    'win_rate': round((g['pct_chg'] > 0).sum() / len(g) * 100, 1),
                })

        result = pd.DataFrame(all_results)
        logger.info(f"时间分段稳健性:\n{result.to_string(index=False)}")
        return result

    # ------------------------------------------------------------------
    # Run all experiments
    # ------------------------------------------------------------------

    def run_all(self) -> Dict:
        """运行全部实验"""
        logger.info("=== 开始全部验证实验 ===")

        results = {}
        results['exp1_regime'] = self.exp1_regime_compare()
        results['exp2_ab'] = self.exp2_ab_comparison()
        results['exp3_lag'] = self.exp3_lag_prediction()
        results['exp4_auc'] = self.exp4_roc_auc()
        results['time_robustness'] = self.exp_time_robustness()

        logger.info("=== 全部实验完成 ===")
        return results
