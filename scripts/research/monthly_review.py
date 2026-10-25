#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐月战绩复盘"""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np

from project_paths import report_file

nav = pd.read_csv(report_file("etf_rotation", "rotation_nav_all.csv"), parse_dates=['trade_date'])

nav['month'] = nav['trade_date'].dt.to_period('M')
monthly = nav.groupby('month').agg(
    d_start=('D_S5_rotation', 'first'),
    d_end=('D_S5_rotation', 'last'),
    b_start=('B_510300_BH', 'first'),
    b_end=('B_510300_BH', 'last'),
    a_start=('A_rotation', 'first'),
    a_end=('A_rotation', 'last'),
)
monthly['D_ret'] = (monthly['d_end'] / monthly['d_start'] - 1) * 100
monthly['B_ret'] = (monthly['b_end'] / monthly['b_start'] - 1) * 100
monthly['A_ret'] = (monthly['a_end'] / monthly['a_start'] - 1) * 100
monthly['alpha'] = monthly['D_ret'] - monthly['B_ret']

print("=" * 80)
print("  逐月战绩 -- 策略D(S5+ETF轮动) vs 沪深300买持 vs 纯轮动")
print("=" * 80)

header = f"  {'月份':<10} {'策略D':>8} {'沪深300':>8} {'纯轮动':>8} {'超额Alpha':>10} {'胜负':>4}"
print(header)
print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*4}")

d_win = 0
total = 0
for idx, row in monthly.iterrows():
    win = "胜" if row['D_ret'] > row['B_ret'] else "负"
    if row['D_ret'] > row['B_ret']:
        d_win += 1
    total += 1
    print(f"  {str(idx):<10} {row['D_ret']:>+7.2f}% {row['B_ret']:>+7.2f}% {row['A_ret']:>+7.2f}% {row['alpha']:>+9.2f}%   {win}")

print(f"  {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*4}")

d_total = (nav['D_S5_rotation'].iloc[-1] / nav['D_S5_rotation'].iloc[0] - 1) * 100
b_total = (nav['B_510300_BH'].iloc[-1] / nav['B_510300_BH'].iloc[0] - 1) * 100
a_total = (nav['A_rotation'].iloc[-1] / nav['A_rotation'].iloc[0] - 1) * 100
print(f"  {'总计':<10} {d_total:>+7.1f}% {b_total:>+7.1f}% {a_total:>+7.1f}% {d_total-b_total:>+9.1f}%")
print()
print(f"  月度胜率 (vs 沪深300): {d_win}/{total} = {d_win/total*100:.0f}%")

# 最大回撤
d_nav = nav['D_S5_rotation']
peak = d_nav.cummax()
dd = (d_nav - peak) / peak
max_dd = dd.min()
max_dd_idx = dd.idxmin()
max_dd_date = nav['trade_date'].iloc[max_dd_idx]

b_nav = nav['B_510300_BH']
b_peak = b_nav.cummax()
b_dd = (b_nav - b_peak) / b_peak
b_max_dd = b_dd.min()

print()
print(f"  策略D最大回撤: {max_dd*100:.1f}% (发生在 {max_dd_date.date()})")
print(f"  沪深300最大回撤: {b_max_dd*100:.1f}%")
print()
print(f"  100万本金终值:")
print(f"    策略D (S5+ETF轮动): {nav['D_S5_rotation'].iloc[-1]/1e4:.0f} 万")
print(f"    纯ETF轮动 (无择时): {nav['A_rotation'].iloc[-1]/1e4:.0f} 万")
print(f"    沪深300买持 (基准):  {nav['B_510300_BH'].iloc[-1]/1e4:.0f} 万")

# 连胜/连败
streaks = []
cur = 0
for _, row in monthly.iterrows():
    if row['D_ret'] > row['B_ret']:
        if cur > 0:
            cur += 1
        else:
            cur = 1
    else:
        if cur < 0:
            cur -= 1
        else:
            cur = -1
    streaks.append(cur)

print()
print(f"  最长连胜月数: {max(streaks)}")
print(f"  最长连败月数: {abs(min(streaks))}")

# 年化
years = len(nav) / 250
d_annual = ((nav['D_S5_rotation'].iloc[-1] / nav['D_S5_rotation'].iloc[0]) ** (1/years) - 1) * 100
b_annual = ((nav['B_510300_BH'].iloc[-1] / nav['B_510300_BH'].iloc[0]) ** (1/years) - 1) * 100
print()
print(f"  年化收益: 策略D {d_annual:.1f}% vs 沪深300 {b_annual:.1f}% (超额 {d_annual-b_annual:.1f}%)")

# 夏普
dr = d_nav.pct_change().dropna()
sharpe = d_annual / (dr.std() * np.sqrt(250) * 100)
print(f"  夏普比率: {sharpe:.2f}")
print("=" * 80)
