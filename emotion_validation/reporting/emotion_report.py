#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪验证研究报告生成

输出:
  - 文本摘要报告
  - CSV 详细数据
  - 图表 (需 matplotlib)
"""

import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class EmotionReport:
    """情绪验证研究报告"""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path('backtest/reports/emotion_validation')
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Text report
    # ------------------------------------------------------------------

    def generate_text_report(self, experiment_results: Dict,
                              emotion_df: pd.DataFrame = None) -> str:
        """生成文本摘要报告"""
        lines = []
        lines.append("=" * 70)
        lines.append("情绪验证研究报告")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)

        # Exp1: Regime
        if 'exp1_regime' in experiment_results:
            lines.append("\n--- 实验1: Regime 分组表现 ---")
            df = experiment_results['exp1_regime']
            for _, row in df.iterrows():
                lines.append(
                    f"  {row['regime']:>7s}: {row['trading_days']:>4d}天 "
                    f"({row['pct_of_total']:>5.1f}%), "
                    f"日均收益={row['avg_daily_return']:>7.4f}%, "
                    f"累计={row['cumulative_return']:>7.2f}%, "
                    f"胜率={row['win_rate']:>5.1f}%"
                )

        # Exp2: A/B
        if 'exp2_ab' in experiment_results:
            lines.append("\n--- 实验2: A/B 对比 ---")
            metrics = experiment_results['exp2_ab'].get('metrics', {})
            for key, m in metrics.items():
                lines.append(
                    f"  [{key}]: "
                    f"年化={m['annual_return_pct']:>6.2f}%, "
                    f"回撤={m['max_drawdown_pct']:>6.2f}%, "
                    f"Sharpe={m['sharpe_ratio']:>5.3f}, "
                    f"胜率={m['win_rate_pct']:>5.1f}%"
                )

        # Exp3: Rank IC
        if 'exp3_lag' in experiment_results:
            lines.append("\n--- 实验3: 滞后预测 (Rank IC) ---")
            df = experiment_results['exp3_lag']
            for _, row in df.iterrows():
                sig = "*" if row.get('significant', False) else " "
                lines.append(
                    f"  Lag={row['lag']}: IC={row['rank_ic']:>7.4f}, "
                    f"p={row['p_value']:>6.4f} {sig}"
                )

        # Exp4: AUC
        if 'exp4_auc' in experiment_results:
            lines.append("\n--- 实验4: ROC-AUC ---")
            auc_res = experiment_results['exp4_auc']
            lines.append(f"  AUC = {auc_res['auc']:.4f}")
            lines.append(f"  Accuracy = {auc_res['accuracy']:.4f}")
            lines.append(f"  样本数 = {auc_res['n']}")

        # Time robustness
        if 'time_robustness' in experiment_results:
            lines.append("\n--- 时间分段稳健性 ---")
            df = experiment_results['time_robustness']
            if not df.empty:
                for period in df['period'].unique():
                    lines.append(f"  [{period}]")
                    sub = df[df['period'] == period]
                    for _, row in sub.iterrows():
                        lines.append(
                            f"    {row['regime']:>7s}: {row['days']:>3d}天, "
                            f"累计={row['cumulative']:>7.2f}%, "
                            f"胜率={row['win_rate']:>5.1f}%"
                        )

        lines.append("\n" + "=" * 70)

        # Conclusion
        if 'exp1_regime' in experiment_results:
            regime_df = experiment_results['exp1_regime']
            high_row = regime_df[regime_df['regime'] == 'high']
            low_row = regime_df[regime_df['regime'] == 'low']
            if not high_row.empty and not low_row.empty:
                h_ret = high_row.iloc[0]['avg_daily_return']
                l_ret = low_row.iloc[0]['avg_daily_return']
                spread = h_ret - l_ret
                lines.append(f"\n结论: high-low 日均收益差 = {spread:.4f}%")
                if spread > 0.05:
                    lines.append("  -> 情绪因子具有显著区分能力")
                elif spread > 0:
                    lines.append("  -> 情绪因子有一定区分能力，但不够显著")
                else:
                    lines.append("  -> 情绪因子在当前参数下无显著区分能力")

        report_text = "\n".join(lines)

        report_path = self.output_dir / 'emotion_validation_report.txt'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_text)

        logger.info(f"文本报告: {report_path}")
        return report_text

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(self, experiment_results: Dict, emotion_df: pd.DataFrame = None):
        """导出详细 CSV 数据"""
        if emotion_df is not None:
            path = self.output_dir / 'emotion_scores.csv'
            emotion_df.to_csv(path, index=False, encoding='utf-8-sig')
            logger.info(f"情绪分数: {path}")

        for key in ['exp1_regime', 'exp3_lag', 'time_robustness']:
            if key in experiment_results and isinstance(experiment_results[key], pd.DataFrame):
                path = self.output_dir / f'{key}.csv'
                experiment_results[key].to_csv(path, index=False, encoding='utf-8-sig')
                logger.info(f"  {key}: {path}")

        if 'exp2_ab' in experiment_results:
            metrics = experiment_results['exp2_ab'].get('metrics', {})
            if metrics:
                path = self.output_dir / 'exp2_ab_metrics.csv'
                pd.DataFrame(metrics.values()).to_csv(path, index=False, encoding='utf-8-sig')

            nav_series = experiment_results['exp2_ab'].get('nav_series', {})
            for label, df in nav_series.items():
                path = self.output_dir / f'exp2_nav_{label}.csv'
                df.to_csv(path, index=False, encoding='utf-8-sig')

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------

    def generate_charts(self, experiment_results: Dict, emotion_df: pd.DataFrame = None):
        """生成图表"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial']
            plt.rcParams['axes.unicode_minus'] = False
        except ImportError:
            logger.warning("matplotlib 未安装, 跳过图表")
            return

        # Chart 1: NAV curves
        if 'exp2_ab' in experiment_results:
            nav_series = experiment_results['exp2_ab'].get('nav_series', {})
            if nav_series:
                fig, ax = plt.subplots(figsize=(14, 6))
                for label, df in nav_series.items():
                    ax.plot(df['trade_date'], df['nav'], label=label, linewidth=1.2)
                ax.set_title('A/B Comparison: NAV Curves')
                ax.set_xlabel('Date')
                ax.set_ylabel('NAV')
                ax.legend()
                ax.grid(True, alpha=0.3)
                fig.tight_layout()
                fig.savefig(self.output_dir / 'nav_comparison.png', dpi=150)
                plt.close(fig)
                logger.info(f"  NAV图: {self.output_dir / 'nav_comparison.png'}")

        # Chart 2: Emotion score time series
        if emotion_df is not None:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

            ax1.plot(emotion_df['trade_date'], emotion_df['emotion_score'],
                     color='steelblue', linewidth=0.8)
            ax1.axhline(y=65, color='green', linestyle='--', alpha=0.5, label='High threshold')
            ax1.axhline(y=40, color='red', linestyle='--', alpha=0.5, label='Low threshold')
            ax1.fill_between(emotion_df['trade_date'], 65, 100,
                             alpha=0.1, color='green')
            ax1.fill_between(emotion_df['trade_date'], 0, 40,
                             alpha=0.1, color='red')
            ax1.set_ylabel('Emotion Score')
            ax1.set_title('Emotion Score & Regime Timeline')
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            if 'position_pct' in emotion_df.columns:
                ax2.fill_between(emotion_df['trade_date'], 0,
                                 emotion_df['position_pct'] * 100,
                                 alpha=0.4, color='steelblue')
                ax2.set_ylabel('Position %')
                ax2.set_xlabel('Date')
                ax2.grid(True, alpha=0.3)

            fig.tight_layout()
            fig.savefig(self.output_dir / 'emotion_timeline.png', dpi=150)
            plt.close(fig)
            logger.info(f"  情绪时间线: {self.output_dir / 'emotion_timeline.png'}")

        # Chart 3: Regime bar chart
        if 'exp1_regime' in experiment_results:
            df = experiment_results['exp1_regime']
            if not df.empty:
                fig, axes = plt.subplots(1, 3, figsize=(14, 5))
                colors = {'high': 'green', 'medium': 'orange', 'low': 'red'}

                for i, (metric, title) in enumerate([
                    ('avg_daily_return', 'Avg Daily Return (%)'),
                    ('cumulative_return', 'Cumulative Return (%)'),
                    ('win_rate', 'Win Rate (%)'),
                ]):
                    ax = axes[i]
                    bars = ax.bar(df['regime'], df[metric],
                                  color=[colors.get(r, 'gray') for r in df['regime']])
                    ax.set_title(title)
                    ax.grid(True, alpha=0.3, axis='y')

                fig.suptitle('Regime Performance Comparison', fontsize=14)
                fig.tight_layout()
                fig.savefig(self.output_dir / 'regime_comparison.png', dpi=150)
                plt.close(fig)
                logger.info(f"  Regime对比: {self.output_dir / 'regime_comparison.png'}")

    # ------------------------------------------------------------------
    # Generate all
    # ------------------------------------------------------------------

    def generate_all(self, experiment_results: Dict,
                     emotion_df: pd.DataFrame = None) -> str:
        """生成全部报告"""
        report_text = self.generate_text_report(experiment_results, emotion_df)
        self.export_csv(experiment_results, emotion_df)
        self.generate_charts(experiment_results, emotion_df)
        return report_text
