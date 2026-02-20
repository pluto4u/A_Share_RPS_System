#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日开盘信号 - 实盘仓位参考

基于回测验证最优策略 S5 (LinearThrottle):
  情绪分 → 目标仓位 (0/25/50/75/100%)
  防抖规则: ① 距上次调仓 ≥5天 ② 仓位变化 ≥15% — 同时满足才执行

为什么用 S5 而不是 S2 (之前的极恐清仓策略):
  回测结果 (2024-01 ~ 2026-02, 沪深300):
    S2 EmergencyExit: 年化 13.2%, 最大回撤 -15.7%, Sharpe 0.734
    S5 LinearThrottle: 年化 13.9%, 最大回撤  -9.2%, Sharpe 1.340  ← 全面胜出
  情绪因子的正确用法是"连续调仓"，不是"开关式进出"

Usage:
    python daily_signal.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd

# Windows 终端强制 UTF-8 输出，避免 GBK 乱码
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(level=logging.WARNING)

SCORE_CSV = Path('backtest/reports/emotion_validation/emotion_scores.csv')
LOG_CSV   = Path('backtest/reports/daily_signals.csv')

# S5 参数 (与 final_comparison.py 保持一致)
MIN_DAYS_BETWEEN = 5     # 调仓冷却期: 至少间隔 5 天
MIN_DELTA        = 0.15  # 仓位变化门槛: 差距 < 15% 不动


# ======================================================================
# 核心逻辑
# ======================================================================

def score_to_target(score: float) -> float:
    """
    情绪分 → S5 目标仓位 (量化到 0/25/50/75/100%)

    分档逻辑 (round-to-nearest):
      score <  12.5  →  0%
      12.5 ~ 37.5    → 25%
      37.5 ~ 62.5    → 50%
      62.5 ~ 87.5    → 75%
      >= 87.5        → 100%
    """
    raw = score / 100.0
    return round(raw * 4) / 4


def score_to_zone(score: float) -> tuple:
    """情绪分 → (英文代码, 中文)"""
    if score >= 87.5:
        return 'OVERHEAT', '过热'
    elif score >= 62.5:
        return 'HOT', '偏热'
    elif score >= 37.5:
        return 'NORMAL', '正常'
    elif score >= 12.5:
        return 'COLD', '偏冷'
    else:
        return 'FREEZE', '极冷'


def reconstruct_s5_state(scores: pd.DataFrame) -> dict:
    """
    从完整历史评分序列重建 S5 当前仓位状态

    必须从头跑一遍，因为当前仓位取决于过去每一次调仓决策。

    Returns:
        current_pos       : 当前持仓比例 (S5 认为现在应该持有的)
        days_since_change : 距上次实际调仓的天数
        days_until_next   : 还需等待多少天才能再次调仓 (0 = 现在就能调)
        last_change_date  : 上次调仓日期
        last_change_from  : 上次从多少仓位
        last_change_to    : 上次调到多少仓位
    """
    cur_pos = 0.5      # 起始仓位 50%
    days_since = 999   # 初始时不设冷却期约束
    last_change_date = None
    last_from = 0.5
    last_to   = 0.5

    for _, row in scores.iterrows():
        target = score_to_target(row['emotion_score'])
        days_since += 1

        if days_since >= MIN_DAYS_BETWEEN:
            delta = abs(target - cur_pos)
            if delta >= MIN_DELTA:
                last_from        = cur_pos
                last_to          = target
                last_change_date = row['trade_date'].strftime('%Y-%m-%d')
                cur_pos          = target
                days_since       = 0

    return {
        'current_pos'      : cur_pos,
        'days_since_change': days_since,
        'days_until_next'  : max(0, MIN_DAYS_BETWEEN - days_since),
        'last_change_date' : last_change_date,
        'last_change_from' : last_from,
        'last_change_to'   : last_to,
    }


def check_emergency_status(today_eco: pd.Series, history_eco: pd.DataFrame) -> tuple:
    """
    检查紧急情况，拦截常规的 S5 指令。
    返回: (紧急动作名, 中文说明, 强制目标仓位) 若非紧急则返回 (None, None, None)
    """
    if history_eco.empty or len(history_eco) < 20:
        return None, None, None
        
    total_stocks = today_eco['total_stocks'] if 'total_stocks' in today_eco else 5300
    
    # 1. 动态跌停占比熔断 (一票否决清仓)
    limit_down_ratio = today_eco['limit_down_count'] / total_stocks
    if limit_down_ratio > 0.03:  # 跌停超过3% (约150家)
        return "EMERGENCY_SELL", "系统性恐慌！单日跌停超3%，触发防守熔断", 0.0
        
    # 2. 大面反噬预警 (涨停家数比跌停+吃大面家数还少)
    # 这说明打板生态极度恶劣，盈亏比极差
    bad_effect = today_eco['limit_down_count'] + today_eco['big_loss_count']
    if bad_effect > today_eco['limit_up_count'] * 1.5 and bad_effect > 50:
        return "ECO_CRASH", "打板生态崩溃！大面数量远超涨停，亏钱效应极速扩散", 0.0

    # 3. 恐慌蔓延速度预警 (环比激增)
    yesterday_limit_down = history_eco.iloc[-2]['limit_down_count']
    if today_eco['limit_down_count'] > 50 and (today_eco['limit_down_count'] > yesterday_limit_down * 3):
        if today_eco.get('up_count', 0) < 1000:
            return "PANIC_SPREAD", "恐慌蔓延！跌停家数单日翻三倍以上，防守撤退", 0.25
            
    # 4. 动态地量冰点 (缩量观望区，但如果已经是右侧则不管)
    # 若昨天已经是极度缩量
    if 'amount' in history_eco.columns:
        vol_20d_mean = history_eco['amount'].tail(20).mean()
        if today_eco.get('amount', 0) < vol_20d_mean * 0.6:
            return "LIQUIDITY_FREEZE", "流动性冰封！全市场极度缩量，强制轻仓观望", 0.25

    return None, None, None

