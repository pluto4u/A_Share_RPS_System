import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
history = json.loads((ROOT / 'output/daily_dual_strategy_history.json').read_text(encoding='utf-8'))
record = next(x for x in history if x['data_asof'] == '2026-09-16')
out = ROOT / 'output/review_20260916'
out.mkdir(exist_ok=True)
for name, data in [('qfq_verification.json', record['g0']), ('limit_ecology_latest.json', record['ecology']), ('historical_daily_snapshot.json', record)]:
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(out)
