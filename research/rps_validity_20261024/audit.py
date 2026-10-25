from project_paths import script_file
"""Read-only inputs; write only isolated diagnostic results. No production repair."""
from __future__ import annotations
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from rps_analyzer.price_adjust import adjust_one, load_gbbq
from rps_analyzer.tdx_reader import _is_a_share, read_day_file

HERE = Path(__file__).resolve().parent
OUT = ROOT / "outputs/diagnostics/rps_validity_20261024"
DTYPE = np.dtype([("date", "<u4"), ("open", "<u4"), ("high", "<u4"),
                  ("low", "<u4"), ("close", "<u4"), ("amount", "<f4"),
                  ("vol", "<u4"), ("reserved", "<u4")])


def summarize_delta(a, b):
    d = (a - b).abs().dropna()
    return {"pairs": len(d), "max": float(d.max()) if len(d) else None,
            "median": float(d.median()) if len(d) else None,
            "gt_1": int((d > 1).sum()), "gt_5": int((d > 5).sum())}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dbpath = ROOT / "data/hithink/market.duckdb"
    con = duckdb.connect(str(dbpath), read_only=True)
    con.execute("SET enable_progress_bar=false")
    results = {"audited_at": datetime.now().astimezone().isoformat(),
               "scope": "沪深A股; current cross-section; no historical unbiased-universe approval"}
    stats = con.execute("SELECT min(date),max(date),count(*),count(distinct thscode) FROM v_daily").fetchone()
    results["db"] = dict(zip(["min_date", "max_date", "rows", "symbols"], map(str, stats)))
    results["db_views"] = con.execute("SELECT view_name,sql FROM duckdb_views() WHERE view_name IN ('v_daily','v_daily_qfq')").fetchall()
    results["factor_schema"] = con.execute("DESCRIBE calc_adjust_factor_daily").fetchall()
    results["db_quality"] = con.execute("""SELECT count(*) n,
        count(*) FILTER(WHERE close<=0 OR low<=0 OR open<=0 OR high<=0) bad_price,
        count(*) FILTER(WHERE high<greatest(open,close,low) OR low>least(open,close,high)) bad_range,
        count(*) FILTER(WHERE volume<0 OR amount<0) negative_liquidity
        FROM v_daily""").fetchdf().to_dict("records")[0]
    results["db_factor"] = con.execute("""SELECT count(*) n,
        count(*) FILTER(WHERE forward_factor IS NULL OR NOT isfinite(forward_factor) OR forward_factor<=0) bad,
        min(forward_factor) min_factor,max(forward_factor) max_factor FROM calc_adjust_factor_daily""").fetchdf().to_dict("records")[0]
    results["db_formula"] = con.execute("""SELECT count(*) joined,
        count(*) FILTER(WHERE abs(q.close-r.close*f.forward_factor)>1e-8) close_mismatch,
        count(*) FILTER(WHERE abs(q.volume-r.volume)>1e-6 OR abs(q.amount-r.amount)>0.01) liquidity_mismatch
        FROM v_daily r JOIN v_daily_qfq q USING(thscode,date)
        JOIN calc_adjust_factor_daily f USING(thscode,date)""").fetchdf().to_dict("records")[0]
    print("DB profile complete", flush=True)
    events = load_gbbq(Path("C:/new_tdx/T0002/hq_cache/gbbq"))
    profile, tails, returns = [], {}, []
    for market in ("sh", "sz"):
        for path in sorted(Path(f"C:/new_tdx/vipdoc/{market}/lday").glob("*.day")):
            if not _is_a_share(path.name, market):
                continue
            rec = np.fromfile(path, dtype=DTYPE)
            code = path.stem[2:] + "." + market.upper()
            row = {"ts_code": code, "bars": len(rec), "bytes_remainder": path.stat().st_size % 32,
                   "latest": int(rec['date'][-1]) if len(rec) else None,
                   "duplicate_or_unsorted": int((np.diff(rec['date'].astype('int64')) <= 0).sum()),
                   "nonpositive_ohlc": int(((rec['open']==0)|(rec['high']==0)|(rec['low']==0)|(rec['close']==0)).sum()),
                   "invalid_range": int(((rec['high']<np.maximum(rec['open'],rec['close'])) | (rec['low']>np.minimum(rec['open'],rec['close'])) | (rec['low']>rec['high'])).sum()),
                   "invalid_liquidity": int((~np.isfinite(rec['amount']) | (rec['amount']<0)).sum())}
            profile.append(row)
            if len(rec) < 251:
                continue
            d = read_day_file(str(path)).tail(320).copy()
            tails[code] = d
            if d.trade_date.iloc[-1] != pd.Timestamp(stats[1]):
                continue
            r = {"ts_code": code}
            try:
                q, applied = adjust_one(d, events.get(code), d.trade_date.iloc[-1])
                r["events"] = applied
                for n in (50, 120, 250):
                    r[f"raw{n}"] = d.close.iloc[-1] / d.close.iloc[-1-n] - 1
                    r[f"tdxq{n}"] = q.close.iloc[-1] / q.close.iloc[-1-n] - 1
            except ValueError as exc:
                r["excluded"] = str(exc)
            returns.append(r)
    pd.DataFrame(profile).to_json(OUT / "tdx_file_profile.json", orient="records", force_ascii=False, indent=2)
    results["tdx"] = {"files": len(profile), "rows": sum(r['bars'] for r in profile),
        "latest_distribution": dict(Counter(str(r['latest']) for r in profile)),
        "bars_ge251": sum(r['bars']>=251 for r in profile),
        "issues": {k:sum(r[k] for r in profile) for k in ('bytes_remainder','duplicate_or_unsorted','nonpositive_ohlc','invalid_range','invalid_liquidity')}}
    ret = pd.DataFrame(returns).set_index("ts_code")
    results["tdx_adjustment_exclusions"] = ret.get("excluded", pd.Series(dtype=str)).dropna().to_dict()
    print(f"TDX profile complete: {len(profile)} files", flush=True)
    # Current RPS endpoint returns use each stock's last traded bars, like production shift(n).
    hs = "(substr(thscode,1,3) IN ('600','601','603','605','688','689','000','001','002','003','300','301')) AND right(thscode,2) IN ('SH','SZ')"
    dbtail = con.execute(f"""WITH x AS (SELECT *, row_number() OVER(PARTITION BY thscode ORDER BY date DESC) rn
        FROM v_daily_qfq WHERE {hs}) SELECT * FROM x WHERE rn<=251 ORDER BY thscode,date""").fetchdf()
    dbret = []
    for code, d in dbtail.groupby("thscode"):
        if len(d)<251 or pd.Timestamp(d.date.iloc[-1])!=pd.Timestamp(stats[1]):
            continue
        dbret.append({"ts_code":code, **{f"dbq{n}":d.close.iloc[-1]/d.close.iloc[-1-n]-1 for n in (50,120,250)}})
    ret = ret.join(pd.DataFrame(dbret).set_index('ts_code'), how='outer')
    results["current_rps"] = {}
    for n in (50,120,250):
        cols = [f"raw{n}",f"tdxq{n}",f"dbq{n}"]
        shared = ret[cols].dropna()
        ranks = shared.rank(pct=True)*100
        results["current_rps"][str(n)] = {
            "common_universe":len(shared),
            "raw_vs_tdx_qfq_rank_delta":summarize_delta(ranks[cols[0]],ranks[cols[1]]),
            "db_qfq_vs_tdx_qfq_rank_delta":summarize_delta(ranks[cols[2]],ranks[cols[1]]),
            "raw_vs_qfq_top10_overlap":len(set(ranks.index[ranks[cols[0]]>=90])&set(ranks.index[ranks[cols[1]]>=90])),
            "qfq_top10_size":int((ranks[cols[1]]>=90).sum())}
    ret.to_json(OUT / "current_endpoint_returns.json",orient='index',indent=2)
    # Sample source agreement across all 320 historical bars; explicit seed, plus targeted names.
    codes = sorted(tails)
    picks = sorted(set(np.random.default_rng(20261024).choice(codes,64,replace=False)) | {'000001.SZ','300033.SZ','600519.SH'})
    rawdb = con.execute("SELECT * FROM v_daily WHERE thscode IN (SELECT unnest(?))",[picks]).fetchdf()
    rawdb['trade_date']=pd.to_datetime(rawdb.date)
    diffs=[]
    for code in picks:
        if code not in tails: continue
        m=tails[code].merge(rawdb.loc[rawdb.thscode==code],on='trade_date',suffixes=('_tdx','_db'))
        if m.empty: continue
        price=np.column_stack([(m[f'{f}_tdx']-m[f'{f}_db']).abs() for f in ('open','high','low','close')])
        diffs.append({'code':code,'rows':len(m),'ohlc_mismatch_gt_0011':int((price>.011).any(axis=1).sum()),
                      'max_ohlc_diff':float(price.max()),
                      'volume_mismatch_gt_1pct':int(((m.vol-m.volume).abs()>np.maximum(100,m.vol*.01)).sum()),
                      'amount_mismatch_gt_1pct':int(((m.amount_tdx-m.amount_db).abs()>np.maximum(100,m.amount_tdx*.01)).sum())})
    results['raw_cross_source_sample']={'symbols':len(diffs),'rows':sum(r['rows'] for r in diffs),
       **{k:sum(r[k] for r in diffs) for k in ('ohlc_mismatch_gt_0011','volume_mismatch_gt_1pct','amount_mismatch_gt_1pct')},'details':diffs}
    # Inspect actual named cache rather than trusting filenames or stale manifests.
    cache=ROOT/'output/tdx_stock_daily_with_rps.parquet'
    results['cache_profile']=con.execute("SELECT min(trade_date) start,max(trade_date) latest,count(*) rows,count(distinct ts_code) symbols FROM read_parquet(?)",[str(cache)]).fetchdf().to_dict('records')[0]
    cachedata=con.execute("DESCRIBE SELECT * FROM read_parquet(?)",[str(cache)]).fetchall()
    results['cache_schema']=cachedata
    results['cache_duplicates']=con.execute("SELECT count(*)-count(distinct (ts_code,trade_date)) FROM read_parquet(?)",[str(cache)]).fetchone()[0]
    # Supplier remote display-qfq versus local multiplicative-qfq: same three codes and dates.
    remote=json.loads((HERE/'remote_samples.json').read_text(encoding='utf-8'))
    rem={}
    for payload in remote:
        if payload.get('code') != 0: continue
        data=payload['data']; d=pd.DataFrame(data['item'])
        d['trade_date']=pd.to_datetime(d.date_ms,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
        rem[(data['thscode'],data['adjust'])]=d
    checks=[]
    for code in ('000001.SZ','300033.SZ','600519.SH'):
        raw=rem[(code,'none')]; qfq=rem[(code,'forward')]
        m=raw.merge(qfq,on='trade_date',suffixes=('_raw','_qfq')).merge(tails[code],on='trade_date')
        db=con.execute('SELECT date,close FROM v_daily_qfq WHERE thscode=?',[code]).fetchdf()
        db['trade_date']=pd.to_datetime(db.date)
        m=m.merge(db.rename(columns={'close':'dbq'}),on='trade_date')
        tq,_=adjust_one(tails[code],events.get(code),pd.Timestamp(stats[1]))
        m=m.merge(tq[['trade_date','close']].rename(columns={'close':'tdxq'}),on='trade_date')
        cols=['trade_date','close_price_raw','close_price_qfq','close','dbq','tdxq']
        m[cols].to_json(OUT/f'remote_{code}.json',orient='records',date_format='iso',indent=2)
        checks.append({'code':code,'rows':len(m),'raw_vs_tdx_max_abs':float((m.close_price_raw-m.close).abs().max()),
            'remote_qfq_vs_local_db_max_abs':float((m.close_price_qfq-m.dbq).abs().max()),
            'tdx_qfq_vs_local_db_max_abs':float((m.tdxq-m.dbq).abs().max()),
            'remote_minus_raw_unique':sorted(set(np.round(m.close_price_qfq-m.close_price_raw,6))),
            'latest':m[cols].iloc[-1].to_dict(), 'first':m[cols].iloc[0].to_dict()})
    results['remote_comparison']=checks
    results['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [HERE/'audit.py',HERE/'remote_samples.json',ROOT/'rps_analyzer/price_adjust.py',script_file("run_daily_rps.py"),script_file("run_tdx_screener.py")]}
    results['gbbq_sha256']=hashlib.sha256(Path('C:/new_tdx/T0002/hq_cache/gbbq').read_bytes()).hexdigest()
    con.close()
    (OUT/'audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in results.items() if k not in ('db_views','factor_schema','cache_schema','source_hashes','raw_cross_source_sample','tdx')},ensure_ascii=False,indent=2,default=str),flush=True)
    print('TDX',results['tdx'],flush=True)
    print('Cross-source', {k:v for k,v in results['raw_cross_source_sample'].items() if k!='details'},flush=True)


if __name__=='__main__':main()
