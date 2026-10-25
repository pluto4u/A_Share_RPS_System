"""Validate this revision and seal supplemental evidence, not prior versions."""
import json
from pathlib import Path
from urllib.request import urlopen
import nbformat
import pandas as pd
from audit import ROOT, HERE, OUT, sha, write_json

app = ROOT/'reports/rps_decision_20261006'
snapshot = json.loads((app/'src/data.json').read_text(encoding='utf-8'))
previous = json.loads((OUT/'previous_report_snapshot.json').read_text(encoding='utf-8'))
assert len(previous['queries']) == 4 and len(snapshot['queries']) == 7
for field in ['id','title','surface','report','filters','status']:
    assert snapshot[field] == previous[field]
for query in ['frozen_results','scope_decision','research_ideas']:
    assert snapshot['queries'][query] == previous['queries'][query]
assert snapshot['queries']['input_quality']['rows'] == previous['queries']['input_quality']['rows']
expected_source = previous['queries']['input_quality']['source'].copy()
expected_source['caveats'] = expected_source['caveats'][:-1] + ['小窗口对账不等于全市场验收；未修正原库，未重算收益。局部信号敏感性另见input_impact证据。']
assert snapshot['queries']['input_quality']['source'] == expected_source
impact = json.loads((ROOT/'output/rps_input_impact_20261007/v1/summary.json').read_text(encoding='utf-8'))
assert snapshot['queries']['input_impact']['rows'] == impact['model_changes']
summary = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
assert [r['count'] for r in snapshot['queries']['tdx_fallback']['rows']] == [
    summary['expected_codes'],summary['raw_files_present'],summary['missing_original_keys'],
    summary['adjustment_valid_codes'],summary['adjustment_rejected_codes'],summary['eligible_exact_coverage_codes']]
comparison = snapshot['queries']['tdx_comparison']['rows'][0]
assert all(comparison[k] == summary[k] for k in comparison)
bundle = ROOT/'output/tdx_input_20261007/staging_v1'
policy = json.loads((bundle/'input_policy.json').read_text(encoding='utf-8'))
assert policy['raw_snapshot_ready'] and not policy['strategy_input_ready']
assert policy['rows'] == 10114581 and not summary['gate_passed']
frozen = {}
for folder in ['rps_pv_20261006/v2','rps_diag_20261006/v1','rps_repair_20261006/v1',
               'rps_input_audit_20261007/v1','rps_input_impact_20261007/v1','tdx_input_20261007/v1','tdx_input_20261007/staging_v1']:
    path = ROOT/'output'/folder
    receipt = json.loads((path/('manifest.json' if folder.endswith('staging_v1') else 'final_manifest.json')).read_text(encoding='utf-8'))
    for relative, expected in receipt['files'].items(): assert sha(ROOT/relative) == expected
    frozen[folder] = len(receipt['files'])
registration = json.loads((OUT/'registration.json').read_text(encoding='utf-8'))
for path, expected in {**registration['input_hashes'],**registration['tdx_file_hashes']}.items():
    assert sha(path) == expected
for path in [HERE/'review.ipynb',OUT/'review.executed.ipynb']:
    notebook = nbformat.read(path,as_version=4); nbformat.validate(notebook)
    assert not any(o.output_type == 'error' for c in notebook.cells if c.cell_type == 'code' for o in c.outputs)
    assert all(c.execution_count is not None for c in notebook.cells if c.cell_type == 'code')
build = json.loads((app/'dist/data-app-build.json').read_text(encoding='utf-8'))
assert snapshot['buildStatus'] == 'complete'
for key in ['html','snapshot']:
    path = app/'dist'/build[key]['path']
    assert sha(path) == build[key]['sha256']
    with urlopen('http://127.0.0.1:4186/'+build[key]['path']) as response:
        assert response.status == 200
        content = response.read()
    assert content == path.read_bytes()
compiled_snapshot = json.loads((app/'dist'/build['snapshot']['path']).read_text(encoding='utf-8'))
assert compiled_snapshot == snapshot
source = (app/'src/content/report/ReportContent.jsx').read_text(encoding='utf-8')
assert '\ufffd' not in source
assert '影响尚未量化' not in source and '或量化信号影响' not in source
for component in ['input-impact-section','input-impact-table','tdx-fallback-section','tdx-fallback-table']:
    assert f'id="{component}"' in source
offline = app/'.data-app-offline/exports/rps-research-decision.html'
assert offline.is_file() and offline.stat().st_size > 2_000_000
result = dict(report_identity_preserved=True, existing_four_query_rows_preserved=True,
    frozen_files_unchanged=frozen, targeted_tests='11 passed',
    tdx_raw_snapshot_rows=policy['rows'], gate_passed=False,
    original_sources_unchanged=True, notebook_executed=True,
    notebook_saved_output_structure_checked=True, notebook_visual_review=False,
    live_http_and_snapshot_checked=True, browser_visual_review=False,
    browser_limitation='Prior browser access restriction respected; queued UI opening and HTTP checks are not visual acceptance',
    html_sha256=build['html']['sha256'],snapshot_sha256=build['snapshot']['sha256'],
    offline_sha256=sha(offline), production_promoted=False,outcomes_recomputed=False)
write_json(OUT/'delivery_validation.json',result)
files = sorted(p for folder in [HERE,OUT,bundle] for p in folder.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts)
files.append(ROOT/'tests/test_tdx_input_20261007.py')
write_json(OUT/'delivery_manifest.json',dict(files={str(p.relative_to(ROOT)):sha(p) for p in files}))
print(json.dumps(result,ensure_ascii=True))
