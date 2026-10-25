"""Frozen-rule, local-file-population event study. Not a portfolio backtest."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
DT=np.dtype([('date','<u4'),('open','<u4'),('high','<u4'),('low','<u4'),('close','<u4'),('amount','<f4'),('vol','<u4'),('reserved','<u4')])
PREFIX={'sh':('600','601','603','605','688','689'),'sz':('000','001','002','003','300','301')}
ARMS=('B0','S1','S1_chip','S2','S2_chip')


def read_raw(path):
    if path.stat().st_size%32:raise ValueError('truncated record')
    r=np.fromfile(path,dtype=DT)
    r=r[(r['date']>=20241201)&(r['date']<=20261023)]
    if not len(r):return pd.DataFrame()
    if (np.diff(r['date'].astype('int64'))<=0).any():raise ValueError('duplicate or unordered date')
    d=pd.DataFrame({k:r[k].astype(float)/(100 if k in ('open','high','low','close') else 1) for k in ('open','high','low','close','amount','vol')})
    d['date']=pd.to_datetime(r['date'].astype(str),format='%Y%m%d',errors='raise')
    if not np.isfinite(d[['open','high','low','close','amount','vol']]).all().all():raise ValueError('nonfinite raw')
    if (d[['open','high','low','close']]<=0).any().any() or (d[['amount','vol']]<0).any().any():raise ValueError('invalid price/liquidity')
    if (d.high<d[['open','low','close']].max(axis=1)).any() or (d.low>d[['open','high','close']].min(axis=1)).any():raise ValueError('OHLC range')
    return d


def features(d):
    d=d.copy()
    c,h,l,v=d.qclose,d.qhigh,d.qlow,d.vol
    prevvol=v.shift().rolling(10,min_periods=10).mean()
    d['vr']=v/prevvol.replace(0,np.nan)
    d['clv']=(c-l)/(h-l).replace(0,np.nan)
    d['range15']=(h.shift().rolling(15).max()-l.shift().rolling(15).min())/c.shift().rolling(15).mean()
    compress=d.range15.le(.20)
    d['base_recent']=compress.shift(1,fill_value=False)|compress.shift(2,fill_value=False)|compress.shift(3,fill_value=False)
    d['drawdown15']=c.shift()/c.shift().rolling(15).max()-1
    d['higher_low']=l.shift().rolling(3).min()>=l.shift(4).rolling(3).min()
    typical=(h+l+c)/3
    overhead=np.full(len(d),np.nan)
    if len(d)>15:
        vp=np.lib.stride_tricks.sliding_window_view(v.to_numpy(),15)[:-1]
        tp=np.lib.stride_tricks.sliding_window_view(typical.to_numpy(),15)[:-1]
        denom=vp.sum(axis=1)
        overhead[15:]=np.divide(np.where(tp>c.to_numpy()[15:,None],vp,0).sum(axis=1),denom,out=np.full(len(denom),np.nan),where=denom>0)
    d['overhead']=overhead
    contiguous=(d.cal-d.cal.shift(18)==18) # all source features + remembered base fit within <20 sessions
    active=d.vol.gt(0)&d.amount.gt(0)
    d['B0']=(c>h.shift().rolling(5).max())&(d.vr>=1.5)&(d.clv>=.65)&(c>d.qopen)&(d.amount.shift().rolling(10).mean()>=1e8)&contiguous&active
    d['S1']=d.B0&d.base_recent
    d['S2']=d.B0&d.drawdown15.le(-.12)&d.higher_low
    d['S1_chip']=d.S1&d.overhead.le(.2)
    d['S2_chip']=d.S2&d.overhead.le(.2)
    return d


def indicative_limit(code):
    return .2 if code.startswith(('300','301','688','689')) else .1


def event_outcome(d,i,horizon,code,last_cal):
    """Return an event diagnostic; board/ST restrictions are explicitly incomplete."""
    n=len(d); signal=int(d.cal.iloc[i]); e=i+1
    base={'status':'censored','entry_date':None,'exit_date':None,'gross':None,'mae':None,'mfe':None,'exit_delay':None}
    if signal+1>last_cal:return base
    if e>=n or int(d.cal.iloc[e])!=signal+1:return {**base,'status':'missing_next_session'}
    factor=d.factor.to_numpy();o=d.open.to_numpy();cl=d.close.to_numpy();hi=d.high.to_numpy();lo=d.low.to_numpy()
    ref=cl[e-1]*factor[e-1]/factor[e]
    limit=indicative_limit(code)-.003
    if o[e]/ref-1>=limit or hi[e]==lo[e]:return {**base,'status':'indicative_entry_rejected'}
    target=signal+1+horizon-1
    if target>last_cal:return base
    k=int(np.searchsorted(d.cal.to_numpy(),target))
    while k<n and int(d.cal.iloc[k])<=target+5:
        prev=cl[k-1]*factor[k-1]/factor[k]
        if cl[k]/prev-1> -limit and hi[k]!=lo[k]:break
        k+=1
    if k>=n or int(d.cal.iloc[k])>target+5:return {**base,'status':'unresolved_exit'}
    entry=o[e]*factor[e]
    low=(lo[e:k+1]*factor[e:k+1]).min()/entry-1
    high=(hi[e:k+1]*factor[e:k+1]).max()/entry-1
    return {**base,'status':'estimated_filled','entry_date':str(d.date.iloc[e].date()),'exit_date':str(d.date.iloc[k].date()),
        'gross':float(cl[k]*factor[k]/entry-1),'mae':float(min(0,low)),'mfe':float(max(0,high)),
        'exit_delay':int(d.cal.iloc[k])-target}


def period(date):
    return '2025_discovery' if date.year==2025 else ('2026_pre_case' if date<pd.Timestamp('2026-08-01') else '2026_known_case')


def summarize(events):
    rows=[];monthly=[]
    for arm in ARMS:
        selected=events.loc[events[arm]]
        for label in ['all','2025_discovery','2026_pre_case','2026_known_case']:
            part=selected if label=='all' else selected.loc[selected.period==label]
            for h in (2,3,5):
                p=part.loc[part.horizon==h];ok=p.loc[p.status=='estimated_filled']
                if p.empty:continue
                daily=ok.groupby('signal_date').gross.mean()
                rows.append({'arm':arm,'period':label,'horizon':h,'events':len(p),'estimated_filled':len(ok),
                    'status_counts':dict(Counter(p.status)),
                    'gross_mean_pct':float(ok.gross.mean()*100) if len(ok) else None,
                    'cost40_mean_pct':float((ok.gross-.004).mean()*100) if len(ok) else None,
                    'cost80_mean_pct':float((ok.gross-.008).mean()*100) if len(ok) else None,
                    'median_gross_pct':float(ok.gross.median()*100) if len(ok) else None,
                    'cost40_positive_pct':float((ok.gross>.004).mean()*100) if len(ok) else None,
                    'date_equal_gross_pct':float(daily.mean()*100) if len(daily) else None,
                    'mae_mean_pct':float(ok.mae.mean()*100) if len(ok) else None,
                    'mfe_mean_pct':float(ok.mfe.mean()*100) if len(ok) else None,
                    'delayed_exits':int(ok.exit_delay.gt(0).sum()),'symbols':int(ok.code.nunique())})
        for (month,h),g in selected.loc[selected.status=='estimated_filled'].groupby(['month','horizon']):
            monthly.append({'arm':arm,'month':month,'horizon':int(h),'events':len(g),
                'gross_mean_pct':float(g.gross.mean()*100),'cost40_mean_pct':float((g.gross-.004).mean()*100)})
    return rows,monthly


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path);ap.add_argument('--max-files',type=int)
    args=ap.parse_args()
    protocol_bytes=(HERE/'protocol.json').read_bytes()
    protocol=json.loads(protocol_bytes.decode('utf-8'))
    run=datetime.now().strftime('%Y%m%dT%H%M%S')
    out=args.output or ROOT/'outputs/research/short_volume_round1'/run
    out.mkdir(parents=True,exist_ok=False)
    digest=hashlib.sha256(protocol_bytes).hexdigest()
    (out/'protocol-source.json').write_bytes(protocol_bytes)
    print(f'Protocol {digest}; output {out}',flush=True)
    calendar=read_raw(Path('C:/new_tdx/vipdoc/sh/lday/sh000001.day')).date
    calmap=dict(zip(calendar,np.arange(len(calendar))))
    last_cal=max(calmap.values())
    dbpath=ROOT/'data/hithink/market.duckdb'
    db=duckdb.connect(str(dbpath),read_only=True);db.execute('SET enable_progress_bar=false')
    sql="""SELECT r.thscode,r.date,r.open,r.high,r.low,r.close,f.forward_factor
        FROM v_daily r JOIN calc_adjust_factor_daily f USING(thscode,date)
        WHERE r.date BETWEEN '2024-12-01' AND '2026-10-23' ORDER BY thscode,date"""
    factors=db.execute(sql).fetchdf();db.close()
    factors['date']=pd.to_datetime(factors.date)
    grouped=dict(tuple(factors.groupby('thscode',sort=False)))
    print(f'Factor anchors {len(factors):,} rows',flush=True)
    prior=ROOT/'outputs/diagnostics/rps_validity_20261024/audit.json'
    excluded={'000615.SZ'}
    if prior.exists():excluded.update(json.loads(prior.read_text(encoding='utf-8'))['tdx_adjustment_exclusions'])
    paths=sorted(p for m in PREFIX for p in Path(f'C:/new_tdx/vipdoc/{m}/lday').glob('*.day') if p.stem[2:5] in PREFIX[m])
    if args.max_files:paths=paths[:args.max_files]
    ledger=[];records=[];checks=0
    for seq,path in enumerate(paths):
        code=path.stem[2:]+'.'+path.stem[:2].upper()
        row={'code':code,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        try:
            if code in excluded:raise ValueError('known_special_action_quarantine')
            raw=read_raw(path)
            if len(raw)<20:raise ValueError('insufficient_history')
            f=grouped.get(code)
            if f is None:raise ValueError('no_factor_symbol')
            d=raw.merge(f.rename(columns={k:'db_'+k for k in ('open','high','low','close')}),on='date',how='left',validate='one_to_one')
            if d.forward_factor.isna().any():raise ValueError('missing_factor_dates')
            if not np.isfinite(d.forward_factor).all() or not d.forward_factor.gt(0).all():raise ValueError('invalid_factor')
            delta=max(float((d[k]-d['db_'+k]).abs().max()) for k in ('open','high','low','close'))
            if delta>.011:raise ValueError('raw_OHLC_anchor_mismatch')
            d['factor']=d.forward_factor
            for k in ('open','high','low','close'):d['q'+k]=d[k]*d.factor
            d['cal']=d.date.map(calmap)
            if d.cal.isna().any():raise ValueError('date_outside_market_calendar')
            d=features(d)
            if checks<40:
                cut=len(d)//2
                pd.testing.assert_frame_equal(features(d.iloc[:cut])[['B0','S1','S2','overhead']],d.iloc[:cut][['B0','S1','S2','overhead']])
                checks+=1
            indices=np.flatnonzero((d.B0&(d.date>=pd.Timestamp('2025-01-01'))).to_numpy())
            prev=-999;count=0
            for i in indices:
                if int(d.cal.iloc[i])-prev<=5:continue
                prev=int(d.cal.iloc[i]);count+=1
                common={'code':code,'board':'20pct' if indicative_limit(code)==.2 else '10pct',
                    'signal_date':d.date.iloc[i].strftime('%Y-%m-%d'),'period':period(d.date.iloc[i]),'month':d.date.iloc[i].strftime('%Y-%m'),
                    **{a:bool(d[a].iloc[i]) for a in ARMS},'vr':float(d.vr.iloc[i]),'overhead':float(d.overhead.iloc[i]),'range15':float(d.range15.iloc[i])}
                for h in (2,3,5):records.append({**common,'horizon':h,**event_outcome(d,i,h,code,last_cal)})
            row.update(status='admitted',bars=len(d),events=count,anchor_max_delta=delta)
        except (ValueError,AssertionError) as exc:row.update(status='excluded',reason=str(exc))
        ledger.append(row)
        if (seq+1)%500==0:print(f'{seq+1}/{len(paths)} files; event outcomes {len(records):,}',flush=True)
    bank=pd.DataFrame(records)
    if bank.empty:raise RuntimeError('No events; inspect frozen rules and coverage before interpreting')
    summaries,monthly=summarize(bank)
    controls=[]
    for arm in ARMS[1:]:
        for h in (2,3,5):
            usable=bank.loc[(bank.horizon==h)&(bank.status=='estimated_filled')]
            control=usable.loc[~(usable.S1|usable.S2)].groupby(['signal_date','board']).gross.agg(['mean','count'])
            selected=usable.loc[usable[arm]].join(control,on=['signal_date','board'],rsuffix='_control')
            matched=selected.loc[selected['count'].notna()]
            for label in ['all','2025_discovery','2026_pre_case','2026_known_case']:
                part=matched if label=='all' else matched.loc[matched.period==label]
                controls.append({'arm':arm,'period':label,'horizon':h,'selected_filled':len(selected if label=='all' else selected.loc[selected.period==label]),
                    'matched':len(part),'mean_delta_pp':float((part.gross-part['mean']).mean()*100) if len(part) else None,
                    'date_equal_delta_pp':float((part.gross-part['mean']).groupby(part.signal_date).mean().mean()*100) if len(part) else None})
    bank.to_parquet(out/'events.parquet',index=False)
    pd.DataFrame(ledger).to_json(out/'universe_ledger.json',orient='records',force_ascii=False,indent=2)
    payload={'protocol_id':protocol['id'],'protocol_sha256':digest,'run_id':run,'partial_run':bool(args.max_files),
        'files_scanned':len(paths),'files_admitted':sum(r['status']=='admitted' for r in ledger),
        'exclusion_counts':dict(Counter(r.get('reason') for r in ledger if r['status']=='excluded')),
        'event_bank_signals':len(bank)//3,'outcome_rows':len(bank),'prefix_checks':checks,
        'summary':summaries,'monthly':monthly,'matched_controls':controls,
        'sql':sql,'factor_rows':len(factors),'caveats':protocol['bounds'],
        'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k not in ('summary','monthly','matched_controls','sql','caveats')},ensure_ascii=False,indent=2),flush=True)
    print(pd.DataFrame(summaries).query("period=='all' and horizon==3")[['arm','events','estimated_filled','cost40_mean_pct','date_equal_gross_pct']].to_string(index=False),flush=True)


if __name__=='__main__':main()
