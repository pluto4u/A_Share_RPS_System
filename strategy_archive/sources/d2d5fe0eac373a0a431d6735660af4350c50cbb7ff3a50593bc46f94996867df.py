"""Breadth/turnover timing research. Signals use only information known that day."""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class TimingConfig:
    breadth_min: float = .70
    divergence_max: float = .65
    down_intensity_min: float = 1.50
    collapse_pp: float = 25.0
    key_step: float = 500.0
    key_tolerance: float = .01
    min_coverage: float = .95
    ma_periods: tuple = (5, 10, 20, 60, 120, 250)
    high_low_periods: tuple = (20, 60, 250)
    big_move_pct: float = 5.0
    limit_tolerance_pp: float = .3   # a 10% board counts as limit at >=9.7% (tick rounding)

def safe_div(a, b):
    return a / b if pd.notna(a) and pd.notna(b) and b > 0 else np.nan

def stock_features(panel, config=TimingConfig()):
    p=panel.sort_values(['ts_code','trade_date']).reset_index(drop=True).copy()
    if p.duplicated(['ts_code','trade_date']).any():
        raise ValueError('Duplicate stock/date records')
    if (p[['close','high','low']]<=0).any().any():
        raise ValueError('Nonpositive adjusted price')
    g=p.groupby('ts_code',sort=False)
    p['prev_close']=g.close.shift(1)
    p['ret']=p.close/p.prev_close-1
    p['prior_adv20']=g.amount.transform(lambda s:s.shift(1).rolling(20,min_periods=20).mean())
    for n in config.ma_periods:
        p[f'ma{n}']=g.close.transform(lambda s:s.rolling(n,min_periods=n).mean())
    for n in config.high_low_periods:
        # Prior window excludes current bar; ties do not count as breakouts.
        p[f'prior_high{n}']=g.close.transform(lambda s:s.shift(1).rolling(n,min_periods=n).max())
        p[f'prior_low{n}']=g.close.transform(lambda s:s.shift(1).rolling(n,min_periods=n).min())
    return p

