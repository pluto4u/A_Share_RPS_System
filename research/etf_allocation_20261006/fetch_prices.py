"""Fetch explicitly unadjusted ETF OHLC from Eastmoney; keep full provenance."""
from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/etf_allocation_20261006/raw'
URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'


def main() -> None:
    protocol = json.loads(Path(__file__).with_name('protocol.json').read_text(encoding='utf-8'))
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    session = requests.Session()
    session.headers['User-Agent'] = 'Mozilla/5.0'
    for symbol in protocol['universe']:
        code = symbol.split('.')[0]
        params = {
            'secid': f'1.{code}', 'klt': 101, 'fqt': 0,
            'beg': protocol['fetch_start'].replace('-', ''),
            'end': protocol['fetch_end'].replace('-', ''), 'lmt': 10000,
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        }
        record = {'symbol': symbol, 'url': URL, 'params': params,
                  'fetched_at': datetime.now(UTC).isoformat(), 'adjust': 'none'}
        try:
            response = session.get(URL, params=params, timeout=20)
            response.raise_for_status()
            envelope = response.json()
            bars = (envelope.get('data') or {}).get('klines') or []
            if not bars:
                raise ValueError('no OHLC data returned')
            (OUT / f'{code}_eastmoney_none.json').write_text(
                json.dumps(envelope, ensure_ascii=False), encoding='utf-8')
            columns = ['date', 'open', 'close', 'high', 'low', 'volume_lots',
                       'amount', 'amplitude', 'pct', 'change', 'turnover']
            frame = pd.DataFrame([row.split(',') for row in bars], columns=columns)
            frame.to_csv(OUT / f'{code}_raw.csv', index=False, encoding='utf-8')
            record.update(ok=True, rows=len(frame), first=frame.date.iloc[0], last=frame.date.iloc[-1])
        except (requests.RequestException, ValueError) as exc:
            record.update(ok=False, error=str(exc))
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    (OUT / 'eastmoney_fetch_log.json').write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
