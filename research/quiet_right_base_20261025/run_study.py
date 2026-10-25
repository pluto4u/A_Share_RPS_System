"""底部位置（月线）+ 安静的右侧（日线）：月末截面事件检验。规则见同目录 PREREG.md（运行前登记）。

    python research/quiet_right_base_20261025/run_study.py --date 2026-10-23

不是可交易回测：无成本、涨跌停、仓位约束；事件重叠。结果写入 results/。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rps_analyzer.monthly_reversal import ReversalConfig, add_features, forward_returns, monthly_bars  # noqa: E402
from workflows.canonical_panel import load_panel  # noqa: E402
from build_limit_ecology import load_name_master  # noqa: E402  (path set by canonical_panel)

OUT = Path(__file__).resolve().parent / 'results'
PERIODS = [('design_2018-2021', '2018-01', '2021-12'),
           ('stress_2022-2024.01', '2022-01', '2024-01'),
           ('recent_2024.02-', '2024-02', '2099-12')]
GROUPS = ['H1', 'C1', 'C2', 'C3', 'C4', 'X0', 'X1', 'POOL']
LABELS = {'H1': '底部+右侧+安静（主假设）', 'C1': '底部+右侧+热', 'C2': '右侧+安静（无月线位置）',
          'C3': '只看月线位置', 'C4': '只看日线右侧', 'X0': '合格池剔除热股', 'X1': '热股', 'POOL': '合格池'}
MIN_GROUP = 5


def nw_t(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return float('nan')
    e = x - x.mean()
    s = e @ e / n
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(s / n)) if s > 0 else float('nan')


def daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Month-end daily features, keyed by (month, ts_code). Row for M uses data up to M's last trading day."""
    ok = daily.close.notna() & (daily.vol > 0) & (daily.amount > 0)
    d = daily.loc[ok, ['trade_date', 'ts_code', 'close', 'amount']]
    c = d.pivot(index='trade_date', columns='ts_code', values='close').sort_index()
    a = d.pivot(index='trade_date', columns='ts_code', values='amount').reindex(c.index)
    ma20 = c.rolling(20, min_periods=16).mean()
    ma60 = c.rolling(60, min_periods=48).mean()
    feats = {
        'close_d': c, 'ma20': ma20, 'ma60': ma60,
        'ma60_slope': ma60 / ma60.shift(20) - 1,
        'ret20': c / c.shift(20) - 1,
        'vol20': c.pct_change(fill_method=None).rolling(20, min_periods=15).std(),
        'amt20': a.rolling(20, min_periods=15).mean(),
        'amt_ratio': a.rolling(20, min_periods=15).mean() / a.rolling(120, min_periods=90).mean(),
    }
    ends = c.index.to_series().groupby(c.index.to_period('M')).max()
    out = pd.concat({k: v.loc[ends.values].set_axis(ends.index).stack(future_stack=True) for k, v in feats.items()}, axis=1)
    out.index.names = ['month', 'ts_code']
    return out.reset_index()


def monthly_position(monthly: pd.DataFrame) -> pd.DataFrame:
    months = pd.period_range(monthly.month.min(), monthly.month.max(), freq='M')
    mc = monthly.pivot(index='month', columns='ts_code', values='close').reindex(months)
    low12 = mc.rolling(12, min_periods=10).min()
    high24 = mc.rolling(24, min_periods=20).max()
    ma10 = mc.rolling(10, min_periods=10).mean()
    f = {'dd_base': low12 / high24 - 1, 'off_base': mc / low12 - 1, 'ma10_up': (ma10 > ma10.shift(1)).astype(float).where(ma10.notna() & ma10.shift(1).notna())}
    out = pd.concat({k: v.stack(future_stack=True) for k, v in f.items()}, axis=1)
    out.index.names = ['month', 'ts_code']
    return out.reset_index()


def build(daily: pd.DataFrame, target: pd.Timestamp, names: dict) -> pd.DataFrame:
    cfg = ReversalConfig(horizons=(1, 3))
    monthly = monthly_bars(daily, target)
    base = add_features(monthly, cfg)[['month', 'ts_code', 'full_close', 'n_days', 'history', 'ret_m']]
    f = (base.merge(monthly_position(monthly), on=['month', 'ts_code'], how='left')
         .merge(daily_features(daily), on=['month', 'ts_code'], how='left')
         .merge(forward_returns(monthly, cfg), on=['month', 'ts_code'], how='left'))
    name = f.ts_code.map(names).fillna('')
    f['eligible'] = (f.full_close & (f.n_days >= 10) & (f.history >= 24) & (f.amt20 >= 5e7)
                     & ~name.str.contains('ST|退', regex=True))
    e = f.eligible
    rank = lambda col: f.loc[e].groupby('month')[col].rank(pct=True)
    f.loc[e, 'ret20_pct'] = rank('ret20')
    f.loc[e, 'vol20_pct'] = rank('vol20')
    f.loc[e, 'liq_q'] = f.loc[e].groupby('month').amt20.transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False))
    pos = (f.dd_base <= -.30) & (f.off_base <= .50) & (f.ma10_up == 1)
    right = (f.close_d > f.ma60) & (f.ma20 > f.ma60) & (f.ma60_slope > 0)
    quiet = (f.ret20_pct < .70) & (f.amt_ratio <= 1.5) & (f.vol20_pct < .50)
    loud = (f.ret20_pct >= .90) | (f.amt_ratio >= 2)
    groups = {'H1': pos & right & quiet, 'C1': pos & right & loud, 'C2': right & quiet, 'C3': pos,
              'C4': right, 'X0': ~loud, 'X1': loud, 'POOL': pd.Series(True, index=f.index)}
    for g, m in groups.items():
        f[g] = e & m.fillna(False)
    for h in (1, 3):
        col = f'fwd_open_{h}'
        bench = f.loc[e & f[col].notna()].groupby(['month', 'liq_q'])[col].mean().rename(f'bench_{h}')
        f = f.merge(bench, left_on=['month', 'liq_q'], right_index=True, how='left')
        f[f'ex_{h}'] = f[col] - f[f'bench_{h}']
    f['name'] = name
    return f


