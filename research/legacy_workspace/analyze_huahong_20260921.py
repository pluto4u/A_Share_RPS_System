import json, struct
from pathlib import Path
import pandas as pd
from screen_siruipu_right_side_v2 import add_indicators

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'output'
gate=json.loads((OUT/'qfq_verification.json').read_text(encoding='utf-8'))
assert gate['price_adjustment']=='verified_qfq'
assert gate['full_market_checks']['latest_anchor']['max_date']=='2026-09-21'
z=pd.read_parquet(OUT/'tdx_stock_daily_with_rps.parquet',filters=[('ts_code','==','688347.SH')]).sort_values('trade_date').reset_index(drop=True)
z=add_indicators(z); r=z.iloc[-1]; p=z.iloc[-2]
assert str(pd.Timestamp(r.trade_date).date())=='2026-09-21'
path=Path('C:/new_tdx/vipdoc/sh/lday/sh688347.day')
with path.open('rb') as f:
    f.seek(-32,2); bar=struct.unpack('<IIIIIfII',f.read(32))
assert bar[0]==20260921
assert max(abs(float(r[k])-bar[i]/100) for i,k in enumerate(['open','high','low','close'],1))<.011
atr=pd.concat([z.high-z.low,(z.high-z.close.shift()).abs(),(z.low-z.close.shift()).abs()],axis=1).max(axis=1).tail(20).mean()
fields=['open','high','low','close','vol','amount','M5','M10','M20','M60','M120','RPS_50','RPS_120','RPS_250','DIF','DEA','MH','LOWAGE','PEAKAGE']
d={k:float(r[k]) for k in fields}
d.update(code='688347.SH',name='华虹宏力',asof='2026-09-21',bars=len(z),raw_anchor=list(bar),bias20=(r.close/r.M20-1)*100,ret1=(r.close/p.close-1)*100,ret5=(r.close/z.close.iloc[-6]-1)*100,ret20=(r.close/z.close.iloc[-21]-1)*100,ma20_change=float(r.M20-p.M20),ma60_change=float(r.M60-p.M60),volratio=float(r.vol/r.VM20),atr20=float(atr),adv20_median=float(z.amount.iloc[-21:-1].median()/1e8),high20_prior=float(z.high.iloc[-21:-1].max()),high60_prior=float(z.high.iloc[-61:-1].max()),drawdown_formula=float((z.high.tail(60).max()-z.low.tail(30).min())/z.high.tail(60).max()*100))
for k in ['DD','LOWNEAR','ORDER_OK','YJ','QD','QR','QQR','TDX_YJ','TDX_QD','TDX_QR','TDX_QQR']:
    d[k]=bool(r[k])
for x in ['A','B']:
    d[x]=bool(pd.read_csv(OUT/f'right_side_20260921_{x}_enriched.csv').ts_code.eq('688347.SH').any())
audit=json.loads((OUT/'integrated_decision_20260921_audit.json').read_text(encoding='utf-8'))
d['full_G2']=any(t['ts_code']=='688347.SH' for t in audit['candidates'])
d['recent']=json.loads(z.tail(65)[['trade_date','open','high','low','close','vol','M20','M60']].to_json(orient='records',date_format='iso'))
(OUT/'huahong_20260921_analysis.json').write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({k:v for k,v in d.items() if k!='recent'},ensure_ascii=False,indent=2))
print(z.tail(12)[['trade_date','open','high','low','close','vol','M20','M60']].to_string(index=False))
