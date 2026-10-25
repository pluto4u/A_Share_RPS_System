"""Read-only 9/23 panel research: named pool plus existing A/observation pool."""
import json, struct
from pathlib import Path
import pandas as pd
import numpy as np
from screen_siruipu_right_side import load_name_master,HQ,load_industry_map,load_industry_names,industry_label
O=Path(__file__).resolve().parents[1]/'output'; DATE='2026-09-23'
def read(n):return json.loads((O/n).read_text(encoding='utf-8'))
gate=read('qfq_verification.json');ec=read('limit_ecology_latest.json')
assert gate['price_adjustment']=='verified_qfq' and gate['full_market_checks']['latest_anchor']['max_date']==DATE
assert ec['as_of_trade_date']==DATE and ec['coverage']>=.8
names=load_name_master(HQ)
given=['301099.SZ','688578.SH','600641.SH','688381.SH','688535.SH','688591.SH','301555.SZ','688045.SH','688690.SH','301183.SZ','001314.SZ','301041.SZ','603893.SH','002119.SZ','301536.SZ']
A=pd.read_csv(O/'right_side_20260923_A_enriched.csv'); aset=set(A.ts_code)
extra=['688008.SH','002463.SZ','002156.SZ','688347.SH','000011.SZ','000620.SZ']
codes=list(dict.fromkeys(given+list(A.ts_code)+extra))
panel=pd.read_parquet(O/'tdx_stock_daily_with_rps.parquet',filters=[('ts_code','in',codes)])
panel.trade_date=pd.to_datetime(panel.trade_date);assert panel.trade_date.max()==pd.Timestamp(DATE)
assert not panel.duplicated(['ts_code','trade_date']).any()
imap=load_industry_map(HQ/'tdxhy.cfg').set_index('ts_code').industry.to_dict();inames=load_industry_names(Path('C:/new_tdx/incon.dat'))
integ=read('integrated_decision_20260923_audit.json'); ranks={r['industry']:i+1 for i,r in enumerate(integ['industries'])}
def cup(g):
    # Fixed search, no outcome optimisation; a screening proxy, never a visual guarantee.
    z=g.tail(240).reset_index(drop=True);n=len(z);found=[]
    for left in range(max(40,n-180),n-39):
        if z.high.iloc[left] < z.high.iloc[max(0,left-5):left+6].max():continue
        lh=float(z.high.iloc[left]); advance=lh/float(z.close.iloc[max(0,left-40):left].min())-1
        for right in range(left+35,min(left+151,n-5)):
            age=n-1-right
            if not 5<=age<=20:continue
            middle=z.iloc[left+1:right];bi=int(middle.low.idxmin());bottom=float(z.low.iloc[bi]);rh=float(z.high.iloc[right]);depth=1-bottom/lh
            handle=z.iloc[right+1:-1];hl=float(handle.low.min());hd=1-hl/rh
            bwidth=int((middle.low<=bottom+(lh-bottom)*.15).sum())
            vr=float(handle.vol.mean()/z.vol.iloc[max(left,right-20):right+1].mean())
            tests={'prior_advance':advance>=.2,'depth':.12<=depth<=.35,'rim':abs(rh/lh-1)<=.08,
                'rounded':bi-left>=10 and right-bi>=10 and bwidth>=5,'handle':.03<=hd<=.12,
                'upper_half':hl>(bottom+lh)/2,'no_deep_handle':rh-hl<=(rh-bottom)/3,
                'dry_volume':vr<=.9,'no_new_high_in_handle':float(handle.high.max())<=rh*1.01,
                'body_ceiling':float(middle.high.max())<=max(lh,rh)*1.03}
            found.append(dict(left=str(z.trade_date.iloc[left].date()),bottom_date=str(z.trade_date.iloc[bi].date()),right=str(z.trade_date.iloc[right].date()),
                pivot=rh,left_high=lh,bottom=bottom,handle_low=hl,depth_pct=depth*100,handle_pct=hd*100,handle_days=age,volume_ratio=vr,tests=tests,passed=sum(tests.values())))
    if not found:return {'status':'未检出杯柄候选','passed':0,'tests':{}}
    best=max(found,key=lambda c:(c['passed'],c['right']))
    best['status']='杯柄候选，仍需目视与突破确认' if best['passed']==10 else '未通过杯柄筛查'
    return best
