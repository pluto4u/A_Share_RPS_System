"""Local TDX breadth timing dashboard. See docs/market_timing_method.md."""
import argparse, json, sys, subprocess, tempfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import asdict
import numpy as np
import pandas as pd
# tmp/ may be a junction into research/legacy_workspace, so locate the repo root instead of a fixed depth.
ROOT=next(p for p in Path(__file__).absolute().parents if (p/'rps_analyzer').is_dir())
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'skills/a-share-rotation-trader/scripts'))
from project_paths import LEGACY_OUTPUT_DIR, OUTPUT_ROOT
from build_limit_ecology import load_tail, RECORD_DTYPE
from rps_analyzer.market_timing import TimingConfig, BottomConfig, VolConfig, VOL_COMPONENTS, stock_features, aggregate_days, add_signals, event_study, summarize_events, add_bottom_signals, bottom_event_study, episode_starts, cap_group_check, permutation_test, add_volatility, vol_study
CAP_INDICES={'csi300':('000300.SH','沪深300'),'csi1000':('000852.SH','中证1000')}

def read_output(name):
    """Prefer outputs/; fall back to the legacy output/ folder for files written before the move."""
    new = OUTPUT_ROOT / name
    return new if new.exists() else LEGACY_OUTPUT_DIR / name

def load_limit_official(target):
    """Official same-day pool sizes (THS) to cross-check the TDX board-rule approximation."""
    ms=int(pd.Timestamp(target).tz_localize('Asia/Shanghai').timestamp()*1000); out={}
    for key,pool in (('limit_up','limit-up-pool'),('limit_down','limit-down-pool'),('limit_broken','limit-break-pool')):
        proc=subprocess.run(['hithink-finance.cmd','special',pool,'--date-ms',str(ms),'--size','1','--format','json'],capture_output=True,text=True,encoding='utf-8',errors='replace')
        try:
            body=json.loads((proc.stdout or '').lstrip('﻿'))
            out[key]=body['data']['pagination']['total'] if body.get('ok') else None
        except (ValueError,KeyError,TypeError):
            out[key]=None
    return out

