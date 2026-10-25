"""Local 9/21 screening audit plus explicitly labelled CPU research basket."""
import json,struct
from pathlib import Path
from datetime import datetime
import pandas as pd
from screen_siruipu_right_side_v2 import add_indicators
from screen_siruipu_right_side import load_industry_map,load_industry_names,industry_label
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'output'; DATE='2026-09-21'
def read(p): return json.loads((OUT/p).read_text(encoding='utf-8'))
g0=read('qfq_verification.json'); eco=read('limit_ecology_latest.json'); audit=read('integrated_decision_20260921_audit.json')
assert g0['price_adjustment']=='verified_qfq' and g0['full_market_checks']['latest_anchor']['max_date']==DATE
assert eco['as_of_trade_date']==DATE and eco['coverage']>=.8
old=read('a_share_analysis_20260920_cxmt_integrated.json')['rows']
universe={r['code']:r['name'] for r in old}
mapping={
'688041.SH':('海光信息','国产CPU替代'), '688047.SH':('龙芯中科','国产CPU替代'),
'000066.SZ':('中国长城','国产整机/生态联想'), '688008.SH':('澜起科技','内存接口/互连'),
'688123.SH':('聚辰股份','内存配套芯片'), '002156.SZ':('通富微电','AMD封测业务关联'),
'600584.SH':('长电科技','封装扩散'), '002916.SZ':('深南电路','载板/PCB扩散'),
'002436.SZ':('兴森科技','载板/PCB扩散'), '002463.SZ':('沪电股份','PCB扩散'),
'603228.SH':('景旺电子','PCB扩散'), '600183.SH':('生益科技','覆铜板扩散'),
'300476.SZ':('胜宏科技','PCB扩散'), '688300.SH':('联瑞新材','封装材料扩散'),
'688535.SH':('华海诚科','封装材料扩散'), '688200.SH':('华峰测控','测试设备扩散'),
'688521.SH':('芯原股份','IP/Chiplet扩散'), '002837.SZ':('英维克','散热扩散'),
'300499.SZ':('高澜股份','散热扩散'), '000977.SZ':('浪潮信息','服务器整机'),
'603019.SH':('中科曙光','服务器整机'), '000938.SZ':('紫光股份','服务器/网络'),
'301018.SZ':('申菱环境','散热扩散'), '300308.SZ':('中际旭创','光互连外圈'),
'300502.SZ':('新易盛','光互连外圈'), '300394.SZ':('天孚通信','光互连外圈')}
for c,(n,l) in mapping.items(): universe[c]=n
A=pd.read_csv(OUT/'right_side_20260921_A_enriched.csv'); B=pd.read_csv(OUT/'right_side_20260921_B_enriched.csv')
for _,r in pd.concat([A,B]).iterrows(): universe[r.ts_code]=r['name']
for r in audit['candidates']: universe[r['ts_code']]=r['name']
aset=set(A.ts_code); bset=set(B.ts_code)
df=pd.read_parquet(OUT/'tdx_stock_daily_with_rps.parquet',columns=['trade_date','ts_code','open','high','low','close','vol','amount','RPS_50','RPS_120','RPS_250'])
df['trade_date']=pd.to_datetime(df.trade_date)
assert df.trade_date.max()==pd.Timestamp(DATE)
df=df[df.ts_code.isin(universe)].sort_values(['ts_code','trade_date'])
assert not df.duplicated(['ts_code','trade_date']).any()
imap=load_industry_map(Path('C:/new_tdx/T0002/hq_cache/tdxhy.cfg')).set_index('ts_code').industry.to_dict()
names=load_industry_names(Path('C:/new_tdx/incon.dat')); irank={r['industry']:i+1 for i,r in enumerate(audit['industries'])}
rows=[]; missing=[]; examples=[]
for code,name in universe.items():
    z=df[df.ts_code.eq(code)].reset_index(drop=True)
    if len(z)<260 or z.trade_date.iloc[-1]!=pd.Timestamp(DATE):
        missing.append({'code':code,'name':name,'reason':'当日数据或历史长度不足'}); continue
    z=add_indicators(z); r=z.iloc[-1]; p=z.iloc[-2]
    rawpath=Path('C:/new_tdx/vipdoc')/code[-2:].lower()/'lday'/f'{code[-2:].lower()}{code[:6]}.day'
    if not rawpath.exists(): missing.append({'code':code,'name':name,'reason':'缺TDX锚'});continue
    with rawpath.open('rb') as f: f.seek(-32,2); bar=struct.unpack('<IIIIIfII',f.read(32))
    assert bar[0]==20260921 and max(abs(float(r[k])-bar[j]/100) for j,k in enumerate(['open','high','low','close'],1))<.011,code
    c=float(r.close); m20=float(r.M20); m60=float(r.M60); bias=(c/m20-1)*100
    atr=float(pd.concat([z.high-z.low,(z.high-z.close.shift()).abs(),(z.low-z.close.shift()).abs()],axis=1).max(axis=1).tail(20).mean())
    rank=irank.get(imap.get(code)); adv=float(z.amount.iloc[-21:-1].median()/1e8)
    failures=[]
    for cond,reason in [(c>m20,'未站上MA20'),(c>m60,'未站上MA60'),(r.RPS_120>=80,'RPS120<80'),(bias<=12,'乖离>12%'),(adv>=1.5,'流动性不足')]:
        if not cond: failures.append(reason)
    basic=not failures; g2=basic and rank is not None and rank<=3
    if rank is None or rank>3: failures.append('行业不在前3')
    s20=float(r.M20-p.M20); s60=float(r.M60-p.M60)
    phase='缺趋势确认' if c<=m20 or c<=m60 else '偏热' if bias>12 else 'MA20未转升' if s20<=0 else '修复启动' if m20<m60 or r.M10<m20 or s60<0 else '均线结构较完整'
    floor=max(m20,m60); zone=[round(floor*1.003,2),round(min(floor*1.02,c),2)] if c>floor*1.003 else None
    entry=zone[1] if zone else c; lp=.2 if code[:3] in ('300','301','688','689') else .1
    h20=float(z.high.iloc[-21:-1].max()); h60=float(z.high.iloc[-61:-1].max()); high=float(r.high)
    levels=sorted(set(v for v in (high,h20,h60) if v>entry))
    pressure=levels[0] if levels else None; D=max(entry-floor,entry*lp*.6,atr)
    rr=(pressure-entry)/D if pressure is not None else None
    row=dict(code=code,name=name,group=mapping.get(code,('', '既有池/全市场筛选'))[1],cpu_basket=code in mapping,
      industry=industry_label(imap.get(code,''),names),industry_rank=rank,close=c,high=high,low=float(r.low),ma10=float(r.M10),ma20=m20,ma60=m60,
      ma20_slope=s20,ma60_slope=s60,ret1=(c/float(p.close)-1)*100,ret5=(c/float(z.close.iloc[-6])-1)*100,ret20=(c/float(z.close.iloc[-21])-1)*100,
      rps50=float(r.RPS_50),rps120=float(r.RPS_120),rps250=float(r.RPS_250),bias=bias,atr=atr,atr_pct=atr/c*100,
      volume_ratio=float(r.vol/r.VM20),adv_median=adv,A=code in aset,B=code in bset,basic=basic,g2=g2,failures=failures,
      phase=phase,zone=zone,invalid=floor,pressure=pressure,high20=h20,high60=h60,rr=rr,D=D,
      tdx_stage='QQR' if r.TDX_QQR else 'QR' if r.TDX_QR else 'QD' if r.TDX_QD else 'YJ' if r.TDX_YJ else '无',
      recent=[{'d':str(t.trade_date.date()),'c':float(t.close),'m20':float(t.M20),'m60':float(t.M60)} for _,t in z.tail(60).iterrows()])
    rows.append(row)
    # Counterexample only, not a performance estimate: present-day selected universe is biased.
    if code in mapping:
        valid=(z.trade_date>=pd.Timestamp('2025-09-01'))&(z.trade_date<=pd.Timestamp('2026-09-11'))
        cond=(z.YJ|z.QD)&(z.close>z.M60)&(z.RPS_120>=80)&(z.close/z.M20<=1.12)&(z.amount.shift(1).rolling(20).median()>=1.5e8)
        future=z.close.shift(-6)/z.open.shift(-1)-1
        eligible=z[valid&cond&future.notna()]
        if len(eligible):
            ix=future[eligible.index].idxmin()
            if future[ix]<0: examples.append(dict(code=code,name=name,signal=str(z.loc[ix,'trade_date'].date()),entry_date=str(z.loc[ix+1,'trade_date'].date()),exit_date=str(z.loc[ix+6,'trade_date'].date()),gross_return_pct=float(future[ix]*100),entry=float(z.loc[ix+1,'open']),exit=float(z.loc[ix+6,'close'])))
