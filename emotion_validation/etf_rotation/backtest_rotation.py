#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 轮动策略回测 — 4 策略并行对比

策略:
  A. ETF轮动       — 月度动量换仓，情绪过滤候选池，仓位恒 100%
  B. 510300买持    — 买入沪深300ETF，从不动，仓位恒 100%（基准）
  C. S5+510300     — 沪深300ETF × S5情绪仓位 (0/25/50/75/100%)
  D. S5+ETF轮动    — ETF月度轮动 × S5情绪仓位（最完整策略）

换仓规则（A & D）:
  - 每月最后交易日收盘后计算，下月第一日开盘执行（无未来函数）
  - 新持仓 = 旧持仓 → 不换仓（节省成本）
  - 费率: 单边万一

S5 规则（C & D）:
  - 情绪分每 25 点 → 25% 仓位
  - 调仓冷却期 ≥5 天 且 仓位差 ≥15% 才执行
  - 与 daily_signal.py 完全一致

CLI:
    python -m emotion_validation.etf_rotation.backtest_rotation
    python -m emotion_validation.etf_rotation.backtest_rotation --start 2024-01-01
"""

import sys
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logger = logging.getLogger(__name__)

REPORT_DIR = Path(__file__).resolve().parent.parent.parent / 'backtest' / 'reports' / 'etf_rotation'
COST       = 0.0001   # 单边万一 (ETF 场内)
INIT       = 1_000_000

# S5 参数（与 daily_signal.py 保持一致）
S5_MIN_DAYS  = 5
S5_MIN_DELTA = 0.15


# ======================================================================
# S5 仓位重建
# ======================================================================

def _score_to_target(score: float) -> float:
    """情绪分 → S5 目标仓位 (0/0.25/0.5/0.75/1.0)"""
    return round((score / 100.0) * 4) / 4


def build_s5_positions(emotion_df: pd.DataFrame,
                       all_dates: pd.Series) -> dict:
    """
    从情绪评分历史重建每个交易日的 S5 仓位

    必须从全量历史起头跑，确保调仓状态正确。
    Returns: {pd.Timestamp → float}
    """
    cur_pos    = 0.5
    days_since = 999
    pos_map    = {}

    for _, row in emotion_df.iterrows():
        # Check Reversal Regimes first (Overrides S5 Target Pos)
        regime = row.get('regime_reversal', row.get('regime', ''))
        
        target = _score_to_target(row['emotion_score'])
        
        if regime == 'flash_crash_reversal':
            target = 1.0
        elif regime == 'exhaustion_reversal':
            target = 0.8
        elif regime == 'low_falling':
            target = 0.0 # 绝对空仓

        days_since += 1
        
        # 若触发特殊反转 (Flash crash / Exhaustion)，或者彻底溃败，也破除 5 天冷却期防抖限制
        if regime in ['flash_crash_reversal', 'exhaustion_reversal', 'low_falling']:
            days_since = 999

        if days_since >= S5_MIN_DAYS:
            if abs(target - cur_pos) >= S5_MIN_DELTA:
                cur_pos    = target
                days_since = 0
        pos_map[row['trade_date']] = cur_pos

    # 映射到回测日期，向前填充（情绪数据可能缺少某些交易日）
    result    = {}
    last_pos  = 0.5
    for d in all_dates:
        if d in pos_map:
            last_pos = pos_map[d]
        result[d] = last_pos
    return result


# ======================================================================
# 通用工具
# ======================================================================

def load_emotion_scores() -> pd.DataFrame:
    score_csv = (Path(__file__).resolve().parent.parent.parent
                 / 'backtest' / 'reports' / 'emotion_validation' / 'emotion_scores.csv')
    if not score_csv.exists():
        raise FileNotFoundError(
            f"情绪评分文件不存在: {score_csv}\n请先运行: python -m emotion_validation.pipeline"
        )
    df = pd.read_csv(score_csv)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df.sort_values('trade_date').reset_index(drop=True)


def get_month_end_dates(all_dates: pd.Series) -> set:
    """每月最后一个交易日的集合"""
    return set(all_dates.groupby(all_dates.dt.to_period('M')).last())


def etf_daily_return(etf_data: dict, holdings: list, date) -> float:
    """按权重持仓的当日收益率"""
    total = 0.0
    for item in holdings:
        sym = item['symbol'] if isinstance(item, dict) else item
        w = item['weight'] if isinstance(item, dict) else (1.0 / len(holdings))
        if sym not in etf_data:
            continue
        row = etf_data[sym]
        row = row[row['trade_date'] == date]
        if row.empty:
            continue
        pct = row['pct_chg'].iloc[0]
        if pd.isna(pct):
            continue
        total += (pct / 100.0) * w
    return total


def calc_metrics(nav_series: pd.Series, label: str, n_trades: int = 0) -> dict:
    n      = len(nav_series)
    years  = n / 250
    ret    = nav_series.iloc[-1] / INIT - 1
    annual = (1 + ret) ** (1 / years) - 1 if years > 0 else 0

    dr     = nav_series.pct_change().dropna()
    vol    = dr.std() * np.sqrt(250)
    sharpe = annual / vol if vol > 0 else 0

    peak   = nav_series.cummax()
    dd     = (nav_series - peak) / peak
    max_dd = dd.min()
    calmar = annual / abs(max_dd) if abs(max_dd) > 0 else 0

    return {
        'label'     : label,
        'annual_ret': round(annual * 100, 1),
        'total_ret' : round(ret    * 100, 1),
        'max_dd'    : round(max_dd * 100, 1),
        'sharpe'    : round(sharpe, 3),
        'calmar'    : round(calmar, 3),
        'vol'       : round(vol    * 100, 1),
        'win_rate'  : round((dr > 0).sum() / len(dr) * 100, 1),
        'n_trades'  : n_trades,
    }


# ======================================================================
# 回测主逻辑
# ======================================================================

def run_backtest(start_date: str = '2024-01-01',
                 end_date:   str = None,
                 top_n:      int = 2) -> dict:

    from .etf_data import ETFDataManager
    from .rotation_strategy import ETFRotationStrategy

    # ---- 加载数据 ----
    logger.info("加载 ETF 数据...")
    etf_data = ETFDataManager().load_all()
    if not etf_data:
        raise RuntimeError("无 ETF 数据，请先运行: python -m emotion_validation.etf_rotation.etf_data")
    logger.info(f"已加载 {len(etf_data)} 只 ETF")

    emotion_df = load_emotion_scores()
    logger.info(f"情绪评分: {len(emotion_df)} 天")

    # ---- 日期范围 ----
    start    = pd.Timestamp(start_date)
    end      = pd.Timestamp(end_date) if end_date else emotion_df['trade_date'].max()
    base_etf = '510300' if '510300' in etf_data else list(etf_data.keys())[0]

    all_dates = (etf_data[base_etf]['trade_date']
                 .pipe(lambda s: s[(s >= start) & (s <= end)])
                 .reset_index(drop=True))

    if all_dates.empty:
        raise ValueError(f"日期范围 {start_date}~{end_date} 内无 ETF 数据")

    # ---- 预计算 ----
    month_ends   = get_month_end_dates(all_dates)
    rot_strategy = ETFRotationStrategy(top_n=top_n)
    s5_positions = build_s5_positions(emotion_df, all_dates)
    emotion_map  = dict(zip(emotion_df['trade_date'], emotion_df['emotion_score']))

    # ---- 4 策略并行 ----
    # A: ETF轮动（无S5），B: 510300买持，C: S5+510300，D: S5+ETF轮动
    nav_A = nav_B = nav_C = nav_D = float(INIT)

    rot_holdings    = [{'symbol': base_etf, 'weight': 1.0, 'is_defensive': False}]   # A & D 当前持仓
    pending_hold    = None         # T-1 决策
    prev_s5_pos     = 0.5          # 上一期 S5 仓位（用于计算换仓成本）

    nav_A_list = []
    nav_B_list = []
    nav_C_list = []
    nav_D_list = []
    trade_log  = []

    for date in all_dates:
        s5_pos = s5_positions[date]

        # 1. 执行昨日 ETF 换仓决策
        if pending_hold is not None:
            old_symbols = {h['symbol'] if isinstance(h, dict) else h for h in rot_holdings}
            new_symbols = {h['symbol'] if isinstance(h, dict) else h for h in pending_hold}
            if old_symbols != new_symbols:
                n_ch    = len(old_symbols.symmetric_difference(new_symbols))
                friction = COST * n_ch
                nav_A  *= (1 - friction)
                nav_D  *= (1 - friction)
                trade_log.append({
                    'date'   : date.strftime('%Y-%m-%d'),
                    'from'   : ','.join(sorted(old_symbols)),
                    'to'     : ','.join(sorted(new_symbols)),
                    'changes': n_ch,
                })
            rot_holdings = pending_hold
            pending_hold = None

        # 2. S5 仓位变动摩擦（C & D）
        s5_delta = abs(s5_pos - prev_s5_pos)
        if s5_delta >= S5_MIN_DELTA:
            s5_friction = COST * s5_delta * 2   # 双向 = 买入/卖出各一次
            nav_C *= (1 - s5_friction)
            nav_D *= (1 - s5_friction)
        prev_s5_pos = s5_pos

        # 3. 当日收益
        rot_ret = etf_daily_return(etf_data, rot_holdings, date)
        bh_ret  = etf_daily_return(etf_data, [{'symbol': base_etf, 'weight': 1.0}], date)

        nav_A *= (1 + rot_ret)
        nav_B *= (1 + bh_ret)
        nav_C *= (1 + bh_ret  * s5_pos)   # S5 仓位 × 市场收益
        nav_D *= (1 + rot_ret * s5_pos)
        
        rot_symbols_str = ','.join(sorted({h['symbol'] if isinstance(h, dict) else h for h in rot_holdings}))

        nav_A_list.append({'trade_date': date, 'nav': nav_A, 'pos': 1.0,    'holdings': rot_symbols_str})
        nav_B_list.append({'trade_date': date, 'nav': nav_B, 'pos': 1.0,    'holdings': base_etf})
        nav_C_list.append({'trade_date': date, 'nav': nav_C, 'pos': s5_pos, 'holdings': base_etf})
        nav_D_list.append({'trade_date': date, 'nav': nav_D, 'pos': s5_pos, 'holdings': rot_symbols_str})

        # 4. 月末决策（次日执行）
        if date in month_ends:
            score    = emotion_map.get(date, 50.0)
            new_h    = rot_strategy.get_holdings(etf_data, score, date)
            pending_hold = new_h if new_h else [{'symbol': base_etf, 'weight': 1.0, 'is_defensive': False}]
            pending_symbols = [h['symbol'] for h in pending_hold]
            logger.debug(f"  {date.date()} 月末决策: {pending_symbols} (情绪={score:.1f}, S5={s5_pos:.0%})")

    # ---- 构造 DataFrame ----
    df_A = pd.DataFrame(nav_A_list)
    df_B = pd.DataFrame(nav_B_list)
    df_C = pd.DataFrame(nav_C_list)
    df_D = pd.DataFrame(nav_D_list)
    trade_df = pd.DataFrame(trade_log)

    metrics = [
        calc_metrics(df_D['nav'], 'D.S5+ETF轮动',  len(trade_df)),
        calc_metrics(df_C['nav'], 'C.S5+510300',    0),
        calc_metrics(df_A['nav'], 'A.ETF轮动',      len(trade_df)),
        calc_metrics(df_B['nav'], 'B.510300买持',   0),
    ]

    return {
        'nav_A'   : df_A,
        'nav_B'   : df_B,
        'nav_C'   : df_C,
        'nav_D'   : df_D,
        'trade_df': trade_df,
        'metrics' : metrics,
    }


# ======================================================================
# 报告输出
# ======================================================================

def save_and_print(result: dict):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    metrics  = result['metrics']
    trade_df = result['trade_df']

    # 合并每日净值
    nav_all = (result['nav_D'][['trade_date', 'nav']].rename(columns={'nav': 'D_S5_rotation'})
               .merge(result['nav_C'][['trade_date', 'nav']].rename(columns={'nav': 'C_S5_510300'}), on='trade_date')
               .merge(result['nav_A'][['trade_date', 'nav']].rename(columns={'nav': 'A_rotation'}),  on='trade_date')
               .merge(result['nav_B'][['trade_date', 'nav']].rename(columns={'nav': 'B_510300_BH'}),  on='trade_date'))
    nav_all.to_csv(REPORT_DIR / 'rotation_nav_all.csv', index=False, encoding='utf-8-sig')

    if not trade_df.empty:
        trade_df.to_csv(REPORT_DIR / 'rotation_trades.csv', index=False, encoding='utf-8-sig')

    date_range = f"{result['nav_B']['trade_date'].min().date()} ~ {result['nav_B']['trade_date'].max().date()}"

    lines = []
    lines.append("=" * 78)
    lines.append(f"  ETF 轮动 × 情绪择时 — 四策略对比回测")
    lines.append(f"  日期范围: {date_range}")
    lines.append("=" * 78)
    lines.append(f"  {'策略':<20} {'年化':>7} {'总收益':>8} {'最大DD':>7} "
                 f"{'Sharpe':>7} {'Calmar':>7} {'胜率':>6} {'换仓':>5}")
    lines.append(f"  {'-'*20} {'-'*7} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*5}")

    best_sharpe = max(m['sharpe'] for m in metrics)
    for m in metrics:
        marker = '  <-- 最优Sharpe' if m['sharpe'] == best_sharpe else ''
        lines.append(
            f"  {m['label']:<20} {m['annual_ret']:>6.1f}% {m['total_ret']:>7.1f}% "
            f"{m['max_dd']:>6.1f}% {m['sharpe']:>7.3f} {m['calmar']:>7.3f} "
            f"{m['win_rate']:>5.1f}% {m['n_trades']:>5}{marker}"
        )

    lines.append("")
    lines.append(f"  换仓记录 ({len(trade_df)} 次):")
    if trade_df.empty:
        lines.append("    (无换仓)")
    else:
        for _, row in trade_df.iterrows():
            lines.append(f"    {row['date']}  [{row['from']}] -> [{row['to']}]")

    lines.append("=" * 78)
    lines.append("  策略说明:")
    lines.append("    D = ETF轮动选标的 + S5情绪择时调仓位（最完整系统）")
    lines.append("    C = 只持沪深300ETF，用S5情绪控制仓位（最简单实用）")
    lines.append("    A = ETF月度轮动，不控仓位（纯选标的效果）")
    lines.append("    B = 买入沪深300ETF不动（基准）")
    lines.append("=" * 78)

    summary = "\n".join(lines)
    print("\n" + summary)
    (REPORT_DIR / 'rotation_summary.txt').write_text(summary, encoding='utf-8')
    logger.info(f"报告已保存: {REPORT_DIR}")


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    parser = argparse.ArgumentParser(description='ETF 轮动×情绪择时 — 四策略回测')
    parser.add_argument('--start', default='2024-01-01', help='开始日期')
    parser.add_argument('--end',   default=None,         help='结束日期')
    parser.add_argument('--top-n', type=int, default=2,  help='持有 ETF 数量 (默认 2)')
    args = parser.parse_args()

    result = run_backtest(args.start, args.end, args.top_n)
    save_and_print(result)


if __name__ == '__main__':
    main()
