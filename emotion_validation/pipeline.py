#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪验证管线 (Pipeline)

一键运行完整流程:
  数据加载 → 生态特征 → 情绪因子 → 回测 → 验证实验 → 报告

CLI:
    python -m emotion_validation.pipeline
    python -m emotion_validation.pipeline --benchmark 000300
    python -m emotion_validation.pipeline --skip-fetch
"""

import sys
import logging
import argparse
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / 'backtest' / 'data'


def run_pipeline(data_dir: str = None,
                 benchmark_symbol: str = '000300',
                 skip_fetch: bool = True,
                 output_dir: str = None) -> dict:
    """
    运行完整情绪验证管线

    Args:
        data_dir: 数据目录
        benchmark_symbol: 基准指数代码
        skip_fetch: 跳过数据拉取 (使用本地缓存)
        output_dir: 报告输出目录

    Returns:
        dict: 管线各阶段输出
    """
    data_path = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    # ----------------------------------------------------------
    # Step 1: Load or build ecology data
    # ----------------------------------------------------------
    logger.info("Step 1: 加载生态特征...")
    from .data_layer.historical_ecology_builder import HistoricalEcologyBuilder

    builder = HistoricalEcologyBuilder(data_dir=str(data_path))
    ecology = builder.load_ecology()
    logger.info(f"  生态特征: {len(ecology)} 天")

    # ----------------------------------------------------------
    # Step 2: Compute emotion factors & score
    # ----------------------------------------------------------
    logger.info("Step 2: 计算情绪因子与评分...")
    from .emotion_engine.emotion_score import EmotionScorer

    scorer = EmotionScorer()
    emotion_df = scorer.compute_with_reversal(ecology)
    logger.info(f"  情绪评分: {len(emotion_df)} 天")
    logger.info(f"  分数范围: {emotion_df['emotion_score'].min():.1f} ~ "
                f"{emotion_df['emotion_score'].max():.1f}")

    # ----------------------------------------------------------
    # Step 3: Load index data
    # ----------------------------------------------------------
    logger.info("Step 3: 加载指数数据...")
    from .data_layer.data_warehouse import DataWarehouse

    warehouse = DataWarehouse(data_dir=str(data_path))
    index_daily = warehouse.load_index_daily(symbol=benchmark_symbol)

    if 'pct_chg' not in index_daily.columns:
        index_daily = index_daily.sort_values('trade_date')
        index_daily['pct_chg'] = index_daily['close'].pct_change() * 100

    logger.info(f"  指数数据: {benchmark_symbol}, {len(index_daily)} 天")

    # ----------------------------------------------------------
    # Step 4: Run validation experiments
    # ----------------------------------------------------------
    logger.info("Step 4: 运行验证实验...")
    from .validation.experiment_suite import ExperimentSuite

    suite = ExperimentSuite(emotion_df, index_daily)
    experiment_results = suite.run_all()

    # ----------------------------------------------------------
    # Step 5: Generate reports
    # ----------------------------------------------------------
    logger.info("Step 5: 生成报告...")
    from .reporting.emotion_report import EmotionReport

    report_dir = output_dir or str(data_path.parent / 'reports' / 'emotion_validation')
    reporter = EmotionReport(output_dir=report_dir)
    report_text = reporter.generate_all(experiment_results, emotion_df)

    print("\n" + report_text)

    # ----------------------------------------------------------
    # Step 6: Multi-benchmark (optional)
    # ----------------------------------------------------------
    logger.info("Step 6: 多基准对比...")
    from .backtest_engine.simulator import EmotionBacktestSimulator

    sim = EmotionBacktestSimulator()
    benchmarks = {}
    for sym in ['000300', '399006', '000852']:
        try:
            idx = warehouse.load_index_daily(symbol=sym)
            if 'pct_chg' not in idx.columns:
                idx = idx.sort_values('trade_date')
                idx['pct_chg'] = idx['close'].pct_change() * 100
            benchmarks[sym] = idx
        except Exception as e:
            logger.warning(f"加载指数 {sym} 失败: {e}")

    if benchmarks:
        comparison = sim.run_multi_benchmark(benchmarks, emotion_df)
        comparison.to_csv(Path(report_dir) / 'multi_benchmark.csv',
                          index=False, encoding='utf-8-sig')
        logger.info(f"多基准对比:\n{comparison.to_string(index=False)}")

    # ----------------------------------------------------------
    # Step 7: Reversal strategy A/B/C comparison
    # ----------------------------------------------------------
    logger.info("Step 7: 拐点策略 A/B/C 对比...")
    abc = sim.run_ab_comparison(index_daily, emotion_df)

    abc_metrics = []
    for name, df in abc.items():
        m = sim.calc_metrics(df)
        abc_metrics.append(m)
    abc_table = pd.DataFrame(abc_metrics)
    abc_table.to_csv(Path(report_dir) / 'abc_comparison.csv',
                     index=False, encoding='utf-8-sig')

    logger.info("A/B/C 策略对比:")
    for _, row in abc_table.iterrows():
        lbl = row['label']
        logger.info(f"  [{lbl}] 年化={row['annual_return_pct']:.1f}%, "
                    f"回撤={row['max_drawdown_pct']:.1f}%, "
                    f"Sharpe={row['sharpe_ratio']:.3f}")

    # 拐点信号统计
    if 'regime_reversal' in emotion_df.columns:
        rev_counts = emotion_df['regime_reversal'].value_counts()
        logger.info(f"拐点信号分布: {rev_counts.to_dict()}")

    logger.info("=== Pipeline 完成 ===")

    return {
        'ecology': ecology,
        'emotion_df': emotion_df,
        'experiment_results': experiment_results,
        'report_text': report_text,
    }


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description='情绪验证管线',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python -m emotion_validation.pipeline
    python -m emotion_validation.pipeline --benchmark 399006
    python -m emotion_validation.pipeline --data-dir backtest/data --output-dir backtest/reports
'''
    )

    parser.add_argument('--data-dir', default=None, help='数据目录')
    parser.add_argument('--benchmark', default='000300', help='基准指数 (默认: 000300)')
    parser.add_argument('--output-dir', default=None, help='报告输出目录')
    parser.add_argument('--skip-fetch', action='store_true', default=True,
                        help='跳过数据拉取 (默认)')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
    )

    run_pipeline(
        data_dir=args.data_dir,
        benchmark_symbol=args.benchmark,
        skip_fetch=args.skip_fetch,
        output_dir=args.output_dir,
    )


if __name__ == '__main__':
    main()
