"""Dated research reuse: preserve production signals, update only as-of and horizon."""
from pathlib import Path

source = Path(__file__).with_name('analyze_cpu_20260922.py').read_text(encoding='utf-8')
source = source.replace('20260922', '20260923').replace('2026-09-22', '2026-09-23')
source = source.replace('2026-09-14', '2026-09-15')
source = source.replace('shift(-5)', 'shift(-4)').replace('ix+5', 'ix+4').replace('holding_horizon=5', 'holding_horizon=4')
exec(compile(source, str(Path(__file__)), 'exec'))