def aggregate_days(features, config=TimingConfig(), groups=None):
    """groups: optional {label: iterable of ts_code}; adds <label>_* breadth columns per day."""
    groups={k:list(v) for k,v in (groups or {}).items()}
    records=[]
    for date,p in features.groupby('trade_date',sort=True):
        active=p.loc[(p.amount>0)&(p.vol>0)&p.close.notna()]
        x=active.loc[active.prev_close.notna()]
        # Floating noise on multiplicative qfq sequences must not turn a flat into up/down.
        up=x.close>x.prev_close+1e-8
        down=x.close<x.prev_close-1e-8
        flat=~(up|down)
        a,d,f=int(up.sum()),int(down.sum()),int(flat.sum())
        ua,da,fa=float(x.loc[up,'amount'].sum()),float(x.loc[down,'amount'].sum()),float(x.loc[flat,'amount'].sum())
        total=ua+da+fa
        rn=safe_div(a,d);rv=safe_div(ua,da);div=safe_div(rv,rn)
        r={'trade_date':date,'active':len(active),'eligible':len(x),'new_no_previous':len(active)-len(x),
           'up':a,'down':d,'flat':f,'breadth':safe_div(a,len(x)),
           'breadth_ex_flat':safe_div(a,a+d),'up_amount_yi':ua/1e8,'down_amount_yi':da/1e8,
           'flat_amount_yi':fa/1e8,'amount_yi':total/1e8,'rn':rn,'rv_amount':rv,'divergence':div,
           'trin_amount':safe_div(rn,rv),
           'trin_volume':safe_div(rn,safe_div(float(x.loc[up,'vol'].sum()),float(x.loc[down,'vol'].sum()))),
           'down_count_share':safe_div(d,len(x)),'down_amount_share':safe_div(da,total),
           'down_intensity':safe_div(safe_div(da,total),safe_div(d,len(x))),
           'median_ret_pct':float(x.ret.median()*100) if len(x) else np.nan}
        # Volatility inputs. Limits by board only (ChiNext/STAR 20%, else 10%); ST 5% names are missed.
        ret=x.ret*100
        lim=np.where(x.ts_code.astype(str).str[:2].isin(['30','68']),20.0,10.0)-config.limit_tolerance_pp
        hit=(x.high/x.prev_close-1)*100>=lim
        sealed=ret>=lim
        r['ret_iqr_pct']=float(ret.quantile(.75)-ret.quantile(.25)) if len(x) else np.nan
        r['amp_median_pct']=float(((x.high-x.low)/x.prev_close*100).median()) if len(x) else np.nan
        r['big_up']=int((ret>=config.big_move_pct).sum()); r['big_down']=int((ret<=-config.big_move_pct).sum())
        r['limit_up']=int(sealed.sum()); r['limit_down']=int((ret<=-lim).sum())
        r['limit_hit']=int(hit.sum()); r['limit_broken']=int((hit&~sealed).sum())
        for n in config.ma_periods:
            z=active.loc[active[f'ma{n}'].notna()]
            r[f'ma{n}_eligible']=len(z);r[f'ma{n}_count']=int((z.close>z[f'ma{n}']+1e-8).sum())
            r[f'ma{n}_pct']=safe_div(r[f'ma{n}_count']*100,len(z))
        for n in config.high_low_periods:
            z=active.loc[active[f'prior_high{n}'].notna()]
            r[f'hl{n}_eligible']=len(z)
            r[f'high{n}']=int((z.close>z[f'prior_high{n}']+1e-8).sum())
            r[f'low{n}']=int((z.close<z[f'prior_low{n}']-1e-8).sum())
        # Proxy is prior trading-amount rank, NOT market cap or actual index weight.
        z=x.loc[x.prior_adv20.notna()].copy()
        if len(z):
            z['liquid_group']=z.prior_adv20.rank(method='first',ascending=False)<=max(1,int(np.ceil(len(z)*.2)))
            for label,is_large in [('high_turnover',True),('other_turnover',False)]:
                q=z.loc[z.liquid_group==is_large]
                r[label+'_members']=len(q)
                r[label+'_amount_share']=safe_div(q.amount.sum(),z.amount.sum())
                r[label+'_up_pct']=float((q.close>q.prev_close+1e-8).mean()*100)
                r[label+'_median_ret_pct']=float(q.ret.median()*100)
        for label,members in groups.items():
            q=x.loc[x.ts_code.isin(members)]
            gu=q.close>q.prev_close+1e-8; gd=q.close<q.prev_close-1e-8
            gua,gda=float(q.loc[gu,'amount'].sum()),float(q.loc[gd,'amount'].sum())
            r[label+'_eligible']=len(q)
            r[label+'_breadth']=safe_div(int(gu.sum()),len(q))
            r[label+'_up_amount_share']=safe_div(gua,float(q.amount.sum()))
            r[label+'_divergence']=safe_div(safe_div(gua,gda),safe_div(int(gu.sum()),int(gd.sum())))
            r[label+'_median_ret_pct']=float(q.ret.median()*100) if len(q) else np.nan
            if 'ma20' in active:
                m=active.loc[active.ts_code.isin(members)&active.ma20.notna()]
                r[label+'_ma20_pct']=safe_div(int((m.close>m.ma20+1e-8).sum())*100,len(m))
        assert a+d+f==len(x)
        assert abs((ua+da+fa)-x.amount.sum())<=max(1,total)*1e-10
        if a>0 and d>0 and da>0:
            assert np.isclose(div,(ua/a)/(da/d))
        records.append(r)
    return pd.DataFrame(records)

