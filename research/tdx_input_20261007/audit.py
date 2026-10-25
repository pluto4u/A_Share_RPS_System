from project_paths import script_file
"""Read-only feasibility gate for TDX, restricted to the frozen survivor pool."""
from __future__ import annotations
import ast
import hashlib
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from pytdx.reader import GbbqReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / 'output/tdx_input_20261007/v1'
PRICE = ['open', 'high', 'low', 'close']
EVENT_FIELDS = ['hongli_panqianliutong','peigujia_qianzongguben','songgu_qianzongguben','peigu_houzongguben']

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''): h.update(b)
    return h.hexdigest()

def write_json(path, obj):
    if path.exists(): raise FileExistsError(path)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def extract(path, name, namespace):
    node = next(n for n in ast.parse(path.read_text(encoding='utf-8-sig')).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]

def main():
    if OUT.exists(): raise FileExistsError('Version exists; never rerun over frozen outputs')
    p = json.loads((HERE/'protocol.json').read_text(encoding='utf-8'))
    panel = ROOT / p['original_panel']
    original_sha = sha(panel)
    if original_sha != p['expected_original_sha256']: raise RuntimeError('Frozen input changed')
    original = pd.read_parquet(panel, columns=['ts_code','trade_date',*PRICE,'vol','amount'])
    original.trade_date = pd.to_datetime(original.trade_date)
    codes = sorted(original.ts_code.unique())
    assert len(codes) == 5224 and not original.duplicated(['ts_code','trade_date']).any()
    assert original.trade_date.min() == pd.Timestamp(p['start'])
    assert original.trade_date.max() == pd.Timestamp(p['cutoff'])
    tdx = Path(p['tdx_root'])
    paths = {c:tdx/'vipdoc'/c[-2:].lower()/'lday'/(c[-2:].lower()+c[:6]+'.day') for c in codes}
    missing_files = [c for c, path in paths.items() if not path.is_file()]
    file_hashes = {str(path):sha(path) for path in paths.values() if path.is_file()}
    gbbq = tdx/'T0002/hq_cache/gbbq'
    adjust_path = script_file("run_local_tdx_compare.py")
    raw_path = ROOT/'research/rps_repair_20261006/common.py'
    report = ROOT/'reports/rps_decision_20261006'
    previous_build = json.loads((report/'dist/data-app-build.json').read_text(encoding='utf-8'))
    # Preserve the verified pre-revision snapshot without reading browser storage.
    previous_snapshot = next((report/'dist').glob('snapshot.*.json'))
    candidates = list((report/'dist').glob('snapshot.*.json'))
    for path in candidates:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if len(obj.get('queries', {})) == 4 and obj.get('generatedAt') == '2026-10-07T07:10:56+00:00':
            previous_snapshot = path; break
    fixed = {str(panel):original_sha, str(gbbq):sha(gbbq), str(adjust_path):sha(adjust_path), str(raw_path):sha(raw_path)}
    OUT.mkdir(parents=True)
    write_json(OUT/'registration.json', dict(generated_at=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=sha(HERE/'protocol.json'), original_rows=len(original), expected_codes=codes,
        input_hashes=fixed, tdx_file_hashes=file_hashes, missing_files=missing_files,
        previous_build=previous_build, previous_report_snapshot_sha256=sha(previous_snapshot)))
    write_json(OUT/'previous_report_snapshot.json', json.loads(previous_snapshot.read_text(encoding='utf-8')))
    raw_reader = extract(raw_path, 'read_raw', dict(Path=Path, struct=struct, pd=pd, PRICE=PRICE))
    adjust = extract(adjust_path, 'adjust', dict(Any=Any, np=np, PRICE=PRICE, EVENT_FIELDS=EVENT_FIELDS))
    actions = GbbqReader().get_df(str(gbbq))
    actions['ts_code'] = actions.code.astype(str).str.zfill(6)+actions.market.map({0:'.SZ',1:'.SH'})
    actions = actions.loc[actions.ts_code.isin(codes)].copy()
    actions['date'] = pd.to_datetime(actions.datetime.astype(str), format='%Y%m%d')
    events = {c:g for c,g in actions.groupby('ts_code', sort=False)}
    empty = actions.iloc[:0]
    cutoff = pd.Timestamp(p['cutoff']); start = pd.Timestamp(p['start'])
    rows = []; missing_rows = []; big_rows = []
    for i, (code, old) in enumerate(original.groupby('ts_code', sort=True, observed=True)):
        if i % 500 == 0: print(f'audit {i}/{len(codes)}', flush=True)
        path = paths[code]
        r = dict(ts_code=code, file_exists=path.is_file(), original_rows=len(old),
                 raw_status='missing_file', adjustment_status='not_checked', reason='',
                 missing_original_keys=len(old), extra_tdx_keys=0, bad_quantity_rows=0,
                 amount_mismatch_cells=0, volume_mismatch_cells=0, large_amount_cells=0,
                 large_volume_cells=0, adjusted_price_mismatch_cells=0, compared_price_cells=0)
        if not path.is_file(): rows.append(r); continue
        try:
            raw = raw_reader(path)
            d = raw.loc[raw.trade_date.between(start, cutoff)].copy()
            r.update(raw_rows=len(raw), scoped_rows=len(d), first_raw_date=str(raw.trade_date.min().date()),
                     latest_raw_date=str(raw.trade_date.max().date()), raw_status='valid')
            if d.empty: raise ValueError('no bars in required interval')
            values = d[['vol','amount']].to_numpy(float)
            r['bad_quantity_rows'] = int((~np.isfinite(values).all(axis=1) | (values < 0).any(axis=1)).sum())
            if r['bad_quantity_rows']: raise ValueError('nonfinite or negative quantity')
            if not np.isfinite(d[PRICE]).all().all() or (d[PRICE] <= 0).any().any():
                raise ValueError('invalid raw price')
            if (d.high < d[PRICE].max(axis=1)).any() or (d.low > d[PRICE].min(axis=1)).any():
                raise ValueError('impossible raw OHLC')
            left = old.set_index('trade_date').sort_index(); right = d.set_index('trade_date').sort_index()
            missing = left.index.difference(right.index)
            r['missing_original_keys'] = len(missing)
            r['extra_tdx_keys'] = len(right.index.difference(left.index))
            missing_rows.extend(dict(ts_code=code, trade_date=str(x.date())) for x in missing)
            keys = left.index.intersection(right.index)
            r['compared_liquidity_rows'] = len(keys)
            for field, prefix in [('amount','amount'),('vol','volume')]:
                a = left.loc[keys,field].to_numpy(float); b = right.loc[keys,field].to_numpy(float)
                r[prefix+'_mismatch_cells'] = int((~np.isclose(a,b,**p['liquidity_tolerance'])).sum())
                ratio = np.divide(a, b, out=np.full_like(a, np.nan), where=b>0)
                large = ((ratio < .5)|(ratio > 2)) & np.isfinite(ratio)
                r['large_'+prefix+'_cells'] = int(large.sum())
                for k in np.flatnonzero(large):
                    big_rows.append(dict(ts_code=code, trade_date=str(keys[k].date()), field=field,
                        original_value=float(a[k]), tdx_value=float(b[k]), original_to_tdx=float(ratio[k])))
            try:
                qfq, count = adjust(raw, events.get(code, empty), cutoff, bars=None)
                qfq = qfq.loc[qfq.trade_date >= start].set_index('trade_date')
                qkeys = left.index.intersection(qfq.index)
                a = left.loc[qkeys,PRICE].to_numpy(float); b = qfq.loc[qkeys,PRICE].to_numpy(float)
                r['compared_price_cells'] = int(a.size)
                r['adjusted_price_mismatch_cells'] = int((~np.isclose(a,b,**p['price_tolerance'])).sum())
                r.update(adjustment_status='valid', price_events=count)
            except ValueError as e:
                r.update(adjustment_status='rejected', reason=str(e))
        except (ValueError, OverflowError) as e:
            r.update(raw_status='invalid', reason=str(e))
        rows.append(r)
    inventory = pd.DataFrame(rows)
    inventory.to_csv(OUT/'inventory.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(missing_rows, columns=['ts_code','trade_date']).to_csv(OUT/'missing_original_keys.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(big_rows,columns=['ts_code','trade_date','field','original_value','tdx_value','original_to_tdx']).to_csv(OUT/'large_liquidity_differences.csv',index=False,encoding='utf-8-sig')
    passed = (inventory.raw_status.eq('valid') & inventory.adjustment_status.eq('valid') & inventory.missing_original_keys.eq(0))
    summary = dict(generated_at=datetime.now(timezone.utc).isoformat(), expected_codes=len(codes),
        original_rows=len(original), raw_files_present=int(inventory.file_exists.sum()),
        raw_valid_codes=int(inventory.raw_status.eq('valid').sum()),
        missing_files=missing_files, missing_original_keys=int(inventory.missing_original_keys.sum()),
        missing_keys_codes=int(inventory.missing_original_keys.gt(0).sum()),
        extra_tdx_keys=int(inventory.extra_tdx_keys.sum()),
        adjustment_valid_codes=int(inventory.adjustment_status.eq('valid').sum()),
        adjustment_rejected_codes=int(inventory.adjustment_status.eq('rejected').sum()),
        adjustment_reasons={str(k):int(v) for k,v in inventory.loc[inventory.adjustment_status.eq('rejected'),'reason'].value_counts().items()},
        eligible_exact_coverage_codes=int(passed.sum()), gate_passed=bool(passed.all()),
        amount_mismatch_cells=int(inventory.amount_mismatch_cells.sum()),
        volume_mismatch_cells=int(inventory.volume_mismatch_cells.sum()),
        large_amount_cells=int(inventory.large_amount_cells.sum()),
        large_volume_cells=int(inventory.large_volume_cells.sum()),
        large_liquidity_code_dates=len({(x['ts_code'],x['trade_date']) for x in big_rows}),
        adjusted_price_compared_cells=int(inventory.compared_price_cells.sum()),
        adjusted_price_mismatch_cells=int(inventory.adjusted_price_mismatch_cells.sum()),
        production_promoted=False, outcomes_recomputed=False,
        scope='Feasibility and source consistency only; source disagreement does not establish which value is correct for all market observations')
    for path, h in {**fixed, **file_hashes}.items():
        if sha(path) != h: raise RuntimeError('Source changed during audit: '+path)
    summary['original_inputs_unchanged'] = True
    write_json(OUT/'summary.json', summary)
    write_json(OUT/'final_manifest.json', {'files':{str(x.relative_to(ROOT)):sha(x) for x in sorted(OUT.iterdir()) if x.is_file()},
        'research_sources':{str(x.relative_to(ROOT)):sha(x) for x in sorted(HERE.iterdir()) if x.is_file()}})
    print(json.dumps(summary,ensure_ascii=True), flush=True)

if __name__ == '__main__': main()
