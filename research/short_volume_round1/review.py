"""Review an existing frozen event bank; no rule modification or parameter search."""
import argparse
import hashlib
import json
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024):h.update(block)
    return h.hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);args=ap.parse_args()
    out=args.run.resolve();root=Path(__file__).resolve().parents[2]
    result=json.loads((out/'results.json').read_text(encoding='utf-8'))
    assert digest(out/'protocol-source.json')==result['protocol_sha256']
    ev=pd.read_parquet(out/'events.parquet')
    assert len(ev)==result['outcome_rows']
    assert not ev.duplicated(['code','signal_date','horizon']).any()
    assert ev.loc[ev.status=='estimated_filled','gross'].notna().all()
    assert not ev.loc[ev.status!='estimated_filled','gross'].notna().any()
    sensitivity=[];examples=[]
    months=sorted(ev.month.unique())
    for arm in ('B0','S1','S1_chip','S2','S2_chip'):
        d=ev.loc[ev[arm]&(ev.horizon==3)&(ev.status=='estimated_filled')].copy()
        for label in ['all','2025_discovery','2026_pre_case','2026_known_case']:
            p=d if label=='all' else d[d.period==label]
            if not len(p):continue
            bymonth=p.groupby('month').gross.agg(['sum','count'])
            samples=bymonth.reindex(months,fill_value=0)
            rng=np.random.default_rng(20261024)
            boot=rng.integers(0,len(samples),size=(2000,len(samples)))
            sums=samples['sum'].to_numpy()[boot].sum(axis=1)
            counts=samples['count'].to_numpy()[boot].sum(axis=1)
            mean=sums[counts>0]/counts[counts>0]-.004
            ci=np.quantile(mean,[.025,.975])*100 if len(bymonth)>=6 else [None,None]
            clipped=p.gross.clip(p.gross.quantile(.01),p.gross.quantile(.99))
            byboard=p.groupby('board').gross.agg(['mean','count'])
            without_case=p.loc[p.code!='301047.SZ']
            sensitivity.append({'arm':arm,'period':label,'events':len(p),'months_with_signal':len(bymonth),
                'cost40_mean_ci95_pct':[float(x) if x is not None else None for x in ci],
                'winsorized_1pct_cost40_mean_pct':float((clipped-.004).mean()*100),
                'without_301047_cost40_mean_pct':float((without_case.gross-.004).mean()*100) if len(without_case) else None,
                'board':{k:{'events':int(v['count']),'cost40_mean_pct':float((v['mean']-.004)*100)} for k,v in byboard.to_dict('index').items()},
                'top5_symbol_count_share_pct':float(p.code.value_counts().head(5).sum()/len(p)*100),
                'median_mae_pct':float(p.mae.median()*100)})
        if len(d):
            for name,part in [('worst',d.nsmallest(5,'gross')),('best',d.nlargest(5,'gross'))]:
                for r in part[['code','signal_date','gross','mae','mfe','board']].to_dict('records'):
                    examples.append({'arm':arm,'kind':name,**r})
    # Freeze the actual source query result. This is a post-run receipt of the unchanged local DB.
    dbpath=root/'data/hithink/market.duckdb';before=dbpath.stat()
    dbhash=digest(dbpath)
    con=duckdb.connect(str(dbpath),read_only=True);con.execute('SET enable_progress_bar=false')
    factors=con.execute(result['sql']).fetchdf();con.close()
    assert len(factors)==result['factor_rows']
    factorfile=out/'factor_anchors.parquet';factors.to_parquet(factorfile,index=False)
    after=dbpath.stat()
    assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'DB changed while freezing'
    receipt={'database_sha256':dbhash,'size':after.st_size,'mtime_ns':after.st_mtime_ns,
        'factor_snapshot_sha256':digest(factorfile),'factor_rows':len(factors),
        'scope':'post-run read-only source snapshot; pre-run source file not hashed as a whole',
        'protocol_source_sha256':digest(out/'protocol-source.json'),'event_bank_sha256':digest(out/'events.parquet')}
    payload={'bootstrap_seed':20261024,'bootstrap_iterations':2000,
        'bootstrap_method':'resample entire calendar months; event-weighted mean ratio; no CI when fewer than 6 signal-months',
        'warning':'Intervals do not repair selection bias, incomplete ST/IPO execution, multiple comparisons or portfolio accounting',
        'sensitivity':sensitivity,'examples':examples,'source_receipt':receipt}
    (out/'review.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'checks':'event uniqueness, status returns, protocol digest, factor snapshot checks passed',
        'factor_snapshot_bytes':factorfile.stat().st_size,
        'all_period_sensitivity':[r for r in sensitivity if r['period']=='all']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