def add_signals(daily, index, coverage, config=TimingConfig()):
    cols=['trade_date']+[c for c in ('index_close','index_high','index_low') if c in index]
    z=daily.merge(index[cols],on='trade_date',how='left',validate='one_to_one').copy()
    z=z.merge(coverage,on='trade_date',how='left',validate='one_to_one')
    z['quality_ok']=z.coverage.ge(config.min_coverage)
    z['key_level']=np.floor(z.index_close/config.key_step+.5)*config.key_step
    z['key_distance_pct']=(z.index_close/z.key_level-1).abs()*100
    z['near_key']=z.key_distance_pct.le(config.key_tolerance*100)&z.index_close.notna()
    z['surface_strong']=z.breadth.gt(config.breadth_min)
    z['amount_divergent']=z.divergence.lt(config.divergence_max)&z.divergence.notna()
    z['down_heavy']=z.down_intensity.gt(config.down_intensity_min)&z.down_intensity.notna()
    z['core_warning']=z.surface_strong&z.amount_divergent&z.down_heavy&z.quality_ok
    z['key_warning']=z.core_warning&z.near_key
    z['breadth_change_pp']=z.breadth.diff()*100
    z['collapse']=z.breadth_change_pp.lt(-config.collapse_pp)&z.quality_ok
    z['cross_below_half']=z.breadth.lt(.5)&z.breadth.shift(1).ge(.5)
    # Confirmation is emitted on t+1, never backfilled into the warning on t.
    z['confirmed_core']=z.core_warning.shift(1,fill_value=False)&z.collapse
    z['confirmed_key']=z.key_warning.shift(1,fill_value=False)&z.collapse
    z['strong_confirmation']=z.confirmed_key&z.cross_below_half
    z['valuation_filter']='unavailable'
    z['weight_cap_validation']='unavailable'
    z['full_system_authorized']=False
    z['breadth_ema5']=z.breadth.ewm(span=5,adjust=False,min_periods=5).mean()*100
    return z

def event_study(daily, config=TimingConfig()):
    events=[]
    for i in np.flatnonzero(daily.core_warning.to_numpy()):
        row=daily.iloc[i]; nxt=daily.iloc[i+1] if i+1<len(daily) else None
        e={'warning_date':str(row.trade_date.date()),'D':row.divergence,'breadth_pct':row.breadth*100,
           'key_warning':bool(row.key_warning),
           'next_session':str(nxt.trade_date.date()) if nxt is not None else None,
           'confirmation': 'pending' if nxt is None else ('confirmed' if nxt.confirmed_core else 'not_confirmed'),
           'confirmation_known_at':str(nxt.trade_date.date()) if nxt is not None else None}
        # Descriptive close/close outcomes are not executable strategy P&L.
        for horizon in (1,3,5,20,63):
            k=i+horizon
            e[f'warning_close_ret_{horizon}_pct']=(daily.iloc[k].index_close/row.index_close-1)*100 if k<len(daily) and row.index_close>0 else None
            ck=i+1+horizon
            e[f'confirmation_close_ret_{horizon}_pct']=(daily.iloc[ck].index_close/nxt.index_close-1)*100 if nxt is not None and ck<len(daily) and nxt.confirmed_core and nxt.index_close>0 else None
        events.append(e)
    return events

@dataclass(frozen=True)
class BottomConfig:
    ma20_max: float = 15.0          # % of stocks above MA20
    mco_max: float = -60.0          # ratio-adjusted McClellan oscillator (x1000)
    thrust_ema_low: float = .40     # 10-day EMA of up/(up+down)
    thrust_ema_high: float = .615
    low20_share_min: float = .30    # 20-day closing lows / eligible
    oversold_votes: int = 3
    panic_breadth_max: float = .10
    panic_amount_ratio: float = 1.2  # amount / prior 20-day mean
    exhaust_amount_ratio: float = .9
    thrust_window: int = 10
    thrust_day_breadth: float = .80
    episode_gap: int = 10

