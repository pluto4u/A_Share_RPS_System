"""Stage a dated native TDX raw bundle; never substitute raw prices for QFQ."""
from pathlib import Path
import json
import struct
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from audit import ROOT, HERE, OUT, PRICE, sha, extract, write_json

DEST = ROOT/'output/tdx_input_20261007/staging_v1'
if DEST.exists(): raise FileExistsError('Create another staging version, never overwrite')
registration = json.loads((OUT/'registration.json').read_text(encoding='utf-8'))
protocol = json.loads((HERE/'protocol.json').read_text(encoding='utf-8'))
summary = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
parser_path = ROOT/'research/rps_repair_20261006/common.py'
assert sha(parser_path) == registration['input_hashes'][str(parser_path)]
reader = extract(parser_path,'read_raw',dict(Path=Path,struct=struct,pd=pd,PRICE=PRICE))
DEST.mkdir(parents=True)
write_json(DEST/'registration.json',dict(purpose='TDX native raw bundle only',
    protocol_sha256=sha(HERE/'protocol.json'), source_audit_sha256=sha(OUT/'final_manifest.json'),
    code_count=len(registration['expected_codes']), start=protocol['start'], cutoff=protocol['cutoff']))
writer = None; total = 0; codes = []
try:
    for i, code in enumerate(registration['expected_codes']):
        path = Path(protocol['tdx_root'])/'vipdoc'/code[-2:].lower()/'lday'/(code[-2:].lower()+code[:6]+'.day')
        assert sha(path) == registration['tdx_file_hashes'][str(path)]
        frame = reader(path)
        frame = frame.loc[frame.trade_date.between(protocol['start'],protocol['cutoff'])].copy()
        frame['ts_code'] = code
        frame = frame[['ts_code','trade_date',*PRICE,'vol','amount']]
        table = pa.Table.from_pandas(frame,preserve_index=False)
        if writer is None: writer = pq.ParquetWriter(DEST/'tdx_raw.parquet',table.schema,compression='zstd')
        writer.write_table(table)
        total += len(frame); codes.append(code)
        if i % 1000 == 0: print(f'stage raw {i}/5224',flush=True)
finally:
    if writer is not None: writer.close()
metadata = pq.ParquetFile(DEST/'tdx_raw.parquet').metadata
assert metadata.num_rows == total == summary['original_rows']-summary['missing_original_keys']+summary['extra_tdx_keys']
assert len(codes) == len(set(codes)) == 5224
for path, expected in registration['tdx_file_hashes'].items(): assert sha(path) == expected
write_json(DEST/'input_policy.json',dict(
    source='TDX local .day files', file='tdx_raw.parquet', adjustment='unadjusted',
    units=dict(price='CNY/share',vol='shares',amount='CNY'),
    rows=total, codes=5224, start=protocol['start'], cutoff=protocol['cutoff'],
    raw_snapshot_ready=True, strategy_input_ready=False, research_selector_enabled=False,
    blockers=dict(missing_original_keys=summary['missing_original_keys'],
                  unsupported_adjustment_codes=summary['adjustment_rejected_codes']),
    prohibition='Do not compute price-return RPS/backtest from unadjusted prices; do not silently drop failed codes, zero fill missing bars, mix providers or overwrite production input',
    next_gate='Recover the one truncated raw history and validate specialized adjustment events; then build a separate QFQ/RPS input version and rerun unchanged research once'))
write_json(DEST/'manifest.json',dict(files={str(p.relative_to(ROOT)):sha(p) for p in sorted(DEST.iterdir()) if p.is_file()},
    original_sources_unchanged=True, outcomes_recomputed=False, production_promoted=False))
print(json.dumps(dict(rows=total,codes=5224,raw_snapshot_ready=True,strategy_input_ready=False),ensure_ascii=True))