out=[]
for code in codes:
    g=panel[panel.ts_code.eq(code)].sort_values('trade_date').reset_index(drop=True)
    assert str(g.trade_date.iloc[-1].date())==DATE
    for n in (10,20,60,120):g[f'm{n}']=g.close.rolling(n).mean()
    r=g.iloc[-1];p=g.iloc[-2];c=float(r.close)
    raw=Path('C:/new_tdx/vipdoc')/code[-2:].lower()/'lday'/f'{code[-2:].lower()}{code[:6]}.day'
    with raw.open('rb') as h:h.seek(-32,2);v=struct.unpack('<IIIIIfII',h.read(32))
    assert v[0]==20260923 and max(abs(float(r[k])-v[j]/100) for j,k in enumerate(['open','high','low','close'],1))<.011
    atr=float(pd.concat([g.high-g.low,(g.high-g.close.shift()).abs(),(g.low-g.close.shift()).abs()],axis=1).max(axis=1).tail(20).mean())
    vol=float(r.vol/g.vol.iloc[-21:-1].mean());vol5=float(g.vol.tail(5).mean()/g.vol.iloc[-25:-5].mean())
    up=g.close.diff()>0;last=g.tail(20);uv=float(g.loc[up].tail(0).vol.sum()) if False else float(g.vol.tail(20)[up.tail(20)].sum()/max(1,g.vol.tail(20)[~up.tail(20)].sum()))
    pivot=float(g.high.iloc[-21:-1].max());high60=float(g.high.iloc[-61:-1].max());bias=(c/r.m20-1)*100
    tight=(float(g.high.tail(10).max())/float(g.low.tail(10).min())-1)*100
    low5=float(g.low.tail(5).min());support=max(float(r.m20),float(r.m60))
    entry=round(max(pivot,float(r.high))*1.003,2); stop=round(low5*.995,2)
    # Require positive distance, realistic observed resistance; do not invent targets.
    cap=round(entry*1.01,2)
    swings=[float(g.high.iloc[j]) for j in range(max(2,len(g)-120),len(g)-2) if g.high.iloc[j]>=g.high.iloc[j-2:j+3].max() and g.high.iloc[j]>entry]
    pressure=min(swings) if swings else None
    lp=.2 if code.startswith(('300','301','688')) else .1
    D=max(entry-stop,entry*lp*.6,atr);rr=(pressure-entry)/D if pressure else None
    m20s=float(r.m20-g.m20.iloc[-6]);m60s=float(r.m60-g.m60.iloc[-6])
    shape='两线下/弱修复' if c<=min(r.m20,r.m60) else '待收复MA60' if c<=r.m60 else '跌回MA20' if c<=r.m20 else '偏离均线过大' if bias>12 else '双均线上修复' if r.m20<r.m60 or m60s<0 else '趋势延续'
    cwh=cup(g)
    out.append(dict(code=code,name=names.get(code,code),given=code in given,A=code in aset,B=False,close=c,ret1=(c/float(p.close)-1)*100,
       m20=float(r.m20),m60=float(r.m60),m20s=m20s,m60s=m60s,rps50=float(r.RPS_50),rps120=float(r.RPS_120),rps250=float(r.RPS_250),rps_delta=float(r.RPS_120-g.RPS_120.iloc[-21]),
       bias=bias,atr=atr,vol=vol,vol5=vol5,up_down_volume=uv,adv=float(g.amount.iloc[-21:-1].median()/1e8),tight=tight,
       close_position=float((r.close-r.low)/(r.high-r.low)) if r.high>r.low else .5,
       high=float(r.high),low=float(r.low),low5=low5,pivot=pivot,high60=high60,entry=entry,stop=stop,cap=cap,D=D,rr=rr,pressure=pressure,
       shape=shape,cup=cwh,industry=industry_label(imap.get(code,''),inames),industry_rank=ranks.get(imap.get(code)),
       basic=bool(c>r.m20 and c>r.m60 and r.RPS_120>=80 and bias<=12 and g.amount.iloc[-21:-1].median()>=1.5e8),
       bars=[{k:(str(t.trade_date.date()) if k=='date' else float(t[k])) for k in ['date','open','high','low','close','vol','m20','m60']} for _,t in g.tail(180).iterrows()]))
result={'asof':DATE,'rows':out,'given':given,'g0':gate['verification_scope'],'market':integ['market'],'risk':integ['risk_state'],'source':'TDX raw anchors / local qfq panel','RPS_check':read('right_side_20260923_audit.json')['rps_check']}
(O/'review_15_20260924.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
for r in out:print(f"{r['code']} {r['name']} C={r['close']:.2f} {r['shape']} RPS={r['rps120']:.1f} bias={r['bias']:.1f} vol={r['vol']:.2f} ADV={r['adv']:.1f} A={r['A']} basic={r['basic']} cup={r['cup']['passed']}/10 pivot={r['pivot']:.2f} stop={r['stop']:.2f} R={r['rr']}")