def add_bottom_signals(daily, config=BottomConfig()):
    """Oversold -> capitulation/exhaustion -> divergence -> thrust. All columns known at that day's close."""
    z=daily.copy()
    a,d=z.up.astype(float),z.down.astype(float)
    ran=(a-d)/(a+d).where(a+d>0)*1000
    z['mco']=ran.ewm(span=19,adjust=False).mean()-ran.ewm(span=39,adjust=False).mean()
    z['thrust_ema']=z.breadth_ex_flat.ewm(span=10,adjust=False).mean()
    z['low20_share']=z.low20/z.hl20_eligible.where(z.hl20_eligible>0)
    z['amount_ratio20']=z.amount_yi/z.amount_yi.shift(1).rolling(20,min_periods=20).mean()
    votes=pd.concat([z.ma20_pct.lt(config.ma20_max),z.mco.lt(config.mco_max),
                     z.thrust_ema.lt(config.thrust_ema_low),z.low20_share.gt(config.low20_share_min)],axis=1)
    z['oversold_votes']=votes.sum(axis=1)
    z['oversold']=z.oversold_votes.ge(config.oversold_votes)
    washout=z.breadth.lt(config.panic_breadth_max)
    z['capitulation']=z.oversold&washout&z.amount_ratio20.ge(config.panic_amount_ratio)
    z['exhaustion']=z.oversold&washout&z.amount_ratio20.lt(config.exhaust_amount_ratio)
    # Index at a fresh 20-day closing low while stock-level damage is smaller than in the prior 20 sessions.
    w=20
    idx_low=z.index_close.lt(z.index_close.shift(1).rolling(w,min_periods=w).min())
    z['bottom_divergence']=(z.oversold.rolling(config.thrust_window,min_periods=1).max().astype(bool)&idx_low
        &z.low20.lt(z.low20.shift(1).rolling(w,min_periods=w).max())
        &z.mco.gt(z.mco.shift(1).rolling(w,min_periods=w).min()))
    recent_oversold=z.oversold.shift(1,fill_value=False).rolling(config.thrust_window,min_periods=1).max().astype(bool)
    recent_low_ema=z.thrust_ema.shift(1).rolling(config.thrust_window,min_periods=1).min().lt(config.thrust_ema_low)
    z['thrust_day']=recent_oversold&z.breadth.gt(config.thrust_day_breadth)
    z['zweig_thrust']=recent_low_ema&z.thrust_ema.gt(config.thrust_ema_high)
    z['ma20_recover']=recent_oversold&z.ma20_pct.ge(50)&z.ma20_pct.shift(1).lt(50)
    z['bottom_confirmed']=z.thrust_day|z.zweig_thrust|z.ma20_recover
    z['bottom_state']=np.select([z.bottom_confirmed,z.capitulation,z.exhaustion,z.bottom_divergence,z.oversold],
        ['推力确认','超卖+放量投降','超卖+缩量衰竭','超卖+背离','超卖'],'正常')
    return z

def episode_starts(mask, gap):
    """True on a signal day with no signal in the prior `gap` sessions."""
    s=np.asarray(mask,bool)
    prior=pd.Series(s).shift(1,fill_value=False).rolling(gap,min_periods=1).max().astype(bool).to_numpy()
    return s&~prior

def bottom_event_study(daily, config=BottomConfig()):
    """First day of each episode only (no same signal in the prior `episode_gap` sessions)."""
    z=daily.reset_index(drop=True)
    ew=(1+z.median_ret_pct.fillna(0)/100).cumprod()
    out=[]
    for kind in ('oversold','capitulation','exhaustion','bottom_divergence','bottom_confirmed','baseline'):
        if kind=='baseline':
            starts=np.arange(len(z))
        else:
            starts=np.flatnonzero(episode_starts(z[kind],config.episode_gap))
        for n in (5,20,63):
            ok=starts[starts+n<len(z)]
            ix=(z.index_close.to_numpy()[ok+n]/z.index_close.to_numpy()[ok]-1)*100
            eq=(ew.to_numpy()[ok+n]/ew.to_numpy()[ok]-1)*100
            out.append({'signal':kind,'horizon':n,'episodes':int(len(starts)),'observations':int(len(ok)),
                'index_median_pct':float(np.median(ix)) if len(ok) else None,
                'index_up_pct':float(np.mean(ix>0)*100) if len(ok) else None,
                'ew_median_pct':float(np.median(eq)) if len(ok) else None,
                'ew_up_pct':float(np.mean(eq>0)*100) if len(ok) else None,
                'dates':[str(pd.Timestamp(z.trade_date.iloc[i]).date()) for i in starts] if kind!='baseline' and n==5 else None})
    return out

