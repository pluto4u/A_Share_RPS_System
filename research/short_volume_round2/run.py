"""A frozen next-day-confirmation experiment on the unchanged parent event bank."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('short_round1',ROOT/'research/short_volume_round1/run.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
ARMS=('D0','W1','P1','V1','W2','P2','V2')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def confirmations(d,i):
    """Only close-known t+1/t+2 data; never use t+2 in t+1 tests."""
    result={'observe1':False,'observe2':False,'P1':False,'V1':False,'P2':False,'V2':False}
    p0=d.qclose.iloc[i];l0=d.qlow.iloc[i];cal=int(d.cal.iloc[i])
    price=[];volume=[]
    for offset in (1,2):
        k=i+offset
        if k>=len(d) or int(d.cal.iloc[k])!=cal+offset:break
        if not np.isfinite(d.vol.iloc[k]) or d.vol.iloc[k]<=0:break
        span=d.qhigh.iloc[k]-d.qlow.iloc[k]
        clv=(d.qclose.iloc[k]-d.qlow.iloc[k])/span if span>0 else np.nan
        price.append(bool(d.qclose.iloc[k]>=p0 and d.qlow.iloc[k]>=l0 and clv>=.5))
        prior=d.vol.iloc[max(0,k-10):k]
        volume.append(bool(len(prior)==10 and prior.mean()>0 and d.vol.iloc[k]>=prior.mean()))
        result['observe'+str(offset)]=True
    if result['observe1']:
        result['P1']=price[0];result['V1']=price[0] and volume[0]
    if result['observe2']:
        result['P2']=all(price) and d.qclose.iloc[i+2]>=d.qclose.iloc[i+1]
        result['V2']=result['P2'] and all(volume)
    return result


def outcome(d,i,wait,horizon,code,last_cal):
    """Corrected entry model cannot inspect the entry day's full range or volume."""
    empty={'status':'censored','gross':None,'mae':None,'mfe':None,'entry_date':None,'exit_date':None,'exit_delay':None}
    entry_cal=int(d.cal.iloc[i])+wait+1
    if entry_cal>last_cal:return empty
    arr=d.cal.to_numpy();e=int(np.searchsorted(arr,entry_cal))
    if e>=len(d) or int(arr[e])!=entry_cal:return {**empty,'status':'missing_entry_session'}
    f=d.factor.to_numpy();op=d.open.to_numpy();cl=d.close.to_numpy();lo=d.low.to_numpy();hi=d.high.to_numpy()
    ref=cl[e-1]*f[e-1]/f[e]
    limit=parent.indicative_limit(code)-.003
    if op[e]/ref-1>=limit:return {**empty,'status':'indicative_open_rejected'}
    target=entry_cal+horizon-1
    if target>last_cal:return empty
    k=int(np.searchsorted(arr,target))
    while k<len(d) and int(arr[k])<=target+5:
        reference=cl[k-1]*f[k-1]/f[k]
        if cl[k]/reference-1> -limit:break
        k+=1
    if k>=len(d) or int(arr[k])>target+5:return {**empty,'status':'unresolved_exit'}
    entry=op[e]*f[e]
    return {'status':'estimated_filled','gross':float(cl[k]*f[k]/entry-1),
        'mae':float(min(0,(lo[e:k+1]*f[e:k+1]).min()/entry-1)),
        'mfe':float(max(0,(hi[e:k+1]*f[e:k+1]).max()/entry-1)),
        'entry_date':d.date.iloc[e].strftime('%Y-%m-%d'),'exit_date':d.date.iloc[k].strftime('%Y-%m-%d'),
        'exit_delay':int(arr[k])-target}


