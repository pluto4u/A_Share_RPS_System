#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
今日操作指令 (一键出单)
================================================================================
每日 14:45 运行，输出完整的操作建议：
  1. 今日情绪分 + 仓位建议
  2. 本周 ETF 轮动持仓 (买什么)
  3. 具体操作指令 (加仓/减仓/换仓/不动)

用法:
    python today_action.py              # 标准模式
    python today_action.py --update     # 先更新数据再出信号
================================================================================
"""
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd

logging.basicConfig(level=logging.WARNING)

from daily_signal import (
    load_scores, score_to_target, score_to_zone,
    get_signal, MIN_DAYS_BETWEEN, MIN_DELTA
)
from emotion_validation.etf_rotation.etf_data import ETFDataManager, ETF_POOL
from emotion_validation.etf_rotation.rotation_strategy import ETFRotationStrategy

ETF_NAME = {code: name for code, name, _ in ETF_POOL}
ETF_SECTOR = {code: sector for code, _, sector in ETF_POOL}


def get_etf_holdings(emotion_score: float, as_of_date=None):
    """获取当前 ETF 轮动持仓建议"""
    mgr = ETFDataManager()
    etf_data = mgr.load_all()
    if not etf_data:
        return None, "无 ETF 数据，请先运行: python update_etfs.py"

    strategy = ETFRotationStrategy(top_n=2)
    holdings = strategy.get_holdings(etf_data, emotion_score, as_of_date)
    return holdings, None


def print_action(sig: dict, holdings: list, scores: pd.DataFrame):
    """输出完整操作指令"""
    today = sig['date']
    score = sig['score']
    zone_cn = sig['zone_cn']
    cur_pos = int(sig['current_pos'] * 100)
    tgt_pos = int(sig['target_pos'] * 100)
    action = sig['action']

    trend = '↑' if sig['chg_1d'] > 0 else ('↓' if sig['chg_1d'] < 0 else '→')

    print()
    print("=" * 66)
    print(f"  今日操作指令  |  {today}  |  14:50 前执行")
    print("=" * 66)

    # ---- 第一部分: 情绪 & 仓位 ----
    print()
    print(f"  [情绪] {score:.1f}分 ({zone_cn}) {trend}  "
          f"日变{sig['chg_1d']:+.1f}  三日变{sig['chg_3d']:+.1f}")

    bar_w = 30
    filled = int(score / 100 * bar_w)
    bar = '█' * filled + '░' * (bar_w - filled)
    print(f"  [{bar}] {score:.0f}/100")
    print()

    # ---- 第二部分: 仓位操作 ----
    print("  ┌─────────────────────────────────────────────────────┐")
    if action == 'ADJUST':
        direction = "加仓" if tgt_pos > cur_pos else "减仓"
        delta = abs(tgt_pos - cur_pos)
        print(f"  │  >>> {direction}操作: {cur_pos}% → {tgt_pos}%  (变动 {delta}%)         │")
    elif action == 'EMERGENCY':
        print(f"  │  !!! 紧急操作: {cur_pos}% → {tgt_pos}%                      │")
        print(f"  │  {sig['action_cn'][:50]}│")
    elif action == 'PENDING':
        days_left = sig['days_until_next']
        print(f"  │  等待中: 目标 {tgt_pos}%，冷却期还剩 {days_left} 天              │")
    else:
        print(f"  │  持仓不变: 维持 {cur_pos}%                                │")
    print("  └─────────────────────────────────────────────────────┘")
    print()

    # ---- 第三部分: ETF 持仓 ----
    print("  [ETF 轮动持仓]")
    if holdings:
        for h in holdings:
            sym = h['symbol']
            name = ETF_NAME.get(sym, sym)
            sector = ETF_SECTOR.get(sym, '')
            mom = h.get('momentum_score', 0)
            weight = h.get('weight', 0.5) * 100
            print(f"    {sym} {name:<12} ({sector})  "
                  f"动量={mom:+.1f}%  配比={weight:.0f}%")
    else:
        print("    (无法获取，请检查 ETF 数据)")
    print()

    # ---- 第四部分: 具体下单指令 ----
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  具体操作 (假设总资金 100 万)                        │")
    print("  │─────────────────────────────────────────────────────│")

    total_capital = 1_000_000
    target_invested = total_capital * tgt_pos / 100

    if holdings and len(holdings) > 0:
        for h in holdings:
            sym = h['symbol']
            name = ETF_NAME.get(sym, sym)
            w = h.get('weight', 1.0 / len(holdings))
            amount = target_invested * w
            print(f"  │  {name:<10}({sym}): 持有 {amount/10000:.1f} 万          │")
        cash = total_capital - target_invested
        print(f"  │  现金留存: {cash/10000:.1f} 万 ({100 - tgt_pos}%)                     │")
    print("  └─────────────────────────────────────────────────────┘")
    print()

    # ---- 第五部分: 近期走势 ----
    print("  [近 5 日情绪]")
    recent = scores.tail(5)
    for _, row in recent.iterrows():
        d = row['trade_date'].strftime('%m-%d')
        s = row['emotion_score']
        z = score_to_zone(s)[1]
        t = int(score_to_target(s) * 100)
        b = '█' * int(s / 10) + '░' * (10 - int(s / 10))
        marker = ' ← 今日' if d == today[-5:] else ''
        print(f"    {d}  {s:>5.1f} {z:<4} 仓位{t:>3}%  {b}{marker}")

    print()
    print("─" * 66)
    print("  提醒: 在 14:50 - 14:57 之间完成操作 (尾盘集合竞价前)")
    print("        信号基于收盘前数据，尾盘执行 ≈ 吃到次日隔夜收益")
    print("=" * 66)


def main():
    parser = argparse.ArgumentParser(description='今日操作指令')
    parser.add_argument('--update', action='store_true',
                        help='先更新情绪数据再出信号')
    parser.add_argument('--feishu', action='store_true',
                        help='是否将结果推送为飞书互动卡片')
    args = parser.parse_args()

    if args.update:
        print("正在更新数据...")
        import subprocess
        py = sys.executable
        subprocess.run([py, '-m', 'emotion_validation.pipeline'], check=False)
        print()

    # 1. 加载情绪分 & 计算仓位信号
    scores = load_scores()

    try:
        ecology = pd.read_csv('backtest/data/ecology_cache.csv',
                              parse_dates=['trade_date'])
        ecology = ecology.sort_values('trade_date').reset_index(drop=True)
    except Exception:
        ecology = None

    sig = get_signal(scores, ecology)

    # 2. 获取 ETF 轮动持仓
    latest_date = scores['trade_date'].iloc[-1]
    holdings, err = get_etf_holdings(sig['score'], latest_date)
    if err:
        print(f"  警告: {err}")

    # 3. 输出完整操作指令
    print_action(sig, holdings, scores)

    if args.feishu:
        from feishu_bot import push_daily_action_report
        if holdings:
            for h in holdings:
                h['name'] = ETF_NAME.get(h['symbol'], h['symbol'])
        push_daily_action_report(sig, holdings, scores)


if __name__ == '__main__':
    main()