def cap_group_check(daily, mask, large='csi300', small='csi1000', gap_pp=10.0):
    """Blogger's claim on warning days: large caps lag while small caps rally. Compare against all days."""
    z=daily.reset_index(drop=True)
    if large+'_breadth' not in z or small+'_breadth' not in z:
        return None
    spread=(z[small+'_breadth']-z[large+'_breadth'])*100
    def summary(sel):
        q=z.loc[sel]; sp=spread[sel].dropna()
        return {'days':int(sel.sum()),
            large+'_breadth_median':float(q[large+'_breadth'].median()*100) if len(q) else None,
            small+'_breadth_median':float(q[small+'_breadth'].median()*100) if len(q) else None,
            large+'_up_amount_share_median':float(q[large+'_up_amount_share'].median()*100) if len(q) else None,
            'spread_pp_median':float(sp.median()) if len(sp) else None,
            'large_lags_pct':float((sp>gap_pp).mean()*100) if len(sp) else None}
    sel=pd.Series(np.asarray(mask,bool),index=z.index)
    return {'gap_pp':gap_pp,'signal_days':summary(sel),'all_days':summary(pd.Series(True,index=z.index))}

def permutation_test(daily, signals, horizons=(5,20)):
    """Circular-shift test. Shifting the signal-day vector keeps its clustering but breaks its alignment
    with future returns; p is the share of shifts at least as extreme as the real alignment.
    signals: {name: (bool mask, 'top'|'bottom')}; top expects lower forward returns, bottom higher."""
    z=daily.reset_index(drop=True); n=len(z)
    idx=z.index_close.to_numpy(float)
    ew=np.cumprod(1+np.nan_to_num(z.median_ret_pct.to_numpy(float))/100)
    out=[]
    for h in horizons:
        if n<=3*h:
            continue
        fwd={'index':(idx[h:]/idx[:-h]-1)*100,'ew':(ew[h:]/ew[:-h]-1)*100}
        m=n-h
        shifts=range(h,m-h+1)  # skip near-zero shifts that overlap the real windows
        for name,(mask,side) in signals.items():
            s=np.asarray(mask,bool)[:m]
            k=int(s.sum())
            row={'signal':name,'side':side,'horizon':h,'observations':k,'shifts':len(shifts)}
            if k==0:
                out.append(row); continue
            more_extreme=(lambda null,obs:null<=obs) if side=='top' else (lambda null,obs:null>=obs)
            for key,f in fwd.items():
                obs=float(np.median(f[s]))
                null=np.array([np.median(f[np.roll(s,sh)]) for sh in shifts])
                row[key+'_median_pct']=obs
                row[key+'_null_median_pct']=float(np.median(null))
                row[key+'_p']=float(more_extreme(null,obs).mean())
            up=float((fwd['index'][s]>0).mean()*100)
            null_up=np.array([(fwd['index'][np.roll(s,sh)]>0).mean()*100 for sh in shifts])
            row['index_up_pct']=up
            row['index_null_up_pct']=float(np.median(null_up))
            row['index_up_p']=float(more_extreme(null_up,up).mean())
            out.append(row)
    return out

@dataclass(frozen=True)
class VolConfig:
    window: int = 20          # realized-vol window
    smooth: int = 5           # smoothing for noisy daily cross-section counts
    flip_window: int = 10
    pct_window: int = 750     # ~3 years of sessions for the percentile reference
    pct_min: int = 250
    cool: float = 30.0
    hot: float = 70.0
    extreme: float = 90.0
    whipsaw_high: float = .80
    whipsaw_low: float = .20
    horizon: int = 20         # forward window used by the study

