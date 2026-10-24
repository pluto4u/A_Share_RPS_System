import unittest
import numpy as np
import pandas as pd
from rps_analyzer.market_timing import TimingConfig, stock_features, aggregate_days, add_signals, event_study, summarize_events, add_bottom_signals, permutation_test, cap_group_check, add_volatility, shift_corr_test, VolConfig

class VolatilityTests(unittest.TestCase):
    def test_limit_counts_follow_board_rules(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(1,))
        # (code, close, high): ChiNext sealed +20%, main sealed +10%, main touched +10% then closed +5% (broken),
        # ChiNext +10% (not a limit for a 20% board), main -10% limit down.
        spec=[('300001.SZ',12.0,12.0),('600001.SH',11.0,11.0),('600002.SH',10.5,11.0),('300002.SZ',11.0,11.0),('600003.SH',9.0,10.0)]
        rows=[]
        for code,c,h in spec:
            rows.append(dict(ts_code=code,trade_date=pd.Timestamp('2026-01-05'),close=10.,high=10.,low=10.,amount=1,vol=1))
            rows.append(dict(ts_code=code,trade_date=pd.Timestamp('2026-01-06'),close=c,high=h,low=min(c,10.),amount=1,vol=1))
        z=aggregate_days(stock_features(pd.DataFrame(rows),cfg),cfg).iloc[-1]
        self.assertEqual(z.limit_up,2); self.assertEqual(z.limit_down,1)
        self.assertEqual(z.limit_hit,3); self.assertEqual(z.limit_broken,1)
        self.assertEqual(z.big_up,4); self.assertEqual(z.big_down,1)

    def daily(self, n=320, seed=2):
        rng=np.random.default_rng(seed); r=rng.normal(0,1,n)
        close=3000*np.cumprod(1+r/100); b=rng.uniform(.05,.95,n)
        return pd.DataFrame({'trade_date':pd.bdate_range('2025-01-01',periods=n),'index_close':close,'index_high':close*1.01,'index_low':close*.99,
            'breadth':b,'ret_iqr_pct':rng.uniform(1,4,n),'big_up':rng.integers(0,300,n),'big_down':rng.integers(0,300,n),
            'limit_up':rng.integers(0,100,n),'limit_down':rng.integers(0,50,n),'limit_hit':rng.integers(50,150,n),'limit_broken':rng.integers(0,40,n),
            'eligible':5000,'amount_yi':rng.uniform(8000,20000,n)})

    def test_temperature_uses_no_future_rows(self):
        cfg=VolConfig(pct_window=200,pct_min=60)
        full=add_volatility(self.daily(),cfg); cut=add_volatility(self.daily().iloc[:250],cfg)
        cols=['rv20','park20','dispersion','flip10','limit_share','broken_rate','vol_temp','vol_state','whipsaw']
        pd.testing.assert_frame_equal(full[cols].iloc[:250],cut[cols])
        self.assertTrue(full.vol_temp.dropna().between(0,100).all())

    def test_shift_corr_detects_relation(self):
        rng=np.random.default_rng(3); x=np.cumsum(rng.normal(size=500)); y=x+rng.normal(size=500)*3
        self.assertLess(shift_corr_test(x,y,20,1)['p'],.05)
        self.assertGreater(shift_corr_test(x,rng.normal(size=500),20,1)['p'],.05)

