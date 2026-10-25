"""Recompute cache endpoint RPS; reconcile sample prices without changing cache."""
import json
import sys
from pathlib import Path
import duckdb
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from rps_analyzer.price_adjust import adjust_one,load_gbbq
from rps_analyzer.tdx_reader import read_day_file
OUT=ROOT/'outputs/diagnostics/rps_validity_20261024'


def main():
    c=duckdb.connect(str(ROOT/'data/hithink/market.duckdb'),read_only=True)
    c.execute('SET enable_progress_bar=false')
    p=str(ROOT/'output/tdx_stock_daily_with_rps.parquet')
    # lead(n) on reverse dates is precisely production shift(n) on ascending dates.
    latest=c.execute('''WITH x AS (SELECT ts_code,trade_date,close,RPS_50,RPS_120,RPS_250,
        lead(close,50) OVER w p50,lead(close,120) OVER w p120,lead(close,250) OVER w p250,
        row_number() OVER w rn FROM read_parquet(?)
        WINDOW w AS(PARTITION BY ts_code ORDER BY trade_date DESC))
        SELECT * FROM x WHERE rn=1 AND trade_date=(SELECT max(trade_date) FROM read_parquet(?))''',[p,p]).fetchdf()
    result={'latest':str(latest.trade_date.max()),'latest_symbols':len(latest),'rps_formula':{}}
    for n in (50,120,250):
        # Match the exact production arithmetic to preserve floating-point ties.
        ret=(latest.close-latest[f'p{n}'])/latest[f'p{n}']
        rank=ret.rank(method='average',pct=True)*100
        diff=(rank-latest[f'RPS_{n}']).abs()
        result['rps_formula'][str(n)]={'recomputable':int(ret.notna().sum()),'max_abs_delta':float(diff.max()),
            'delta_gt_1e_8':int((diff>1e-8).sum()),'null_disagreement':int((rank.isna()!=latest[f'RPS_{n}'].isna()).sum())}
    events=load_gbbq(Path('C:/new_tdx/T0002/hq_cache/gbbq'))
    comparisons=[]
    for code in ('000001.SZ','300033.SZ','600519.SH'):
        market=code[-2:].lower()
        raw=read_day_file(f'C:/new_tdx/vipdoc/{market}/lday/{market}{code[:6]}.day').tail(320)
        tq,_=adjust_one(raw,events.get(code),latest.trade_date.max())
        cached=c.execute('SELECT trade_date,close,amount,vol FROM read_parquet(?) WHERE ts_code=? AND trade_date>=?',[p,code,raw.trade_date.min()]).fetchdf()
        db=c.execute('SELECT date trade_date,close FROM v_daily_qfq WHERE thscode=?',[code]).fetchdf()
        m=cached.merge(tq[['trade_date','close','amount','vol']],on='trade_date',suffixes=('_cache','_tdxq'))
        m=m.merge(db.rename(columns={'close':'close_dbq'}),on='trade_date')
        comparisons.append({'code':code,'rows':len(m),
            'cache_vs_tdx_qfq_max_abs':float((m.close_cache-m.close_tdxq).abs().max()),
            'cache_vs_db_qfq_max_abs':float((m.close_cache-m.close_dbq).abs().max()),
            'cache_vs_tdx_amount_max_abs':float((m.amount_cache-m.amount_tdxq).abs().max()),
            'cache_vs_tdx_vol_max_abs':float((m.vol_cache-m.vol_tdxq).abs().max())})
    result['sample_cache_lineage']=comparisons
    c.close()
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'cache_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