VOL_COMPONENTS={'rv20':'上证20日实现波动','park20':'上证20日高低价波动','dispersion':'个股涨跌幅离散度（IQR）',
    'flip10':'宽度翻转（日变化均值）','big_move_share':'大涨大跌家数占比','limit_share':'涨跌停家数占比',
    'broken_rate':'炸板率','amount_ratio':'成交额/20日均量'}

def trailing_percentile(s, window, min_periods):
    """Percentile of today's value within the trailing window (today included, ties count half). No look-ahead."""
    def pct(a):
        past=a[:-1]; past=past[np.isfinite(past)]
        if not np.isfinite(a[-1]) or len(past)==0:
            return np.nan
        return ((past<a[-1]).sum()+.5*(past==a[-1]).sum())/len(past)*100
    return s.rolling(window,min_periods=min_periods).apply(pct,raw=True)

def add_volatility(daily, config=VolConfig()):
    z=daily.copy(); w,sm=config.window,config.smooth
    lr=np.log(z.index_close).diff()
    z['rv20']=lr.rolling(w,min_periods=w).std()*np.sqrt(252)*100
    if {'index_high','index_low'}<=set(z.columns):
        hl=np.log(z.index_high/z.index_low)**2
        z['park20']=np.sqrt(hl.rolling(w,min_periods=w).mean()/(4*np.log(2)))*np.sqrt(252)*100
    else:
        z['park20']=np.nan
    z['dispersion']=z.ret_iqr_pct.rolling(sm,min_periods=sm).mean()
    z['flip10']=z.breadth.diff().abs().rolling(config.flip_window,min_periods=config.flip_window).mean()*100
    z['big_move_share']=((z.big_up+z.big_down)/z.eligible*100).rolling(sm,min_periods=sm).mean()
    z['limit_share']=((z.limit_up+z.limit_down)/z.eligible*100).rolling(sm,min_periods=sm).mean()
    hits=z.limit_hit.rolling(sm,min_periods=sm).sum()
    z['broken_rate']=z.limit_broken.rolling(sm,min_periods=sm).sum()/hits.where(hits>0)*100
    z['amount_ratio']=z.amount_yi/z.amount_yi.shift(1).rolling(w,min_periods=w).mean()
    for c in VOL_COMPONENTS:
        z[c+'_pctl']=trailing_percentile(z[c],config.pct_window,config.pct_min)
    p=z[[c+'_pctl' for c in VOL_COMPONENTS]]
    z['vol_temp']=p.mean(axis=1).where(p.notna().sum(axis=1)>=len(VOL_COMPONENTS)-2)
    z['vol_state']=np.select([z.vol_temp.isna(),z.vol_temp.ge(config.extreme),z.vol_temp.ge(config.hot),z.vol_temp.ge(config.cool)],
                             ['未取得','极端','高','中'],'低')
    z['vol_direction']=np.where(z.index_close/z.index_close.shift(10)-1>=0,'上涨中','下跌中')
    z['whipsaw']=z.breadth.rolling(5,min_periods=5).max().gt(config.whipsaw_high)&z.breadth.rolling(5,min_periods=5).min().lt(config.whipsaw_low)
    return z

def shift_corr_test(x, y, min_shift, sign=1):
    """Spearman corr of x vs y; null = circularly shifted x (keeps x's autocorrelation).
    sign=+1 tests 'higher x -> higher y', -1 the reverse. Returns obs, null median, one-sided p."""
    m=np.isfinite(x)&np.isfinite(y)
    rx=pd.Series(x[m]).rank().to_numpy(); ry=pd.Series(y[m]).rank().to_numpy(); n=len(rx)
    if n<=3*min_shift:
        return {'n':int(n),'corr':None,'null_corr':None,'p':None}
    corr=lambda a:float(np.corrcoef(a,ry)[0,1])
    obs=corr(rx); null=np.array([corr(np.roll(rx,s)) for s in range(min_shift,n-min_shift+1)])
    p=float((sign*null>=sign*obs).mean())
    return {'n':int(n),'corr':obs,'null_corr':float(np.median(null)),'p':p}

