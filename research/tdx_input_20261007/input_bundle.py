"""Read-only TDX fallback adapter with an enforced raw-vs-research boundary."""
import argparse
import json
from pathlib import Path
import pandas as pd
from audit import ROOT, sha

DEST = ROOT/'output/tdx_input_20261007/staging_v1'

def policy():
    manifest = json.loads((DEST/'manifest.json').read_text(encoding='utf-8'))
    path = DEST/'input_policy.json'
    if sha(path) != manifest['files'][str(path.relative_to(ROOT))]:
        raise RuntimeError('Frozen input policy changed')
    return json.loads(path.read_text(encoding='utf-8')), manifest

def load_for_reconciliation(codes, start, end):
    p, manifest = policy()
    allowed = json.loads((ROOT/'output/tdx_input_20261007/v1/registration.json').read_text(encoding='utf-8'))['expected_codes']
    if not codes or not set(codes).issubset(allowed): raise ValueError('Only registered surviving codes allowed')
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if first > last or first < pd.Timestamp(p['start']) or last > pd.Timestamp(p['cutoff']):
        raise ValueError('Dates outside frozen range')
    path = DEST/p['file']
    if sha(path) != manifest['files'][str(path.relative_to(ROOT))]: raise RuntimeError('Frozen raw bundle changed')
    frame = pd.read_parquet(path, filters=[('ts_code','in',list(codes)),('trade_date','>=',first),('trade_date','<=',last)])
    if frame.duplicated(['ts_code','trade_date']).any(): raise RuntimeError('Duplicate stock-date keys')
    return frame.sort_values(['ts_code','trade_date']).reset_index(drop=True)

def load_for_strategy():
    p, _ = policy()
    if not p['strategy_input_ready'] or p['adjustment'] != 'qfq':
        raise RuntimeError('TDX research input not accepted: raw prices, history mapping and special adjustments remain unresolved')
    raise RuntimeError('No validated strategy version registered')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--code',action='append',required=True)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end',required=True)
    args = parser.parse_args()
    print(load_for_reconciliation(args.code,args.start,args.end).to_string(index=False))