class GroupAndPermutationTests(unittest.TestCase):
    def test_group_breadth_uses_only_members(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(1,))
        rows=[]
        for i,c in enumerate([11,11,9,9,9]):
            for date,price in [('2026-01-05',10),('2026-01-06',c)]:
                rows.append(dict(ts_code=str(i),trade_date=pd.Timestamp(date),close=price,high=price,low=price,amount=10,vol=1))
        z=aggregate_days(stock_features(pd.DataFrame(rows),cfg),cfg,{'big':['0','1'],'small':['2','3','4']}).iloc[-1]
        self.assertEqual(z.big_eligible,2); self.assertAlmostEqual(z.big_breadth,1.0)
        self.assertAlmostEqual(z.small_breadth,0.0); self.assertAlmostEqual(z.breadth,.4)
        self.assertTrue(pd.isna(z.big_divergence))  # no decliners in the group

    def daily(self, n=400, seed=1):
        rng=np.random.default_rng(seed)
        ret=rng.normal(0,1,n); signal=np.zeros(n,bool); signal[rng.choice(np.arange(20,n-30),15,replace=False)]=True
        return ret,signal

    def test_permutation_detects_real_edge_and_not_noise(self):
        ret,signal=self.daily()
        drop=ret.copy()
        for i in np.flatnonzero(signal): drop[i+1:i+6]-=3   # market falls after each top signal
        mk=lambda r:pd.DataFrame({'index_close':100*np.cumprod(1+r/100),'median_ret_pct':r})
        edge=[x for x in permutation_test(mk(drop),{'s':(signal,'top')},horizons=(5,)) if x['signal']=='s'][0]
        noise=[x for x in permutation_test(mk(ret),{'s':(signal,'top')},horizons=(5,)) if x['signal']=='s'][0]
        self.assertLess(edge['index_p'],.01); self.assertGreater(noise['index_p'],.05)
        self.assertEqual(edge['observations'],15)

    def test_cap_check_absent_without_groups(self):
        self.assertIsNone(cap_group_check(pd.DataFrame({'breadth':[.5]}),[True]))

class BottomTests(unittest.TestCase):
    def daily(self, n=80):
        rng=np.random.default_rng(0)
        up=rng.integers(200,4800,n); down=5000-up
        return pd.DataFrame({'trade_date':pd.bdate_range('2026-01-01',periods=n),'up':up,'down':down,
            'breadth':up/5000,'breadth_ex_flat':up/5000,'ma20_pct':rng.uniform(5,95,n),'low20':rng.integers(0,3000,n),
            'hl20_eligible':5000,'amount_yi':rng.uniform(8000,20000,n),'index_close':3000+rng.normal(0,30,n).cumsum(),
            'median_ret_pct':rng.normal(0,1.5,n)})

    def test_bottom_signals_use_no_future_rows(self):
        full=add_bottom_signals(self.daily()); cut=add_bottom_signals(self.daily().iloc[:60])
        cols=['mco','thrust_ema','oversold','capitulation','exhaustion','bottom_divergence','bottom_confirmed','bottom_state']
        pd.testing.assert_frame_equal(full[cols].iloc[:60],cut[cols])

    def test_capitulation_requires_heavy_amount(self):
        z=add_bottom_signals(self.daily())
        self.assertFalse((z.capitulation&z.amount_ratio20.lt(1.2)).any())
        self.assertFalse((z.capitulation&z.exhaustion).any())