def vol_study(daily, config=VolConfig()):
    """Does the temperature anticipate future volatility / drawdowns (not direction)?"""
    z=daily.reset_index(drop=True); h=config.horizon
    lr=np.log(z.index_close).diff()
    fwd_rv=(lr.rolling(h,min_periods=h).std()*np.sqrt(252)*100).shift(-h)
    fwd_ret=(z.index_close.shift(-h)/z.index_close-1)*100
    fut_min=z.index_close[::-1].rolling(h,min_periods=h).min()[::-1].shift(-1)
    fwd_dd=(fut_min/z.index_close-1).clip(upper=0)*100
    buckets=[]
    for state in ('低','中','高','极端'):
        sel=z.vol_state.eq(state)&fwd_rv.notna()
        buckets.append({'state':state,'days':int(sel.sum()),'temp_median':float(z.vol_temp[sel].median()) if sel.any() else None,
            'rv20_now_median':float(z.rv20[sel].median()) if sel.any() else None,
            'fwd_rv_median':float(fwd_rv[sel].median()) if sel.any() else None,
            'fwd_ret_median_pct':float(fwd_ret[sel].median()) if sel.any() else None,
            'fwd_up_pct':float((fwd_ret[sel]>0).mean()*100) if sel.any() else None,
            'fwd_dd_median_pct':float(fwd_dd[sel].median()) if sel.any() else None,
            'fwd_dd_over5_pct':float((fwd_dd[sel]<-5).mean()*100) if sel.any() else None})
    t=z.vol_temp.to_numpy(float); rv=z.rv20.to_numpy(float)
    tests={'temp_vs_fwd_rv':shift_corr_test(t,fwd_rv.to_numpy(float),h,1),
           'rv20_vs_fwd_rv':shift_corr_test(rv,fwd_rv.to_numpy(float),h,1),
           'temp_vs_fwd_dd':shift_corr_test(t,fwd_dd.to_numpy(float),h,-1),
           'temp_vs_fwd_ret':shift_corr_test(t,fwd_ret.to_numpy(float),h,1)}
    # Incremental value: rank-residualize temperature and future vol on current rv20, then test the residuals.
    f=fwd_rv.to_numpy(float); m=np.isfinite(t)&np.isfinite(rv)&np.isfinite(f)
    rt,rr,rf=(pd.Series(a[m]).rank().to_numpy() for a in (t,rv,f))
    X=np.column_stack([np.ones(m.sum()),rr])
    res=lambda y:y-X@np.linalg.lstsq(X,y,rcond=None)[0]
    et,ef=np.full(len(t),np.nan),np.full(len(t),np.nan); et[m]=res(rt); ef[m]=res(rf)
    tests['temp_vs_fwd_rv_given_rv20']=shift_corr_test(et,ef,h,1)
    return {'horizon':h,'buckets':buckets,'tests':tests}

def summarize_events(daily, events):
    studies=[]
    for kind,predicate in [('core_warning',lambda e:True),('key_warning',lambda e:e['key_warning']),
                           ('confirmed_core',lambda e:e['confirmation']=='confirmed'),
                           ('confirmed_key',lambda e:e['confirmation']=='confirmed' and e['key_warning'])]:
        subset=[e for e in events if predicate(e)]
        for n in (1,3,5,20,63):
            key=('confirmation_close_ret_' if kind.startswith('confirmed') else 'warning_close_ret_')+str(n)+'_pct'
            values=[e[key] for e in subset if e[key] is not None and np.isfinite(e[key])]
            studies.append({'signal':kind,'horizon':n,'observations':len(values),'median_return_pct':float(np.median(values)) if values else None,'down_frequency_pct':float(np.mean(np.array(values)<0)*100) if values else None})
    for n in (1,3,5,20,63):
        values=((daily.index_close.shift(-n)/daily.index_close-1)*100).dropna()
        studies.append({'signal':'baseline','horizon':n,'observations':len(values),'median_return_pct':float(values.median()) if len(values) else None,'down_frequency_pct':float((values<0).mean()*100) if len(values) else None})
    return studies
