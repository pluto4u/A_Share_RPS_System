#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 数据管理: 拉取、缓存、加载行业 ETF 日线数据
"""

import time
import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

ETF_POOL = [
    ('512480', '半导体ETF', '科技'),
    ('512760', '芯片ETF',   '科技'),
    ('159819', '人工智能ETF', '科技'),
    ('588050', '科创50ETF',  '科技'),
    ('512010', '医药ETF',    '防御'),
    ('515650', '消费50ETF',  '消费'),
    ('516160', '新能源ETF',  '周期'),
    ('512660', '军工ETF',    '主题'),
    ('512880', '证券ETF',    '金融'),
    ('510300', '沪深300ETF', '宽基'),
    ('159915', '创业板ETF',  '成长'),
    ('513130', '恒生科技ETF', '港股科技'),
]

COL_MAP = {
    '日期': 'trade_date',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'volume',
    '成交额': 'amount',
    '涨跌幅': 'pct_chg',
}

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / 'backtest' / 'data' / 'etf'


class ETFDataManager:
    """ETF 日线数据管理器"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def fetch_all(self, start_date: str = '20240101', end_date: str = None) -> Dict[str, pd.DataFrame]:
        """从 akshare 拉取全部 ETF 历史数据"""
        import akshare as ak
        from datetime import datetime

        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')

        results = {}
        for code, name, sector in ETF_POOL:
            csv_path = self.data_dir / f'{code}.csv'
            try:
                logger.info(f"Fetching {name} ({code})...")
                df = ak.fund_etf_hist_em(
                    symbol=code, period='daily',
                    start_date=start_date, end_date=end_date,
                    adjust='qfq'
                )
                if df is None or df.empty:
                    logger.warning(f"  {code}: no data")
                    continue

                df = df.rename(columns=COL_MAP)
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                df['symbol'] = code
                df['name'] = name
                df['sector'] = sector

                keep = ['trade_date', 'symbol', 'name', 'sector',
                        'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']
                keep = [c for c in keep if c in df.columns]
                df = df[keep].sort_values('trade_date').reset_index(drop=True)

                df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                results[code] = df
                logger.info(f"  {code} {name}: {len(df)} rows saved")
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"  {code} {name}: FAILED - {e}")

        logger.info(f"Fetched {len(results)}/{len(ETF_POOL)} ETFs")
        return results

    def load_all(self) -> Dict[str, pd.DataFrame]:
        """从本地缓存加载全部 ETF 数据"""
        results = {}
        for code, name, sector in ETF_POOL:
            csv_path = self.data_dir / f'{code}.csv'
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                results[code] = df
        return results

    def load_one(self, symbol: str) -> Optional[pd.DataFrame]:
        """加载单只 ETF"""
        csv_path = self.data_dir / f'{symbol}.csv'
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            return df
        return None

    def get_combined(self) -> pd.DataFrame:
        """合并全部 ETF 为一个 DataFrame"""
        all_data = self.load_all()
        if not all_data:
            return pd.DataFrame()
        return pd.concat(all_data.values(), ignore_index=True)

    @staticmethod
    def pool_info() -> List[dict]:
        return [{'symbol': c, 'name': n, 'sector': s} for c, n, s in ETF_POOL]


# CLI
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    mgr = ETFDataManager()
    data = mgr.fetch_all()
    print(f"\nDone: {len(data)} ETFs fetched")
    for code, df in data.items():
        print(f"  {code}: {len(df)} rows, {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