def get_signal(scores: pd.DataFrame, ecology: pd.DataFrame = None) -> dict:
    """计算今日完整信号"""
    state  = reconstruct_s5_state(scores)
    latest = scores.iloc[-1]
    score  = latest['emotion_score']

    target_pos          = score_to_target(score)
    zone_code, zone_cn  = score_to_zone(score)
    current_pos         = state['current_pos']
    days_since          = state['days_since_change']
    days_until          = state['days_until_next']
    
    # Check Reversal Regimes first (Overrides S5 Target Pos)
    regime = latest.get('regime_reversal', latest.get('regime', ''))
    if regime == 'flash_crash_reversal':
        target_pos = 1.0
        zone_cn = '闪崩反转'
    elif regime == 'exhaustion_reversal':
        target_pos = 0.8
        zone_cn = '衰竭反转'

    n         = len(scores)
    chg_1d    = score - scores['emotion_score'].iloc[-2] if n >= 2 else 0.0
    chg_3d    = score - scores['emotion_score'].iloc[-4] if n >= 4 else 0.0

    # 1. 检查紧急状态 (优先级最高，无视任何防抖)
    emergency_action = None
    if ecology is not None and not ecology.empty:
        today_eco = ecology.iloc[-1]
        hist_eco = ecology.iloc[:-1] if len(ecology) > 1 else ecology
        e_action, e_reason, e_pos = check_emergency_status(today_eco, hist_eco)
        if e_action:
            target_pos = e_pos
            emergency_action = e_action
            action = 'EMERGENCY'
            action_cn = f'!!! 紧急干预: {e_reason} -> 强制调仓至 {int(target_pos*100)}%'
            days_until = 0 # 破除防抖
            can_adjust = True
            needs_adjust = abs(target_pos - current_pos) >= MIN_DELTA

    if not emergency_action:
        # 常规 S5 逻辑 或 特殊反转逻辑
        # 若触发特殊反转 (Flash crash / Exhaustion)，也破除 5 天冷却期防抖限制，立刻上车！
        if regime in ['flash_crash_reversal', 'exhaustion_reversal']:
            days_until = 0
            can_adjust = True
            
        can_adjust  = days_since >= MIN_DAYS_BETWEEN or days_until == 0
        needs_adjust = abs(target_pos - current_pos) >= MIN_DELTA

        if can_adjust and needs_adjust:
            action    = 'ADJUST'
            action_cn = (f'>>> 今日建议调仓: {int(current_pos*100)}% → {int(target_pos*100)}%  '
                         f'(仓位{"增加" if target_pos > current_pos else "降低"}'
                         f' {abs(int((target_pos-current_pos)*100))}%)')
        elif not can_adjust and needs_adjust:
            action    = 'PENDING'
            action_cn = (f'    待调仓: 目标 {int(target_pos*100)}%，'
                         f'还需等 {days_until} 天冷却期 (防止频繁换仓)')
        elif zone_code == 'OVERHEAT' or regime == 'flash_crash_reversal':
            action    = 'CAUTION'
            action_cn = f'    高弹性区间，正常持仓 {int(current_pos*100)}%，注意随时兑现'
        else:
            action    = 'HOLD'
            action_cn = f'    无需操作，持仓维持 {int(current_pos*100)}%'

    return {
        'date'             : latest['trade_date'].strftime('%Y-%m-%d'),
        'score'            : round(score, 1),
        'zone_code'        : zone_code,
        'zone_cn'          : zone_cn,
        'chg_1d'           : round(chg_1d, 1),
        'chg_3d'           : round(chg_3d, 1),
        'current_pos'      : current_pos,
        'target_pos'       : target_pos,
        'days_since_change': days_since,
        'days_until_next'  : days_until,
        'action'           : action,
        'action_cn'        : action_cn,
        'last_change_date' : state['last_change_date'],
        'last_change_from' : state['last_change_from'],
        'last_change_to'   : state['last_change_to'],
    }


