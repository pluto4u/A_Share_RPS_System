"""Read-only source audit and candidate enrichment for 2026-09-18."""
import argparse
import collections
import json
from pathlib import Path
import struct
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'
TARGET=pd.Timestamp('2026-09-18')
TAG='20260918'

def tdx_records():
    rows=[]
    for market,prefixes in [('sh',('600','601','603','605','688','689')),('sz',('000','001','002','003','300','301'))]:
        for p in (Path('C:/new_tdx/vipdoc')/market/'lday').glob('*.day'):
            code=p.stem[2:]
            if not code.startswith(prefixes): continue
            size=p.stat().st_size
            if not size or size%32: continue
            with p.open('rb') as f:
                f.seek(-32,2); rec=struct.unpack('<IIIIIfII',f.read(32))
            rows.append({'ts_code':code+'.'+market.upper(),'latest_date':rec[0], 'raw_close':rec[4]/100})
    return rows

def main():
    global TARGET, TAG
    ap=argparse.ArgumentParser();ap.add_argument('--freshness-only',action='store_true')
    ap.add_argument('--date', default=str(TARGET.date()), help='Target trading date, YYYY-MM-DD')
    args=ap.parse_args()
    TARGET=pd.Timestamp(args.date); TAG=TARGET.strftime('%Y%m%d')
    raw=tdx_records()
    freshness={'target':str(TARGET.date()),'tdx_latest_counts':dict(collections.Counter(str(x['latest_date']) for x in raw)), 'tdx_files':len(raw)}
    (OUT/f'right_side_{TAG}_freshness.json').write_text(json.dumps(freshness,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.freshness_only:
        print(json.dumps(freshness,ensure_ascii=False));return
    gate=json.loads((OUT/'qfq_verification.json').read_text(encoding='utf-8'))
    eco=json.loads((OUT/'limit_ecology_latest.json').read_text(encoding='utf-8'))
    assert gate['price_adjustment']=='verified_qfq'
    assert gate['full_market_checks']['latest_anchor']['max_date']==str(TARGET.date())
    assert eco['as_of_trade_date']==str(TARGET.date()) and eco['coverage']>=.8
    panel=pd.read_parquet(OUT/'tdx_stock_daily_with_rps.parquet')
    panel['trade_date']=pd.to_datetime(panel.trade_date)
    panel=panel[panel.trade_date<=TARGET].sort_values(['ts_code','trade_date'])
    assert panel.trade_date.max()==TARGET
    assert not panel.duplicated(['ts_code','trade_date']).any()
    # Independently reconstruct the latest cross-section from the historical price panel.
    rps_check={}
    for n in (50,120,250):
        base=panel.groupby('ts_code').close.shift(n)
        ret=(panel.close-base)/base
        last=panel.trade_date==TARGET
        rank=ret[last].rank(pct=True)*100
        err=(rank-panel.loc[last,f'RPS_{n}']).abs()
        rps_check[str(n)]={'valid':int(rank.notna().sum()),'max_abs_diff':float(err.max())}
        assert err.max()<1e-7
    adf=pd.read_csv(OUT/f'siruipu_right_side_v2_{TAG}.csv',dtype={'ts_code':str})
    adf=adf[adf.role.isin(['DISCOVERY','UPGRADE']) & (adf.adv20_yi>=1.5)].copy()
    bdf=pd.read_csv(OUT/f'bottom_ambush_{TAG}.csv',dtype={'ts_code':str})
    bdf=bdf[bdf.stage=='AMBUSH'].copy()
    codes=set(adf.ts_code)|set(bdf.ts_code)
    from screen_bottom_ambush import add_indicators
    from screen_siruipu_right_side_v2 import add_indicators as add_start
    records=[]
    b_audit=[]
    rawmap={x['ts_code']:x for x in raw}
    for code,g in panel[panel.ts_code.isin(codes)].groupby('ts_code'):
        g=g.sort_values('trade_date').reset_index(drop=True)
        calc=add_indicators(g.tail(650).reset_index(drop=True))
        last=calc.iloc[-1]
        prev=calc.iloc[-2]
        start=add_start(g.tail(500).reset_index(drop=True))
        prev_a=bool(start.iloc[-2].YJ or start.iloc[-2].QD)
        records.append({'ts_code':code,'raw_date':rawmap.get(code,{}).get('latest_date'), 'raw_close':rawmap.get(code,{}).get('raw_close'),
            'ma20':last.MA20,'ma60':last.MA60,'atr20':last.ATR20,'high_today':last.high,'low_today':last.low,
            'prior_high20':calc.high.iloc[-21:-1].max(),'prior_high60':calc.high.iloc[-61:-1].max(),
            'low10':calc.low.tail(10).min(),'ret1_pct':(last.close/prev.close-1)*100,
            'above_ma60':bool(last.close>last.MA60),'ma20_rising':bool(last.MA20>prev.MA20),
            'bars':len(g),'a_signal_previous_day':prev_a,'a_new_vs_previous_day':not prev_a})
        if code in set(bdf.ts_code):
            age=int(last.TURNAGE);turn_idx=len(calc)-1-age
            earlier=calc.iloc[turn_idx+1:-1]
            # Two distinct tests: first qualifying AMBUSH vs first touch of the MA20 zone.
            earlier_touch=earlier[(earlier.low<=earlier.MA20*1.02)]
            earlier_signal=earlier[earlier.AMBUSH]
            b_audit.append({'ts_code':code,'turn_date':str(calc.iloc[turn_idx].trade_date.date()),'turn_age':age,
                'prior_qualifying_signals':[str(d.date()) for d in earlier_signal.trade_date],
                'prior_ma20_zone_touches':[str(d.date()) for d in earlier_touch.trade_date],
                'first_qualifying_signal':bool(earlier_signal.empty),'first_zone_touch':bool(earlier_touch.empty)})
    extra=pd.DataFrame(records)
    if len(extra):
        adf=adf.merge(extra,on='ts_code',how='left');bdf=bdf.merge(extra,on='ts_code',how='left')
        for frame in (adf,bdf):
            if len(frame):
                assert (frame.raw_date==int(TAG)).all()
                assert ((frame.raw_close-frame.close).abs()<=.011).all()
                frame['g2_full']=((frame.RPS_120>=80)&frame.above_ma60&(frame.vs_ma20_pct>0)&(frame.vs_ma20_pct<=12)&(frame.bars>=260))
    adf.to_csv(OUT/f'right_side_{TAG}_A_enriched.csv',index=False,encoding='utf-8-sig')
    bdf.to_csv(OUT/f'right_side_{TAG}_B_enriched.csv',index=False,encoding='utf-8-sig')
    audit={**freshness,'rps_check':rps_check,'A_total':len(adf),'A_rps120_ge80':int((adf.RPS_120>=80).sum()),
        'A_full_numeric_G2':int(adf.g2_full.sum()) if len(adf) else 0,
        'A_new_vs_previous_day':int(adf.a_new_vs_previous_day.sum()) if len(adf) else 0,
        'B_raw':len(bdf),'B_first_audit':b_audit,
        'candidate_raw_anchors':'all match within 0.011 CNY','no_future_rows':True}
    (OUT/f'right_side_{TAG}_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False))

if __name__=='__main__':main()
