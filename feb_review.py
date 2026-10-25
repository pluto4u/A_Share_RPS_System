#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2月1日~2月13日 每日信号逐日复盘"""
import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
from pathlib import Path
from daily_signal import score_to_target, score_to_zone, MIN_DAYS_BETWEEN, MIN_DELTA
from project_paths import market_data_file, report_file

scores = pd.read_csv(report_file("emotion_validation", "emotion_scores.csv"), parse_dates=['trade_date'])
scores = scores.sort_values('trade_date').reset_index(drop=True)

try:
    ecology = pd.read_csv(market_data_file("ecology_cache.csv"), parse_dates=['trade_date'])
    ecology = ecology.sort_values('trade_date').reset_index(drop=True)
except:
    ecology = None

# ETF 轮动持仓
try:
    trades = pd.read_csv(report_file("etf_rotation", "rotation_trades.csv"))
except:
    trades = None

# ETF NAV
try:
    nav_df = pd.read_csv(report_file("etf_rotation", "rotation_nav_all.csv"))
except:
    nav_df = None

from emotion_validation.etf_rotation.etf_data import ETF_POOL
etf_name_map = {code: name for code, name, _ in ETF_POOL}

# 从头重建 S5 状态
cur_pos = 0.5
days_since = 999

print("=" * 90)
print("  2月份逐日复盘 (2026-02-01 ~ 2026-02-13)")
print("=" * 90)
print()
print(f"  {'日期':<12} {'情绪分':>6} {'档位':<6} {'目标仓':>6} {'实际仓':>6} {'操作':<20} {'ETF轮动持仓':<30}")
print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*20} {'-'*30}")

# 找到 2 月之前的最后一个交易日来重建状态
feb_start_idx = scores[scores['trade_date'] >= '2026-02-01'].index[0]

for i in range(len(scores)):
    row = scores.iloc[i]
    target = score_to_target(row['emotion_score'])
    days_since += 1

    action = "持仓不变"
    if days_since >= MIN_DAYS_BETWEEN:
        delta = abs(target - cur_pos)
        if delta >= MIN_DELTA:
            action = f"{int(cur_pos*100)}% -> {int(target*100)}%"
            cur_pos = target
            days_since = 0

    if i >= feb_start_idx:
        date_str = row['trade_date'].strftime('%Y-%m-%d')
        zone_cn = score_to_zone(row['emotion_score'])[1]
        
        # 查找当天 ETF 持仓
        etf_holding = ""
        if trades is not None:
            matched = trades[trades['date'] == date_str]
            if not matched.empty:
                to_symbols = matched.iloc[0]['to']
                names = [etf_name_map.get(s.strip(), s.strip()) for s in to_symbols.split(',')]
                etf_holding = f"换仓-> {', '.join(names)}"
            else:
                # 找最近一次换仓
                prev = trades[trades['date'] < date_str]
                if not prev.empty:
                    last_to = prev.iloc[-1]['to']
                    names = [etf_name_map.get(s.strip(), s.strip()) for s in last_to.split(',')]
                    etf_holding = ', '.join(names)
        
        # 查 NAV
        nav_str = ""
        if nav_df is not None:
            nav_row = nav_df[nav_df['trade_date'] == date_str]
            if not nav_row.empty:
                d_nav = nav_row.iloc[0]['D_S5_rotation']
                nav_str = f" NAV={d_nav/1e6:.3f}M"
        
        print(f"  {date_str:<12} {row['emotion_score']:>6.1f} {zone_cn:<6} {int(target*100):>5}% {int(cur_pos*100):>5}% {action:<20} {etf_holding:<30}{nav_str}")

print()
print("=" * 90)

# 计算 2 月份 D 策略收益
if nav_df is not None:
    feb_nav = nav_df[nav_df['trade_date'] >= '2026-02-01']
    if not feb_nav.empty:
        start_nav = feb_nav.iloc[0]['D_S5_rotation']
        end_nav = feb_nav.iloc[-1]['D_S5_rotation']
        ret = (end_nav / start_nav - 1) * 100
        
        b_start = feb_nav.iloc[0]['B_510300_BH']
        b_end = feb_nav.iloc[-1]['B_510300_BH']
        b_ret = (b_end / b_start - 1) * 100
        
        print(f"  2月份策略D收益: {ret:+.2f}%  (vs 沪深300买持: {b_ret:+.2f}%)")
        print(f"  策略D NAV: {start_nav/1e6:.3f}M -> {end_nav/1e6:.3f}M")
        print(f"  沪深300 NAV: {b_start/1e6:.3f}M -> {b_end/1e6:.3f}M")
        print()

# 全局绩效
if nav_df is not None:
    total_start = nav_df.iloc[0]['D_S5_rotation']
    total_end = nav_df.iloc[-1]['D_S5_rotation']
    total_ret = (total_end / total_start - 1) * 100
    print(f"  全周期策略D总收益: {total_ret:.1f}% (初始100万 -> {total_end/1e4:.0f}万)")