# ======================================================================
# 输出
# ======================================================================

def print_signal(sig: dict, scores: pd.DataFrame):
    trend = '↑' if sig['chg_1d'] > 0 else ('↓' if sig['chg_1d'] < 0 else '→')

    print()
    print("=" * 62)
    print(f"  A股市场情绪仪表盘  |  {sig['date']}")
    print("=" * 62)
    print()
    print(f"  情绪得分: {sig['score']:5.1f} / 100  "
          f"[{sig['zone_cn']} / {sig['zone_code']}]  {trend}")
    print(f"  1日变化: {sig['chg_1d']:+.1f}pts   3日变化: {sig['chg_3d']:+.1f}pts")

    bar_width = 42
    filled = int(sig['score'] / 100 * bar_width)
    bar = '#' * filled + '.' * (bar_width - filled)
    print(f"  [{bar}]")
    print(f"   0   极冷  偏冷   正常    偏热  过热  100")
    print()

    # 仓位面板
    cur_pct  = int(sig['current_pos'] * 100)
    tgt_pct  = int(sig['target_pos']  * 100)
    pos_bar  = ('#' * (cur_pct // 10)).ljust(10, '.')

    print("  +------------------------------------------------+")
    print(f"  |  当前持仓  [{pos_bar}]  {cur_pct:>3}%                     |")
    print(f"  |  目标仓位  {tgt_pct:>3}%  (情绪分 -> 25% 步进)          |")
    print(f"  |  距上次调仓  {sig['days_since_change']:>3} 天  (上次: "
          f"{sig['last_change_date'] or '无'}  "
          f"{int(sig['last_change_from']*100)}%->{int(sig['last_change_to']*100)}%)  |")
    if sig['days_until_next'] > 0:
        print(f"  |  冷却期剩余  {sig['days_until_next']:>3} 天"
              f"                                       |")
    print("  +------------------------------------------------+")
    print()
    print(f"  {sig['action_cn']}")
    print()

    # 近10天历史
    print("  近 10 日:")
    print(f"  {'日期':>6}  {'分数':>5}  {'档位':>5}  {'目标仓':>5}  迷你图")
    print(f"  {'------':>6}  {'-----':>5}  {'-----':>5}  {'------':>5}")
    recent = scores.tail(10)
    for _, row in recent.iterrows():
        d = row['trade_date'].strftime('%m-%d')
        s = row['emotion_score']
        z = score_to_zone(s)[1]
        t = int(score_to_target(s) * 100)
        b = '#' * int(s / 10) + '.' * (10 - int(s / 10))
        marker = ' ← 今日' if d == sig['date'][-5:] else ''
        print(f"  {d:>6}  {s:>5.1f}  {z:>4}  {t:>4}%  {b}{marker}")

    print()
    print("─" * 62)
    print("  规则 (S5 LinearThrottle, 回测 Sharpe 1.34 / 最大回撤 -9.2%):")
    print("  ① 情绪分每 25 点 → 25% 仓位  (0/25/50/75/100%)")
    print("  ② 调仓冷却期 ≥5 天 且 仓位差 ≥15% 才执行")
    print("  ③ 配合选股: 总仓位 ÷ N支股票 = 每支持仓比例")
    print("=" * 62)


def load_scores() -> pd.DataFrame:
    if not SCORE_CSV.exists():
        print("ERROR: 找不到情绪评分文件，请先运行:")
        print("  python -m emotion_validation.pipeline")
        sys.exit(1)
    df = pd.read_csv(SCORE_CSV)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df.sort_values('trade_date').reset_index(drop=True)


def log_signal(sig: dict):
    """追加写入历史信号日志"""
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{
        'date'             : sig['date'],
        'score'            : sig['score'],
        'zone'             : sig['zone_code'],
        'action'           : sig['action'],
        'current_pos_pct'  : int(sig['current_pos'] * 100),
        'target_pos_pct'   : int(sig['target_pos']  * 100),
        'chg_1d'           : sig['chg_1d'],
        'chg_3d'           : sig['chg_3d'],
        'days_since_change': sig['days_since_change'],
        'logged_at'        : datetime.now().strftime('%Y-%m-%d %H:%M'),
    }])

    if LOG_CSV.exists():
        existing = pd.read_csv(LOG_CSV)
        existing = existing[existing['date'] != sig['date']]
        updated  = pd.concat([existing, row], ignore_index=True)
    else:
        updated = row

    updated.to_csv(LOG_CSV, index=False, encoding='utf-8-sig')


def main():
    scores = load_scores()
    
    # Try to load ecology data for emergency checks
    try:
        ecology = pd.read_csv('backtest/data/ecology_cache.csv', parse_dates=['trade_date'])
        ecology = ecology.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f"Warning: Could not load ecology data for emergency checks: {e}")
        ecology = None
        
    sig    = get_signal(scores, ecology)
    print_signal(sig, scores)
    log_signal(sig)


if __name__ == '__main__':
    main()
