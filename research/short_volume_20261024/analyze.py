"""Exploratory case study; no portfolio backtest, parameter search or production edits."""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from rps_analyzer.tdx_reader import read_day_file

HERE=Path(__file__).resolve().parent
OUT=ROOT/'outputs/research/short_volume_20261024'
START=pd.Timestamp('2026-08-04')
END=pd.Timestamp('2026-10-23')


def features(d):
    d=d.copy().sort_values('trade_date').reset_index(drop=True)
    d['ret']=d.close.pct_change()
    for n in (3,5,10,15):
        d[f'ma{n}']=d.close.rolling(n,min_periods=n).mean()
        d[f'prior_high{n}']=d.high.shift().rolling(n,min_periods=n).max()
        d[f'ret{n}']=d.close.pct_change(n)
    d['vol_base10']=d.vol.shift().rolling(10,min_periods=10).mean()
    d['vol_ratio']=d.vol/d.vol_base10.replace(0,np.nan)
    d['vol3_ratio']=d.vol.rolling(3).mean()/d.vol_base10.replace(0,np.nan)
    d['active_days3']=(d.vol>d.vol_base10).rolling(3).sum()
    d['clv']=(d.close-d.low)/(d.high-d.low).replace(0,np.nan)
    d['upper_wick']=(d.high-d[['open','close']].max(axis=1))/(d.high-d.low).replace(0,np.nan)
    d['prior_range15']=(d.high.shift().rolling(15).max()-d.low.shift().rolling(15).min())/d.close.shift().rolling(15).mean()
    d['efficiency10']=(d.close-d.close.shift(10)).abs()/d.close.diff().abs().rolling(10).sum().replace(0,np.nan)
    # Daily-bar volume allocated entirely to typical price: explicitly NOT true chip distribution.
    typical=(d.high+d.low+d.close)/3
    overhead=[]
    for i in range(len(d)):
        if i<15: overhead.append(np.nan); continue
        v=d.vol.iloc[i-15:i]; price=typical.iloc[i-15:i]
        overhead.append(float(v[price>d.close.iloc[i]].sum()/v.sum()) if v.sum()>0 else np.nan)
    d['overhead_proxy15']=overhead
    d['activation']=(d.close>d.prior_high10)&(d.vol_ratio>=1.5)&(d.clv>=.65)&(d.upper_wick<=.25)
    d['sustained']=(d.active_days3>=2)&(d.vol3_ratio>=1.3)&(d.close>d.ma5)&(d.ma5>d.ma10)&(d.clv>=.6)
    d['prototype']=d.sustained&(d.close>d.prior_high10)&(d.overhead_proxy15<=.2)&(d.prior_range15<=.20)
    # This is a diagnostic observation, not an immediately executed stop order.
    d['failure']=(d.close<d.ma5)&(d.close<d.low.shift())
    return d