def period_of(month: pd.Period) -> str | None:
    s = str(month)
    for p, lo, hi in PERIODS:
        if lo <= s <= hi:
            return p
    return None


def summarize(f: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, series = [], []
    for g in GROUPS:
        for h in (1, 3):
            ev = f.loc[f[g] & f[f'ex_{h}'].notna(), ['month', f'ex_{h}']]
            per = ev.groupby('month')[f'ex_{h}'].agg(['mean', 'size'])
            per = per.loc[per['size'] >= MIN_GROUP]
            series.append(per.assign(group=g, h=h).reset_index())
            for p, lo, hi in PERIODS + [('all', '2000-01', '2099-12')]:
                s = per.loc[(per.index.astype(str) >= lo) & (per.index.astype(str) <= hi)]
                e = ev.loc[ev.month.isin(s.index)]
                rows.append({'group': g, 'label': LABELS[g], 'h': h, 'period': p, 'months': len(s),
                             'events': int(s['size'].sum()), 'avg_per_month': round(s['size'].mean(), 1) if len(s) else None,
                             'mean_ex_pct': round(100 * s['mean'].mean(), 2) if len(s) else None,
                             'nw_t': round(nw_t(s['mean'].to_numpy(), 0 if h == 1 else 2), 2),
                             'pos_month_pct': round(100 * (s['mean'] > 0).mean(), 1) if len(s) else None,
                             'event_median_ex_pct': round(100 * e[f'ex_{h}'].median(), 2) if len(e) else None})
    return pd.DataFrame(rows), pd.concat(series, ignore_index=True)


def verdict(summary: pd.DataFrame) -> dict:
    s = summary.loc[(summary.h == 3) & (summary.period != 'all')].set_index(['group', 'period']).mean_ex_pct
    periods = [p for p, _, _ in PERIODS]
    h1_pos = all((s.get(('H1', p)) or -1) > 0 for p in periods)
    adds = sum((s.get(('H1', p)) or -9) > (s.get(('C2', p)) or 9) for p in periods)
    return {'h1_positive_all_periods': bool(h1_pos), 'h1_beats_c2_periods': int(adds),
            'supported': bool(h1_pos and adds >= 2),
            'h1_3m_by_period': {p: s.get(('H1', p)) for p in periods},
            'c2_3m_by_period': {p: s.get(('C2', p)) for p in periods}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    ap.add_argument('--tdx-dir', default='C:/new_tdx/vipdoc')
    args = ap.parse_args()
    tdx = Path(args.tdx_dir)
    panel = load_panel(tdx, args.date, years=10, bars=2500)
    target = panel.target
    daily = panel.qfq()
    panel.merged = None
    names = load_name_master(tdx.parent / 'T0002/hq_cache')
    print('[QRB] building features', flush=True)
    f = build(daily, target, names)
    del daily
    OUT.mkdir(parents=True, exist_ok=True)
    summary, series = summarize(f)
    summary.to_csv(OUT / 'summary.csv', index=False, encoding='utf-8-sig')
    series.to_csv(OUT / 'monthly_series.csv', index=False, encoding='utf-8-sig')
    yr = f.loc[f.H1 | f.C2, ['month', 'H1', 'C2', 'ex_3']].dropna(subset=['ex_3'])
    yearly = pd.concat({g: yr.loc[yr[g]].groupby(yr.month.dt.year).ex_3.agg(['mean', 'median', 'size']) for g in ('H1', 'C2')}, axis=1)
    (yearly * ([100, 100, 1] * 2)).round(2).to_csv(OUT / 'yearly_3m.csv', encoding='utf-8-sig')
    last = f.month.max()
    picks = f.loc[(f.month == last) & f.H1, ['ts_code', 'name', 'month', 'close_d', 'dd_base', 'off_base', 'ret20', 'amt_ratio', 'vol20_pct', 'amt20']]
    picks.sort_values('vol20_pct').to_csv(OUT / f'h1_list_{last}.csv', index=False, encoding='utf-8-sig')
    v = verdict(summary)
    audit = {'data_date': str(target.date()), 'last_signal_month': str(last), 'eligible_median_per_month':
             int(f.loc[f.eligible].groupby('month').size().median()), 'h1_list_size': int(len(picks)), 'verdict': v}
    (OUT / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps(audit, ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