result=dict(asof=DATE,generated=datetime.now().astimezone().isoformat(),rows=rows,excluded=missing,market=audit['market'],industries=audit['industries'],gates=audit['gates'],risk=audit['risk_state'],screen_audit=read('right_side_20260921_audit.json'),tdx_count=read('siruipu_right_side_v2_20260921.json')['tdx_exact_count'],counterexamples=sorted(examples,key=lambda x:x['gross_return_pct'])[:6],counterexample_limit='只证明相似条件仍有亏损样本；当前股票池回看、有幸存者偏差、未建成交/涨跌停/停牌模型，非策略回测或胜率估计')
(OUT/'cpu_right_side_20260921_research.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
for r in rows:
    if r['cpu_basket'] or r['name'] in ['拓荆科技','芯源微','江丰电子','药康生物','百普赛斯','义翘神州','力芯微','纳芯微']:
        print(f"{r['name']} {r['code']} {c if False else r['close']:.2f} ret={r['ret1']:.1f} rps={r['rps120']:.1f} bias={r['bias']:.1f} ma20/60={r['ma20']:.2f}/{r['ma60']:.2f} {r['phase']} A/B={r['A']}/{r['B']} G2={r['g2']} ind={r['industry']}#{r['industry_rank']} zone={r['zone']} near={r['pressure']} rr={r['rr']}")
print(json.dumps({'rows':len(rows),'missing':missing,'counterexamples':result['counterexamples']},ensure_ascii=False))
