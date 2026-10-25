from __future__ import annotations
import json, struct, sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'
sys.path.insert(0,str(ROOT/'tmp'))
from screen_siruipu_right_side_v2 import add_indicators
from screen_siruipu_right_side import load_industry_map, load_industry_names, industry_label

def read(name): return json.loads((OUT/name).read_text(encoding='utf-8'))

def main():
    gate=read('qfq_verification.json'); eco=read('limit_ecology_latest.json'); audit=read('integrated_decision_20260918_audit.json')
    assert gate['price_adjustment']=='verified_qfq'
    assert gate['full_market_checks']['latest_anchor']['max_date']=='2026-09-18'
    assert eco['as_of_trade_date']=='2026-09-18' and eco['coverage']>=.8
    universe={}
    for filename,key,group in [('a_share_analysis_20260919_analog_chip.json','peers','模拟芯片'),('a_share_analysis_20260919_user_shortlist.json','stocks','原自选科技'),('a_share_analysis_20260919_ai_drug_basket.json','stocks','创新药研发链')]:
        for x in read(filename)[key]: universe[x['code']]={'name':x['name'],'group':group}
    added={'002371.SZ':'北方华创','688012.SH':'中微公司','688082.SH':'盛美上海','688120.SH':'华海清科','688361.SH':'中科飞测','300604.SZ':'长川科技','688627.SH':'精智达','603986.SH':'兆易创新','300458.SZ':'全志科技','301308.SZ':'江波龙','688525.SH':'佰维存储','300782.SZ':'卓胜微'}
    # 新增DRAM制造及存储产品对照，不把射频或SoC加入消息受益池。
    added.pop('300458.SZ'); added.pop('300782.SZ')
    for c,n in added.items(): universe[c]={'name':n,'group':'存储链新增对照'}
    direct={'688072.SH':'薄膜沉积','002371.SZ':'刻蚀/沉积等平台设备','688012.SH':'刻蚀','688082.SH':'清洗','688037.SH':'涂胶显影','688120.SH':'CMP','688361.SH':'检测','300604.SZ':'测试','688627.SH':'测试','300666.SZ':'靶材','688146.SH':'特气'}
    cols=['trade_date','ts_code','open','high','low','close','vol','amount','RPS_50','RPS_120','RPS_250']
    raw=pd.read_parquet(OUT/'tdx_stock_daily_with_rps.parquet',columns=cols)
    raw['trade_date']=pd.to_datetime(raw.trade_date)
    assert raw.trade_date.max()==pd.Timestamp('2026-09-18')
    raw=raw[raw.ts_code.isin(universe)].sort_values(['ts_code','trade_date'])
    assert not raw.duplicated(['ts_code','trade_date']).any()
    imap=load_industry_map(Path('C:/new_tdx/T0002/hq_cache/tdxhy.cfg')).set_index('ts_code').industry.to_dict()
    inames=load_industry_names(Path('C:/new_tdx/incon.dat'))
    irank={x['industry']:i+1 for i,x in enumerate(audit['industries'])}
    A=pd.read_csv(OUT/'right_side_20260918_A_enriched.csv'); B=pd.read_csv(OUT/'right_side_20260918_B_enriched.csv')
    aset=set(A.ts_code); bset=set(B.ts_code)
    rows=[]
    for code,info in universe.items():
        g=raw[raw.ts_code.eq(code)].reset_index(drop=True)
        assert len(g)>=260 and g.trade_date.iloc[-1]==pd.Timestamp('2026-09-18'),code
        z=add_indicators(g); r=z.iloc[-1]; p=z.iloc[-2]
        tr=pd.concat([z.high-z.low,(z.high-z.close.shift()).abs(),(z.low-z.close.shift()).abs()],axis=1).max(axis=1)
        c=float(r.close); a=float(tr.tail(20).mean()); ma20=float(r.M20); ma60=float(r.M60)
        volratio=float(r.vol/r.VM20); advmedian=float(z.amount.iloc[-21:-1].median()/1e8)
        path=Path('C:/new_tdx/vipdoc')/code[-2:].lower()/'lday'/f'{code[-2:].lower()}{code[:6]}.day'
        with path.open('rb') as f:
            f.seek(-32,2); bar=struct.unpack('<IIIIIfII',f.read(32))
        assert bar[0]==20260918 and max(abs(float(r[k])-bar[j]/100) for j,k in enumerate(['open','high','low','close'],1))<.011,code
        physical=len(g)>=260 and c>ma20 and c>ma60 and r.RPS_120>=80 and c/ma20<=1.12 and advmedian>=1.5
        rank=irank.get(imap.get(code)); g2=physical and rank is not None and rank<=3
        blockers=[]
        if c<=ma20: blockers.append('未站上MA20')
        if c<=ma60: blockers.append('未站上MA60')
        if r.RPS_120<80: blockers.append('RPS120<80')
        if c/ma20>1.12: blockers.append('MA20乖离>12%')
        if advmedian<1.5: blockers.append('前20日成交额中位数<1.5亿')
        if rank is None or rank>3: blockers.append('行业不在前3')
        ret5=float((c/z.close.iloc[-6]-1)*100)
        s20=float(r.M20-p.M20); s60=float(r.M60-p.M60)
        if c<=ma20 or c<=ma60: phase='尚缺价格确认'
        elif c/ma20>1.12 or ret5>20: phase='短线过热'
        elif s20<=0: phase='MA20尚未转升'
        elif ma20<=ma60 or r.M10<=ma20 or s60<0: phase='修复/早期启动'
        else: phase='均线趋势相对完整'
        # 均线仅作9/18静态结构参考；失效在观察区下方，避免旧报告的矛盾。
        floor=max(ma20,ma60)
        zone=[round(floor*1.003,2),round(min(floor*1.02,c),2)] if c>floor*1.003 else None
        invalid=round(floor,2)
        H20=float(z.high.iloc[-21:-1].max()); H60=float(z.high.iloc[-61:-1].max()); high=float(r.high)
        ref=zone[1] if zone else c
        # 最近可见阻力先用当日/20日/60日高；不越过近压只挑最远高点算2R。
        pressures=sorted(set(round(v,2) for v in [high,H20,H60] if v>ref))
        pressure=pressures[0] if pressures else None
        lp=.2 if code[:3] in ('300','301','688') else .1
        effective=max(ref-invalid,lp*.6*ref,a)
        rr=(pressure-ref)/effective if pressure else None
        rows.append(dict(code=code,**info,cxmt_link=direct.get(code,'无直接工艺映射'),close=c,high=high,low=float(r.low),ma5=float(r.M5),ma10=float(r.M10),ma20=ma20,ma60=ma60,ma20_slope=s20,ma60_slope=s60,ret1=(c/float(p.close)-1)*100,ret5=ret5,ret20=(c/float(z.close.iloc[-21])-1)*100,rps50=float(r.RPS_50),rps120=float(r.RPS_120),rps250=float(r.RPS_250),bias=(c/ma20-1)*100,vol_ratio=volratio,adv_mean=float(r.ADV20/1e8),adv_median=advmedian,atr=a,atr_pct=a/c*100,industry=industry_label(imap.get(code,''),inames),industry_rank=rank,phase=phase,g2_basic=bool(physical),g2=bool(g2),blockers=blockers,A=code in aset,B=code in bset,tdx_stage='QQR' if r.TDX_QQR else 'QR' if r.TDX_QR else 'QD' if r.TDX_QD else 'YJ' if r.TDX_YJ else '无',zone=zone,invalid=invalid,high20=H20,high60=H60,pressure=pressure,rr=rr,risk_distance=effective,raw_anchor_ok=True))
    result=dict(asof='2026-09-18',generated=datetime.now().astimezone().isoformat(),rows=rows,market=audit['market'],industries=audit['industries'],gates=audit['gates'],risk=audit['risk_state'],news_date='2026-09-20',source='本地TDX当日原始OHLC锚+本地v_daily_qfq导出的RPS面板',quality=dict(symbols=len(rows),raw_anchor_matches=len(rows),duplicate_rows=0,scope=gate['verification_scope']),corrections=['旧报告候选表被top_n=25截断，未入展示不代表未过G2','旧回踩区部分低于MA60失效线；本次观察区下沿必须高于结构失效线','G2生产实现ADV20为前20日成交额中位数；另展示含当日20日均额，不混用','本次排序和观察区为人工研究规则，未进行胜率回测'])
    (OUT/'a_share_analysis_20260920_cxmt_integrated.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for x in rows:
        print(f"{x['name']} {x['code']} C={c if False else x['close']:.2f} {x['phase']} RPS={x['rps120']:.1f} bias={x['bias']:.1f}% MA20/60={x['ma20']:.2f}/{x['ma60']:.2f} 行业={x['industry']}#{x['industry_rank']} G2={x['g2']} A/B={x['A']}/{x['B']} zone={x['zone']} pressure={x['pressure']} RR={x['rr']:.2f}" if x['rr'] is not None else str(x))

if __name__=='__main__': main()