def draw(stock,index,benchmark):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    s=stock.loc[stock.trade_date.between(START,END)].reset_index(drop=True)
    fig,axes=plt.subplots(3,1,figsize=(14,10),gridspec_kw={'height_ratios':[3,1.2,1.6]},sharex=True)
    x=np.arange(len(s))
    for i,r in s.iterrows():
        color='#c84640' if r.close>=r.open else '#287c6c'
        axes[0].plot([i,i],[r.low,r.high],color=color,lw=.8)
        axes[0].add_patch(Rectangle((i-.29,min(r.open,r.close)),.58,max(abs(r.close-r.open),.08),facecolor=color,edgecolor=color))
    for n in (5,10):axes[0].plot(x,s[f'ma{n}'],label=f'MA{n}',lw=1)
    for date in ['2026-08-10','2026-08-20','2026-09-08','2026-09-17','2026-09-30','2026-10-08']:
        i=int(s.index[s.trade_date==pd.Timestamp(date)][0]);r=s.iloc[i]
        axes[0].annotate(date[5:],(i,r.high),xytext=(0,12),textcoords='offset points',ha='center',fontsize=9)
    axes[0].set_title('义翘神州 301047.SZ：日K与短周期量价复盘（事后案例，不是交易建议）',loc='left')
    axes[0].set_ylim(float(s.low.min())*.94,float(s.high.max())*1.09)
    axes[0].set_ylabel('等比前复权价格（元）');axes[0].legend(loc='upper left')
    axes[1].bar(x,s.vol/1e4,color='#687d8a',label='成交量')
    axes[1].plot(x,s.vol_base10/1e4,color='#c58232',lw=1,label='此前10日均量')
    axes[1].set_ylabel('成交量（万股）');axes[1].legend(loc='upper left')
    for name,d in [('义翘神州',stock),('创新药概念指数 886015.TI',index),('创业板指',benchmark)]:
        m=s[['trade_date']].merge(d[['trade_date','close']],on='trade_date',how='left')
        axes[2].plot(x,m.close/m.close.iloc[0]*100,label=name,lw=1.4)
    axes[2].set_ylabel('收盘归一化（8/4=100）');axes[2].legend(loc='upper left',ncol=2)
    ticks=list(range(0,len(s),5));axes[2].set_xticks(ticks,[s.trade_date.iloc[i].strftime('%m-%d') for i in ticks])
    axes[2].set_xlabel('交易日（2026-08-04至2026-10-23；非交易日不占横轴）')
    for ax in axes:ax.grid(axis='y',alpha=.18)
    fig.text(.07,.015,'个股：TDX原始量额＋本地同花顺等比因子；概念指数：同花顺MCP；创业板指：TDX。日K不能证明真实持仓成本或主力行为。',fontsize=9)
    fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(OUT/'review.png',dpi=160);plt.close(fig)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rawpath=Path('C:/new_tdx/vipdoc/sz/lday/sz301047.day')
    raw=read_day_file(str(rawpath));raw=raw.loc[raw.trade_date.between('2026-06-01',END)].copy()
    con=duckdb.connect(str(ROOT/'data/hithink/market.duckdb'),read_only=True)
    con.execute('SET enable_progress_bar=false')
    sql="""SELECT r.date trade_date,r.open db_open,r.high db_high,r.low db_low,r.close db_close,
        r.volume db_volume,r.amount db_amount,f.forward_factor FROM v_daily r
        JOIN calc_adjust_factor_daily f USING(thscode,date)
        WHERE thscode='301047.SZ' AND r.date BETWEEN '2026-06-01' AND '2026-10-23' ORDER BY r.date"""
    factors=con.execute(sql).fetchdf();con.close()
    factors['trade_date']=pd.to_datetime(factors.trade_date)
    m=raw.merge(factors,on='trade_date',validate='one_to_one')
    if len(m)!=len(raw) or m.trade_date.max()!=END:raise ValueError('Factor coverage or latest-date mismatch')
    max_price=max(float((m[k]-m[f'db_{k}']).abs().max()) for k in ('open','high','low','close'))
    amount_rel=float(((m.amount-m.db_amount).abs()/m.amount.replace(0,np.nan)).max())
    vol_rel=float(((m.vol-m.db_volume).abs()/m.vol.replace(0,np.nan)).max())
    if max_price>.011 or not m.forward_factor.gt(0).all():raise ValueError('Invalid raw anchor or factor')
    stock=m[['trade_date','open','high','low','close','amount','vol']].copy()
    stock[['open','high','low','close']]=stock[['open','high','low','close']].mul(m.forward_factor,axis=0)
    stock=features(stock)
    payload=json.loads((HERE/'innovation_index.json').read_text(encoding='utf-8'))
    if payload.get('code')!=0:raise ValueError('Sector API failed')
    idx=pd.DataFrame(payload['data']['item'])
    idx['trade_date']=pd.to_datetime(idx.date_ms,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    idx=idx.rename(columns={'close_price':'close','open_price':'open','high_price':'high','low_price':'low','volume':'vol','turnover':'amount'})
    idx=features(idx)
    bench=read_day_file('C:/new_tdx/vipdoc/sz/lday/sz399006.day')
    reference=json.loads((HERE/'chinext_reference.json').read_text(encoding='utf-8'))
    if reference.get('code')!=0:raise ValueError('Benchmark reference failed')
    rb=pd.DataFrame(reference['data']['item'])
    rb['trade_date']=pd.to_datetime(rb.date_ms,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    bc=bench.merge(rb[['trade_date','close_price']],on='trade_date',validate='one_to_one')
    if len(bc)!=53 or float((bc.close-bc.close_price).abs().max())>.011:
        raise ValueError('Benchmark cross-source mismatch')
    win=stock.loc[stock.trade_date.between(START,END)].copy()
    sector=idx.loc[idx.trade_date.between(START,END)].copy()
    if not win.trade_date.equals(sector.trade_date.set_axis(win.index)):
        if list(win.trade_date)!=list(sector.trade_date):raise ValueError('Case and index calendar mismatch')
    def series_stats(d):
        return {'start_close':float(d.close.iloc[0]),'end_close':float(d.close.iloc[-1]),
                'return_pct':float((d.close.iloc[-1]/d.close.iloc[0]-1)*100),
                'max_close_drawdown_pct':float((d.close/d.close.cummax()-1).min()*100),
                'close_peak_date':str(d.loc[d.close.idxmax(),'trade_date'].date()),'close_peak':float(d.close.max()),
                'up_days':int((d.ret>0).sum()),'down_days':int((d.ret<0).sum())}
    summary={'created_at':datetime.now().astimezone().isoformat(),'start':str(START.date()),'end':str(END.date()),'sessions':len(win),
         'quality':{'stock_raw_anchor_max_ohlc_difference':max_price,'max_amount_relative_difference':amount_rel,'max_volume_relative_difference':vol_rel,
                    'raw_rows':len(raw),'factor_rows':len(m),'benchmark_checked_rows':len(bc),
                    'benchmark_max_close_difference':float((bc.close-bc.close_price).abs().max()),'sql':sql},
         'stock':series_stats(win),'sector':series_stats(sector),
         'prototype_status':'exploratory hypotheses; no optimization; no execution backtest',
         'signal_dates':{k:win.loc[win[k],'trade_date'].dt.strftime('%Y-%m-%d').tolist() for k in ('activation','sustained','prototype','failure')}}
    # Prefix invariance verifies feature code, not raw corporate-action PIT completeness.
    cols=['vol_ratio','vol3_ratio','overhead_proxy15','activation','sustained','prototype','failure']
    for i in range(30,len(stock)):
        a=features(stock.iloc[:i+1]).iloc[-1][cols]
        pd.testing.assert_series_equal(a,stock.iloc[i][cols],check_names=False)
    summary['prefix_test']='all evaluated prefixes equal, fixed input price basis'
    windows=[('首轮推进','2026-08-04','2026-08-17'),('首轮回撤','2026-08-17','2026-09-04'),
             ('低位反复','2026-09-04','2026-09-16'),('二次推进','2026-09-16','2026-09-30'),('再次回撤','2026-09-30','2026-10-23')]
    summary['descriptive_phases']=[]
    for name,a,b in windows:
        s=stock[stock.trade_date.between(a,b)];t=idx[idx.trade_date.between(a,b)]
        summary['descriptive_phases'].append({'label':name,'start':a,'end':b,
            'stock_return_pct':float((s.close.iloc[-1]/s.close.iloc[0]-1)*100),
            'sector_return_pct':float((t.close.iloc[-1]/t.close.iloc[0]-1)*100),
            'stock_mean_amount_yi':float(s.amount.mean()/1e8)})
    summary['phase_caveat']='事后分段描述，端点不得转成事前交易信号；各段共享边界日。'
    summary['input_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [rawpath,HERE/'innovation_index.json',Path(__file__)]}
    # Safe full warmup snapshot; no credentials or private holdings.
    for name,d in [('stock',stock),('sector',idx),('raw_and_factor',m)]:
        d.to_json(OUT/f'{name}.json',orient='records',date_format='iso',force_ascii=False,indent=2)
    combined=win[['trade_date','close']].rename(columns={'close':'stock'}).merge(sector[['trade_date','close']].rename(columns={'close':'sector'}),on='trade_date').merge(bench[['trade_date','close']].rename(columns={'close':'benchmark'}),on='trade_date',validate='one_to_one')
    for k in ('stock','sector','benchmark'):combined[k]=combined[k]/combined[k].iloc[0]*100
    combined.to_json(OUT/'comparison.json',orient='records',date_format='iso',force_ascii=False,indent=2)
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    draw(stock,idx,bench)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    selected=win.loc[win.trade_date.isin(pd.to_datetime(['2026-08-04','2026-08-10','2026-08-18','2026-08-20','2026-09-04','2026-09-08','2026-09-10','2026-09-14','2026-09-17','2026-09-18','2026-09-22','2026-09-30','2026-10-08','2026-10-23']))]
    print(selected[['trade_date','close','ret','vol_ratio','vol3_ratio','clv','overhead_proxy15','prior_range15','activation','prototype']].to_string(index=False))


if __name__=='__main__':main()
