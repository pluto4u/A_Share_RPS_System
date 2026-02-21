#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契参数寻优器 - 测试不同的动量窗口组合
"""

import sys
import logging
import pandas as pd

# 禁用常规日志，保持输出整洁
logging.getLogger().setLevel(logging.WARNING)

from emotion_validation.etf_rotation.backtest_rotation import run_backtest
from emotion_validation.etf_rotation.rotation_strategy import ETFRotationStrategy

original_init = ETFRotationStrategy.__init__

# 要测试的 (快线, 慢线) 组合
fibonacci_params = [
    (5, 10),   # 极速（非斐波那契，作为对比）
    (8, 13),   # 黄金短波段
    (10, 20),  # 人类习惯（当前最优）
    (13, 21),  # 经典斐波那契月度
    (21, 34),  # 中线趋势
    (34, 55)   # 长线趋势
]

results = []

print("开始进行动量窗口参数寻优 (基于月度轮动)...\n")

for fast, slow in fibonacci_params:
    print(f"正在回测: 快线={fast}天, 慢线={slow}天 ...", end="", flush=True)
    
    # 动态修改类的初始化默认参数 (Monkey Patch)
    def custom_init(self, top_n=2, fast_window=fast, slow_window=slow, fast_weight=0.5):
        original_init(self, top_n=top_n, fast_window=fast_window, slow_window=slow_window, fast_weight=fast_weight)
        
    ETFRotationStrategy.__init__ = custom_init
    
    try:
        # 运行回测
        res = run_backtest()
        
        # 提取策略 D (S5+ETF轮动) 的指标
        d_metrics = next(m for m in res['metrics'] if 'D.S5' in m['label'])
        
        results.append({
            'fast': fast,
            'slow': slow,
            'annual_ret': d_metrics['annual_ret'],
            'total_ret': d_metrics['total_ret'],
            'max_dd': d_metrics['max_dd'],
            'sharpe': d_metrics['sharpe'],
            'calmar': d_metrics['calmar'],
            'n_trades': d_metrics['n_trades']
        })
        print(f" 完成! (年化: {d_metrics['annual_ret']}%)")
    except Exception as e:
        print(f" 失败! ({e})")

# 打印最终结果表
print("\n" + "="*70)
print("  斐波那契 / 自然周期 动量参数寻优结果 (策略D: S5+ETF轮动)")
print("="*70)
print(f"  {'快线':>4} | {'慢线':>4} | {'年化收益':>8} | {'最大回撤':>8} | {'Sharpe':>7} | {'换仓数':>5}")
print("-" * 70)

# 找出最优 Sharpe
best_sharpe = max(r['sharpe'] for r in results)

for r in results:
    marker = "  <-- 最优" if r['sharpe'] == best_sharpe else ""
    print(f"  {r['fast']:>4} | {r['slow']:>4} | {r['annual_ret']:>7.1f}% | {r['max_dd']:>7.1f}% | {r['sharpe']:>7.3f} | {r['n_trades']:>5}{marker}")
print("="*70)