def aggregate(bank):
    rows=[]
    for (arm,horizon),part in bank.groupby(['arm','horizon'],sort=False):
        for period in ('all','2025_discovery','2026_pre_case','2026_known_case'):
            g=part if period=='all' else part.loc[part.period==period]
            chosen=g.loc[g.selected];filled=chosen.loc[chosen.status=='estimated_filled']
            date=filled.groupby('signal_date').gross.mean()
            rows.append({'arm':arm,'period':period,'horizon':int(horizon),'parent_candidates':len(g),'selected':len(chosen),
                'filled':len(filled),'statuses':dict(Counter(g.status)),
                'cost40_mean_pct':float((filled.gross-.004).mean()*100) if len(filled) else None,
                'cost80_mean_pct':float((filled.gross-.008).mean()*100) if len(filled) else None,
                'date_equal_cost40_pct':float((date-.004).mean()*100) if len(date) else None,
                'median_mae_pct':float(filled.mae.median()*100) if len(filled) else None,
                'median_cost40_pct':float((filled.gross-.004).median()*100) if len(filled) else None})
    return rows


def main():
    pb=(HERE/'protocol.json').read_bytes();protocol=json.loads(pb)
    source=ROOT/protocol['parent_run']
    prior=json.loads((source/'results.json').read_text(encoding='utf-8'))
    receipt=json.loads((source/'review.json').read_text(encoding='utf-8'))['source_receipt']
    assert sha(source/'events.parquet')==receipt['event_bank_sha256']
    assert sha(source/'factor_anchors.parquet')==receipt['factor_snapshot_sha256']
    run=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S')
    out=ROOT/'outputs/research/short_volume_round2'/run
    out.mkdir(parents=True,exist_ok=False);(out/'protocol-source.json').write_bytes(pb)
    print(f'Output {out}; frozen {hashlib.sha256(pb).hexdigest()}',flush=True)
    bank=pd.read_parquet(source/'events.parquet')
    signals=bank.loc[bank.S2&(bank.horizon==3),['code','signal_date','period','month']].copy()
    assert len(signals)==1137 and not signals.duplicated(['code','signal_date']).any()
    ledger={r['code']:r for r in json.loads((source/'universe_ledger.json').read_text(encoding='utf-8')) if r['status']=='admitted'}
    codes=sorted(signals.code.unique())
    factors=pd.read_parquet(source/'factor_anchors.parquet',filters=[('thscode','in',codes)])
    fgroups=dict(tuple(factors.groupby('thscode',sort=False)))
    indexpath=Path('C:/new_tdx/vipdoc/sh/lday/sh000001.day')
    calendar=parent.read_raw(indexpath).date
    expected=pd.DatetimeIndex(factors.date.unique()).sort_values()
    assert set(expected).issubset(set(calendar)),'Current index calendar conflicts with parent snapshot'
    calmap=dict(zip(calendar,np.arange(len(calendar))));last_cal=max(calmap.values())
    results=[];diagnostics=[];blocked=[];checks=0
    for seq,(code,g) in enumerate(signals.groupby('code',sort=True)):
        path=Path(ledger[code]['path'])
        if sha(path)!=ledger[code]['sha256']:
            blocked.append({'code':code,'signals':len(g),'reason':'raw_source_changed'});continue
        raw=parent.read_raw(path)
        f=fgroups[code].rename(columns={k:'db_'+k for k in ('open','high','low','close')})
        d=raw.merge(f,on='date',how='left',validate='one_to_one')
        assert d.forward_factor.notna().all()
        for k in ('open','high','low','close'):
            assert (d[k]-d['db_'+k]).abs().max()<=.011
            d['q'+k]=d[k]*d.forward_factor
        d['factor']=d.forward_factor;d['cal']=d.date.map(calmap)
        assert d.cal.notna().all()
        lookup=dict(zip(d.date.dt.strftime('%Y-%m-%d'),range(len(d))))
        for r in g.to_dict('records'):
            i=lookup[r['signal_date']];flags=confirmations(d,i)
            if flags['observe1'] and checks<100:
                assert confirmations(d.iloc[:i+2],i)['P1']==flags['P1']
                assert confirmations(d.iloc[:i+2],i)['V1']==flags['V1']
                checks+=1
            diagnostics.append({**r,**flags})
            for arm in ARMS:
                wait=0 if arm=='D0' else (1 if arm.endswith('1') else 2)
                observed=True if wait==0 else flags['observe'+str(wait)]
                chosen=observed and (arm[0] in ('D','W') or flags[arm])
                for h in (2,3,5):
                    result=outcome(d,i,wait,h,code,last_cal) if chosen else {'status':'filtered_out' if observed else 'no_confirmation_bar',
                        'gross':None,'mae':None,'mfe':None,'entry_date':None,'exit_date':None,'exit_delay':None}
                    results.append({**r,'arm':arm,'horizon':h,'wait':wait,'selected':chosen,**result})
        if (seq+1)%100==0:print(f'{seq+1}/{len(codes)} symbols',flush=True)
    events=pd.DataFrame(results);summary=aggregate(events)
    # Same-entry-date controls. Compare selected vs rejected confirmations, never against earlier entry as proof.
    comparisons=[]
    for arm in ('P1','V1','P2','V2'):
        control='W'+arm[-1]
        for h in (2,3,5):
            baseline=events.loc[(events.arm==control)&(events.horizon==h)&(events.status=='estimated_filled')]
            selection=events.loc[(events.arm==arm)&(events.horizon==h),['code','signal_date','selected']]
            joined=baseline.merge(selection,on=['code','signal_date'],suffixes=('','_confirmed'),validate='one_to_one')
            for period in ('all','2025_discovery','2026_pre_case','2026_known_case'):
                z=joined if period=='all' else joined.loc[joined.period==period]
                accepted=z.loc[z.selected_confirmed];rejected=z.loc[~z.selected_confirmed]
                diff=accepted.gross.mean()-rejected.gross.mean() if len(accepted) and len(rejected) else None
                comparisons.append({'arm':arm,'control':control,'horizon':h,'period':period,'accepted_filled':len(accepted),
                    'rejected_filled':len(rejected),'accepted_minus_rejected_pp':float(diff*100) if diff is not None else None})
    monthly=[];uncertainty=[]
    for arm in ARMS:
        z=events.loc[(events.arm==arm)&(events.horizon==3)&(events.status=='estimated_filled')]
        for month,g in z.groupby('month'):
            monthly.append({'arm':arm,'month':month,'events':len(g),'cost40_mean_pct':float((g.gross-.004).mean()*100)})
        sums=z.groupby('month').gross.agg(['sum','count']).reindex(sorted(signals.month.unique()),fill_value=0)
        if len(sums) and sums['count'].gt(0).sum()>=6:
            rng=np.random.default_rng(20261024);ix=rng.integers(0,len(sums),(2000,len(sums)))
            num=sums['sum'].to_numpy()[ix].sum(1);den=sums['count'].to_numpy()[ix].sum(1)
            ci=np.quantile(num[den>0]/den[den>0]-.004,[.025,.975])*100
            uncertainty.append({'arm':arm,'horizon':3,'cost40_ci95_pct':ci.tolist()})
    events.to_parquet(out/'events.parquet',index=False)
    pd.DataFrame(diagnostics).to_json(out/'confirmation_audit.json',orient='records',indent=2)
    payload={'protocol_id':protocol['id'],'protocol_sha256':hashlib.sha256(pb).hexdigest(),'run_id':run,
        'parent_run':protocol['parent_run'],'parent_events_sha256':receipt['event_bank_sha256'],
        'factor_snapshot_sha256':receipt['factor_snapshot_sha256'],'candidate_signals':len(signals),'processed_signals':len(diagnostics),
        'symbols':len(codes),'blocked':blocked,'prefix_checks':checks,'summary':summary,'comparisons':comparisons,
        'monthly':monthly,'uncertainty':uncertainty,'code_sha256':sha(Path(__file__)),
        'parent_engine_sha256':sha(ROOT/'research/short_volume_round1/run.py'),'caveats':protocol['bounds']}
    (out/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k not in ('summary','comparisons','monthly','uncertainty','caveats')},ensure_ascii=False,indent=2))
    print(pd.DataFrame(summary).query("period=='all' and horizon==3")[['arm','parent_candidates','selected','filled','cost40_mean_pct','date_equal_cost40_pct','median_mae_pct']].to_string(index=False))


if __name__=='__main__':main()
