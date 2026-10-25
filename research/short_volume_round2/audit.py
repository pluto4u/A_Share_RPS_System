"""Independently reconcile saved confirmation results; read-only input artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);args=ap.parse_args()
    out=args.run.resolve();root=Path(__file__).resolve().parents[2]
    s=json.loads((out/'results.json').read_text(encoding='utf-8'))
    parent=root/s['parent_run']
    ev=pd.read_parquet(out/'events.parquet');old=pd.read_parquet(parent/'events.parquet')
    assert not ev.duplicated(['code','signal_date','arm','horizon']).any()
    assert len(ev)==1137*7*3
    assert sha(out/'protocol-source.json')==s['protocol_sha256']
    filled=ev.status.eq('estimated_filled')
    assert ev.loc[filled,'selected'].all()
    assert ev.loc[filled,['gross','mae','mfe']].notna().all().all()
    assert ev.loc[~filled,'gross'].isna().all()
    assert (pd.to_datetime(ev.loc[filled,'entry_date'])>pd.to_datetime(ev.loc[filled,'signal_date'])).all()
    for arm,control in [('P1','W1'),('V1','W1'),('P2','W2'),('V2','W2')]:
        a=ev.loc[(ev.arm==arm)&filled]
        b=ev.loc[(ev.arm==control)&filled]
        m=a.merge(b,on=['code','signal_date','horizon'],suffixes=('_a','_b'),validate='one_to_one')
        assert len(a)==len(m)
        np.testing.assert_allclose(m.gross_a,m.gross_b,rtol=0,atol=0)
        assert m.entry_date_a.equals(m.entry_date_b)
    original=old.loc[(old.horizon==3)&old.S2]
    corrected=ev.loc[(ev.arm=='D0')&(ev.horizon==3)]
    pair=original.merge(corrected,on=['code','signal_date','horizon'],suffixes=('_old','_new'),validate='one_to_one')
    changed=(pair.status_old!=pair.status_new)|((pair.gross_old-pair.gross_new).abs().fillna(0)>1e-12)
    corrections=pair.loc[changed,['code','signal_date','status_old','status_new','gross_old','gross_new','entry_date_old','entry_date_new','exit_date_old','exit_date_new']]
    corrections.to_json(out/'engine_corrections.json',orient='records',date_format='iso',indent=2)
    sensitivity=[]
    for arm in ('D0','W1','P1','V1','W2','P2','V2'):
        d=ev.loc[(ev.arm==arm)&(ev.horizon==3)&filled]
        if not len(d):continue
        cut=d.gross.quantile([.01,.99]);without_top=d.drop(d.gross.nlargest(5).index)
        daily=d.groupby('signal_date').gross.agg(['sum','count'])
        top=daily.sort_values('sum',ascending=False).head(5)
        removed=d.loc[~d.signal_date.isin(top.index)]
        sensitivity.append({'arm':arm,'n':len(d),'cost40_mean_pct':float((d.gross-.004).mean()*100),
            'median_cost40_pct':float((d.gross-.004).median()*100),
            'positive_after40_pct':float(d.gross.gt(.004).mean()*100),
            'drop_best5_event_cost40_pct':float((without_top.gross-.004).mean()*100),
            'winsorized_1pct_cost40_pct':float((d.gross.clip(cut.iloc[0],cut.iloc[1])-.004).mean()*100),
            'remove5_largest_sum_dates_cost40_pct':float((removed.gross-.004).mean()*100),
            'removed_dates':top.index.tolist(),
            'worst5':d.nsmallest(5,'gross')[['code','signal_date','gross','mae']].to_dict('records'),
            'best5':d.nlargest(5,'gross')[['code','signal_date','gross','mae']].to_dict('records')})
    payload={'checks':'protocol, row uniqueness, selected/fill accounting, all same-delay matched entry dates and gross returns exactly reconciled',
        'outcome_rows':len(ev),'engine_correction_3day_events':len(corrections),'sensitivity':sensitivity,
        'event_sha256':sha(out/'events.parquet'),'code_sha256':sha(Path(__file__)),
        'warning':'Removing profitable events/dates and winsorizing are diagnostics only; do not overwrite original results or promote them as an optimized trading rule'}
    (out/'audit.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k!='sensitivity'},ensure_ascii=False,indent=2))
    print(pd.DataFrame(sensitivity).drop(columns=['worst5','best5','removed_dates']).to_string(index=False))


if __name__=='__main__':main()