class TimingTests(unittest.TestCase):
    def panel(self, up=80, down=20, flat=0):
        rows=[]
        for i in range(up+down+flat):
            c=11 if i<up else 9 if i<up+down else 10
            amount=169/up if i<up and up else 100/down if i<up+down and down else 1
            for date,price,money in [('2026-01-05',10,1),('2026-01-06',c,amount)]:
                rows.append(dict(ts_code=str(i),trade_date=pd.Timestamp(date),close=price,high=price,low=price,amount=money,vol=10))
        return pd.DataFrame(rows)

    def test_amount_ratio_identity_and_flat_denominator(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(1,))
        z=aggregate_days(stock_features(self.panel(80,20,5),cfg),cfg).iloc[-1]
        self.assertEqual(z.eligible,105)
        self.assertAlmostEqual(z.breadth,80/105)
        self.assertAlmostEqual(z.rn,4)
        self.assertAlmostEqual(z.rv_amount,1.69)
        self.assertAlmostEqual(z.divergence,1.69/4)
        self.assertAlmostEqual(z.divergence*z.trin_amount,1)
        self.assertEqual(z.flat,5)

    def test_zero_denominator_returns_missing(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(1,))
        z=aggregate_days(stock_features(self.panel(10,0,0),cfg),cfg).iloc[-1]
        self.assertTrue(pd.isna(z.rn))
        self.assertTrue(pd.isna(z.divergence))

    def test_strict_prior_window_excludes_current_and_ties(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(2,))
        p=pd.DataFrame([dict(ts_code='A',trade_date=pd.Timestamp('2026-01-01')+pd.Timedelta(days=i),close=c,high=c,low=c,amount=1,vol=1) for i,c in enumerate([10,11,11,12,9])])
        f=stock_features(p,cfg); z=aggregate_days(f,cfg)
        self.assertEqual(z.iloc[2].high2,0)
        self.assertEqual(z.iloc[3].high2,1)
        self.assertEqual(z.iloc[4].low2,1)
        self.assertEqual(z.iloc[0].ma2_eligible,0)

    def test_bad_price_gap_breaks_windows(self):
        cfg=TimingConfig(ma_periods=(2,),high_low_periods=(2,))
        p=pd.DataFrame([dict(ts_code='A',trade_date=pd.Timestamp('2026-01-01')+pd.Timedelta(days=i),close=c,high=c,low=c,amount=1,vol=1) for i,c in enumerate([10,np.nan,12,13])])
        z=stock_features(p,cfg)
        self.assertTrue(pd.isna(z.iloc[2].ma2))
        self.assertTrue(pd.isna(z.iloc[3].prior_high2))
        self.assertFalse(pd.isna(z.iloc[3].ma2))

    def sample(self):
        dates=pd.date_range('2026-01-05',periods=3)
        d=pd.DataFrame({'trade_date':dates,'breadth':[.8,.4,.6],'divergence':[.4,.8,1.2],'down_intensity':[2.,1.,1.]})
        ix=pd.DataFrame({'trade_date':dates,'index_close':[4000,3900,3950]})
        cov=pd.DataFrame({'trade_date':dates,'coverage':[1.,1.,1.]})
        return d,ix,cov

    def test_confirmation_has_no_lookahead(self):
        d,ix,cov=self.sample()
        full=add_signals(d,ix,cov)
        prefix=add_signals(d.iloc[:1],ix,cov)
        self.assertTrue(full.iloc[0].key_warning)
        self.assertFalse(full.iloc[0].confirmed_core)
        self.assertTrue(full.iloc[1].confirmed_core)
        self.assertEqual(prefix.iloc[0].core_warning,full.iloc[0].core_warning)
        event=event_study(prefix)[0]
        self.assertEqual(event['confirmation'],'pending')
        self.assertIsNone(event['warning_close_ret_1_pct'])
        self.assertTrue((~full.full_system_authorized).all())

    def test_coverage_blocks_warning_and_confirmation(self):
        d,ix,cov=self.sample();cov.loc[0,'coverage']=.8
        z=add_signals(d,ix,cov)
        self.assertFalse(z.iloc[0].core_warning)
        self.assertFalse(z.iloc[1].confirmed_core)

    def test_summary_separates_confirmation_date_and_pending(self):
        d,ix,cov=self.sample();z=add_signals(d,ix,cov)
        stats=summarize_events(z,event_study(z))
        confirmed=next(x for x in stats if x['signal']=='confirmed_key' and x['horizon']==1)
        self.assertEqual(confirmed['observations'],1)
        self.assertAlmostEqual(confirmed['median_return_pct'],(3950/3900-1)*100)
        pending=z.iloc[:1];stats=summarize_events(pending,event_study(pending))
        confirmed=next(x for x in stats if x['signal']=='confirmed_key' and x['horizon']==1)
        self.assertEqual(confirmed['observations'],0)
        self.assertIsNone(confirmed['median_return_pct'])

if __name__=='__main__':unittest.main()