def load_cap_groups():
    """Current constituents only (the API has no as-of date), so history carries look-ahead bias."""
    groups,meta={},{}
    with tempfile.TemporaryDirectory(prefix='timing-cons-') as tmp:
        for label,(code,name) in CAP_INDICES.items():
            fp=Path(tmp)/f'{label}.json'
            proc=subprocess.run(['hithink-finance.cmd','index','constituents','--thscode',code,'--output',str(fp),'--format','json'],capture_output=True,text=True,encoding='utf-8',errors='replace')
            if proc.returncode!=0 or not fp.exists():
                meta[label]={'code':code,'name':name,'error':(proc.stderr or proc.stdout)[:300]}; continue
            body=json.loads(fp.read_text(encoding='utf-8'))['data']
            groups[label]=[x['thscode'] for x in body['item']]
            ts=body.get('timestamp')  # epoch ms of the fetch, not an index-rebalance date
            fetched=datetime.fromtimestamp(ts/1000,ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d') if isinstance(ts,(int,float)) else None
            meta[label]={'code':code,'name':name,'members':len(groups[label]),'as_of':fetched}
    return groups,meta

def clean(x):
    if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()[:10]
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    return x

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date');ap.add_argument('--tdx-dir',default='C:/new_tdx/vipdoc')
    ap.add_argument('--bars',type=int,default=1300);ap.add_argument('--display-days',type=int,default=120)
    ap.add_argument('--divergence-max',type=float,default=.65);ap.add_argument('--breadth-min',type=float,default=.70)
    ap.add_argument('--collapse-pp',type=float,default=25);ap.add_argument('--down-intensity-min',type=float,default=1.5)
    ap.add_argument('--key-step',type=float,default=500);ap.add_argument('--key-tolerance',type=float,default=.01)
    args=ap.parse_args(); out=OUTPUT_ROOT;out.mkdir(parents=True,exist_ok=True)
    cfg=TimingConfig(divergence_max=args.divergence_max,breadth_min=args.breadth_min,collapse_pp=args.collapse_pp,down_intensity_min=args.down_intensity_min,key_step=args.key_step,key_tolerance=args.key_tolerance)
    if args.bars<400 or cfg.key_step<=0 or not 0<cfg.breadth_min<1 or cfg.collapse_pp<=0:raise ValueError('Invalid history or thresholds')
    local=load_tail(Path(args.tdx_dir),args.bars,1,verbose=False)
    target=pd.Timestamp(args.date) if args.date else local.trade_date.max()
    local=local.loc[(local.trade_date<=target)&(local.trade_date>=target-pd.DateOffset(years=5))].copy()
    if local.empty or local.trade_date.max()!=target:raise RuntimeError('Requested date absent from local TDX')
    gate=json.loads(read_output('qfq_verification.json').read_text(encoding='utf-8'))
    if gate['price_adjustment']!='verified_qfq':raise RuntimeError('G0未通过，请先运行verify_qfq.py')
    gate_date=pd.Timestamp(gate['full_market_checks']['latest_anchor']['max_date'])
    if gate_date<target:raise RuntimeError('G0早于目标日')
    start=local.trade_date.min().strftime('%Y-%m-%d'); end=target.strftime('%Y-%m-%d')
    sql=("SELECT r.thscode AS ts_code,r.date AS trade_date,r.close AS anchor_close,f.forward_factor "
         "FROM v_daily r JOIN calc_adjust_factor_daily f USING(thscode,date) "
         "JOIN dim_symbol s ON r.thscode=s.thscode WHERE s.asset_type='a-share' "
         f"AND s.exchange IN ('SH','SZ') AND r.date BETWEEN DATE '{start}' AND DATE '{end}'")
    with tempfile.TemporaryDirectory(prefix='timing-factors-') as tmp:
        fp=Path(tmp)/'factors.parquet'
        proc=subprocess.run(['hithink-finance.cmd','--db',str(ROOT/'data/hithink/market.duckdb'),'db','export','--sql',sql,'--output',str(fp),'--file-format','parquet','--format','json'],capture_output=True,text=True,encoding='utf-8',errors='replace')
        if proc.returncode!=0:raise RuntimeError(proc.stderr or proc.stdout)
        receipt=json.loads(proc.stdout)
        if not receipt.get('ok'):raise RuntimeError(str(receipt))
        factors=pd.read_parquet(fp)
    factors.trade_date=pd.to_datetime(factors.trade_date)
    merged=local.merge(factors,on=['ts_code','trade_date'],how='left',validate='one_to_one')
    invalid=merged.forward_factor.isna()|merged.forward_factor.le(0)|((merged.close-merged.anchor_close).abs()>.011)
    missing=merged.forward_factor.isna();mismatch=(~missing)&((merged.close-merged.anchor_close).abs()>.011)
    latest_excluded=merged.loc[(merged.trade_date==target)&invalid,['ts_code']].ts_code.tolist()
    active=(merged.vol>0)&(merged.amount>0)
    qc=merged.loc[active,['trade_date']].assign(valid=(~invalid[active]).to_numpy()).groupby('trade_date').agg(raw_active=('valid','size'),matched_active=('valid','sum')).reset_index()
    qc['coverage']=qc.matched_active/qc.raw_active
    p=merged.copy()
    # TDX supplies all OHLC and amount/volume; only corporate-action factors come from DB.
    for col in ('open','high','low','close'):
        p[col]=p[col]*p.forward_factor
        p.loc[invalid,col]=np.nan
    p=p.drop(columns=['anchor_close','forward_factor'])
    del local,merged,factors
    features=stock_features(p,cfg);del p
    cap_groups,cap_meta=load_cap_groups()
    daily=aggregate_days(features,cfg,cap_groups);del features
    index_path=Path(args.tdx_dir)/'sh/lday/sh000001.day'
    rec=np.fromfile(index_path,dtype=RECORD_DTYPE)
    index=pd.DataFrame({'trade_date':pd.to_datetime(rec['date'].astype(str),format='%Y%m%d'),'index_close':rec['close']/100.,'index_high':rec['high']/100.,'index_low':rec['low']/100.})
    daily=add_signals(daily,index,qc,cfg)
    # Bottom EMAs warm up on the full 5-year window before the 3-year evaluation trim.
    bcfg=BottomConfig(); daily=add_bottom_signals(daily,bcfg)
    vcfg=VolConfig(); daily=add_volatility(daily,vcfg)
    # Common display/evaluation starts only once 250 prior observations are available.
    daily=daily.loc[(daily.trade_date>=target-pd.DateOffset(years=3))&(daily.hl250_eligible>=daily.eligible*.8)].reset_index(drop=True)
    if daily.empty:raise RuntimeError('Insufficient 250-day history')
    events=event_study(daily,cfg); latest=daily.iloc[-1]
    studies=summarize_events(daily,events); bottom_study=bottom_event_study(daily,bcfg)
    perm_signals={'core_warning':(daily.core_warning,'top'),'confirmed_core':(daily.confirmed_core,'top'),'key_warning':(daily.key_warning,'top')}
    for k in ('oversold','bottom_divergence','bottom_confirmed'):
        perm_signals[k]=(episode_starts(daily[k],bcfg.episode_gap),'bottom')
    permutation=permutation_test(daily,perm_signals)
    cap_check=cap_group_check(daily,daily.core_warning)
    vstudy=vol_study(daily,vcfg); limit_official=load_limit_official(target)
    payload=clean({'asof':end,'generated':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),'config':asdict(cfg),
        'bottom_config':asdict(bcfg),'bottom_study':bottom_study,'permutation':permutation,
        'vol_config':asdict(vcfg),'vol_components':VOL_COMPONENTS,'vol_study':vstudy,'limit_official':limit_official,
        'cap_groups':{'indices':cap_meta,'check':cap_check,'bias':'成分股为当前名单回看全部历史：历史上后来被调入的股票会被计入，存在前视与幸存者偏差'},
        'quality':{'tdx_dir':str(args.tdx_dir),'price_source':'通达信原始OHLC × 同花顺本地公司行动等比因子','amount_source':'通达信.day原始成交额（元）','missing_factor_rows':int(missing.sum()),'raw_price_mismatch_rows':int(mismatch.sum()),'latest_excluded_codes':latest_excluded,'latest_coverage':float(latest.coverage),'g0_verified_at':gate['verified_at'],'g0_scope':'本地运营一致性验证，非独立外部复权证明','history_start':str(daily.trade_date.min().date()),'history_sessions':len(daily),'max_raw_active':int(qc.raw_active.max()),'valuation':'未取得历史估值序列','true_market_cap_groups':'以沪深300/中证1000当前成分股近似大/小市值分组；未取得历史成分、历史市值与指数权重'},
        'latest':latest.to_dict(),'series':daily.tail(args.display_days).to_dict('records'),'events':events,'study':studies,
        'sources':[{'title':'StockCharts 标准TRIN公式','url':'https://chartschool.stockcharts.com/table-of-contents/market-indicators/arms-index-trin'}]})
    tag=end.replace('-',''); jp=out/f'market_timing_{tag}.json'; jp.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    daily.to_csv(out/f'market_timing_daily_{tag}.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(events).to_csv(out/f'market_timing_events_{tag}.csv',index=False,encoding='utf-8-sig')
    from render_market_timing import render
    hp=out/f'a_share_analysis_{tag}_market_timing.html'; render(payload,hp)
    print(json.dumps({'report':str(hp),'bottom_state':payload['latest']['bottom_state'],'latest':payload['latest'],'quality':payload['quality'],'warnings':len(events),'confirmed':sum(e['confirmation']=='confirmed' for e in events)},ensure_ascii=False))

if __name__=='__main__':main()
