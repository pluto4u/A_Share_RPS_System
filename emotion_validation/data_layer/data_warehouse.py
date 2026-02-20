#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场A股数据仓库

从 akshare 获取全A股日线数据，存储为 parquet 格式，支持增量更新。

CLI:
    python -m emotion_validation.data_layer.data_warehouse --init
    python -m emotion_validation.data_layer.data_warehouse --update
    python -m emotion_validation.data_layer.data_warehouse --status
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / 'backtest' / 'data'

STOCK_HIST_COL_MAP = {
    '日期': 'trade_date',
    '股票代码': 'ts_code',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'vol',
    '成交额': 'amount',
    '振幅': 'amplitude',
    '涨跌幅': 'pct_chg',
    '涨跌额': 'change',
    '换手率': 'turnover',
}

INDEX_HIST_COL_MAP = {
    '日期': 'trade_date',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'vol',
    '成交额': 'amount',
    '振幅': 'amplitude',
    '涨跌幅': 'pct_chg',
    '涨跌额': 'change',
    '换手率': 'turnover',
}

INDEX_CODES = {
    'sh000001': ('000001', '上证指数'),
    'sh000300': ('000300', '沪深300'),
    'sz399006': ('399006', '创业板指'),
    'sh000852': ('000852', '中证1000'),
}


class DataWarehouse:
    """全市场A股数据仓库"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.stock_daily_path = self.data_dir / 'stock_daily.parquet'
        self.stock_daily_csv_path = self.data_dir / 'stock_daily.csv'
        self.index_daily_path = self.data_dir / 'index_daily.parquet'
        self.stock_list_path = self.data_dir / 'stock_list_cache.csv'
        self.meta_path = self.data_dir / 'meta.json'

        self._meta = self._load_meta()

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def _load_meta(self) -> dict:
        if self.meta_path.exists():
            with open(self.meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_meta(self, updates: dict):
        self._meta.update(updates)
        with open(self.meta_path, 'w', encoding='utf-8') as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2, default=str)

    # ------------------------------------------------------------------
    # Stock list
    # ------------------------------------------------------------------

    def get_stock_list(self) -> pd.DataFrame:
        """获取全A股股票列表（排除 BJ 北交所）"""
        import akshare as ak

        logger.info("获取全A股股票列表...")
        df = ak.stock_zh_a_spot_em()

        stock_list = df[['代码', '名称', '总市值', '流通市值']].copy()
        stock_list.columns = ['code', 'name', 'total_mv', 'circ_mv']

        stock_list = stock_list[
            stock_list['code'].str.match(r'^(0|3|6)\d{5}$')
        ].reset_index(drop=True)

        stock_list.to_csv(self.stock_list_path, index=False, encoding='utf-8-sig')
        logger.info(f"股票列表: {len(stock_list)} 只 (排除北交所)")
        return stock_list

    # ------------------------------------------------------------------
    # Single stock fetch
    # ------------------------------------------------------------------

    def _fetch_single_stock(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """获取单只股票前复权日线数据"""
        import akshare as ak

        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period='daily',
                start_date=start_date, end_date=end_date,
                adjust='qfq',
            )
            if df is None or df.empty:
                return None

            df = df.rename(columns=STOCK_HIST_COL_MAP)

            suffix = '.SH' if code.startswith('6') else '.SZ'
            df['ts_code'] = code + suffix
            return df

        except Exception as e:
            logger.debug(f"获取 {code} 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # Index fetch
    # ------------------------------------------------------------------

    def _fetch_index_data(self, start_date: str, end_date: str):
        """获取主要指数日线数据"""
        import akshare as ak

        logger.info("获取指数日线数据...")
        all_index = []

        for em_symbol, (code, name) in INDEX_CODES.items():
            try:
                df = ak.stock_zh_index_daily_em(symbol=em_symbol,
                                                start_date=start_date,
                                                end_date=end_date)
                if df is not None and not df.empty:
                    df = df.rename(columns={'date': 'trade_date'})
                    df['symbol'] = code
                    df['name'] = name
                    all_index.append(df)
                    logger.info(f"  {name}({code}): {len(df)} 行")
                    continue
            except Exception as e:
                logger.debug(f"指数 {name} 主API失败: {e}")

            try:
                df = ak.index_zh_a_hist(symbol=code, period='daily',
                                        start_date=start_date, end_date=end_date)
                if df is not None and not df.empty:
                    df = df.rename(columns=INDEX_HIST_COL_MAP)
                    df['symbol'] = code
                    df['name'] = name
                    all_index.append(df)
                    logger.info(f"  {name}({code}): {len(df)} 行 (备用API)")
            except Exception as e2:
                logger.warning(f"指数 {name}({code}) 两个API均失败: {e2}")

        if all_index:
            index_df = pd.concat(all_index, ignore_index=True)
            if 'trade_date' in index_df.columns:
                index_df['trade_date'] = pd.to_datetime(index_df['trade_date'])
            index_df.to_parquet(self.index_daily_path, index=False)
            logger.info(f"指数数据保存完成: {len(index_df)} 行")

    # ------------------------------------------------------------------
    # Full init
    # ------------------------------------------------------------------

    def init_full_data(self, start_date: str = '20240101', end_date: str = None,
                       max_workers: int = 8) -> bool:
        """首次全量拉取全市场日线数据"""
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')

        logger.info(f"=== 全量拉取 {start_date} -> {end_date} ===")

        stock_list = self.get_stock_list()
        codes = stock_list['code'].tolist()
        logger.info(f"待拉取: {len(codes)} 只, 并发: {max_workers} 线程")

        all_data = []
        failed = []
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._fetch_single_stock, code, start_date, end_date): code
                for code in codes
            }
            done_count = 0
            for future in as_completed(futures):
                code = futures[future]
                done_count += 1
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        all_data.append(df)
                    else:
                        failed.append(code)
                except Exception:
                    failed.append(code)

                if done_count % 200 == 0 or done_count == len(codes):
                    elapsed = time.time() - t0
                    logger.info(
                        f"进度: {done_count}/{len(codes)} "
                        f"({done_count / len(codes) * 100:.1f}%), "
                        f"成功: {len(all_data)}, 失败: {len(failed)}, "
                        f"耗时: {elapsed:.0f}s"
                    )

        if not all_data:
            logger.error("未获取到任何数据!")
            return False

        big_df = pd.concat(all_data, ignore_index=True)
        big_df['trade_date'] = pd.to_datetime(big_df['trade_date'])
        big_df = big_df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

        big_df.to_parquet(self.stock_daily_path, index=False)
        big_df.to_csv(self.stock_daily_csv_path, index=False, encoding='utf-8-sig')

        elapsed = time.time() - t0
        parquet_mb = self.stock_daily_path.stat().st_size / 1024 / 1024
        csv_mb = self.stock_daily_csv_path.stat().st_size / 1024 / 1024

        logger.info(
            f"=== 全量拉取完成 ===\n"
            f"  股票数: {len(all_data)}\n"
            f"  总行数: {len(big_df):,}\n"
            f"  失败数: {len(failed)}\n"
            f"  Parquet: {parquet_mb:.1f} MB\n"
            f"  CSV: {csv_mb:.1f} MB\n"
            f"  耗时: {elapsed:.0f}s"
        )

        self._fetch_index_data(start_date, end_date)

        self._save_meta({
            'last_update_date': end_date,
            'start_date': start_date,
            'stock_count': len(all_data),
            'total_rows': len(big_df),
            'failed_codes': failed[:50],
            'fetch_time_seconds': round(elapsed, 1),
            'parquet_size_mb': round(parquet_mb, 1),
            'updated_at': datetime.now().isoformat(),
        })
        return True

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------

    def update_data(self, max_workers: int = 8) -> bool:
        """增量更新（追加最新交易日数据）"""
        if not self._meta:
            logger.error("无元数据, 请先执行 --init")
            return False

        last_date = self._meta.get('last_update_date', '')
        if not last_date:
            logger.error("无上次更新日期, 请先执行 --init")
            return False

        start_date = (datetime.strptime(last_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')

        if start_date > end_date:
            logger.info("数据已是最新")
            return True

        logger.info(f"=== 增量更新 {start_date} -> {end_date} ===")

        stock_list = self.get_stock_list()
        codes = stock_list['code'].tolist()

        new_data = []
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._fetch_single_stock, code, start_date, end_date): code
                for code in codes
            }
            for future in as_completed(futures):
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        new_data.append(df)
                except Exception:
                    pass

        if not new_data:
            logger.info("无新数据（可能非交易日）")
            return True

        new_df = pd.concat(new_data, ignore_index=True)
        new_df['trade_date'] = pd.to_datetime(new_df['trade_date'])

        if self.stock_daily_path.exists():
            existing = pd.read_parquet(self.stock_daily_path)
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.drop_duplicates(
                subset=['ts_code', 'trade_date']
            ).sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
        else:
            combined = new_df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

        combined.to_parquet(self.stock_daily_path, index=False)
        combined.to_csv(self.stock_daily_csv_path, index=False, encoding='utf-8-sig')

        self._fetch_index_data(start_date, end_date)

        elapsed = time.time() - t0
        self._save_meta({
            'last_update_date': end_date,
            'stock_count': combined['ts_code'].nunique(),
            'total_rows': len(combined),
            'updated_at': datetime.now().isoformat(),
        })

        logger.info(f"增量更新完成: 新增 {len(new_df):,} 行, 耗时 {elapsed:.0f}s")
        return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """查看仓库状态"""
        info = {'meta': self._meta}

        for label, path in [
            ('stock_daily_parquet', self.stock_daily_path),
            ('stock_daily_csv', self.stock_daily_csv_path),
            ('index_daily_parquet', self.index_daily_path),
            ('stock_list_csv', self.stock_list_path),
        ]:
            if path.exists():
                size_mb = path.stat().st_size / 1024 / 1024
                info[label] = f'{size_mb:.1f} MB'
            else:
                info[label] = 'not found'

        return info

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def load_stock_daily(self, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """读取个股日线数据"""
        if self.stock_daily_path.exists():
            df = pd.read_parquet(self.stock_daily_path)
        elif self.stock_daily_csv_path.exists():
            df = pd.read_csv(self.stock_daily_csv_path)
            df['trade_date'] = pd.to_datetime(df['trade_date'])
        else:
            raise FileNotFoundError("无个股日线数据, 请先执行 --init")

        if start_date:
            df = df[df['trade_date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['trade_date'] <= pd.to_datetime(end_date)]
        return df

    def load_index_daily(self, symbol: str = None) -> pd.DataFrame:
        """读取指数日线数据"""
        if not self.index_daily_path.exists():
            raise FileNotFoundError("无指数日线数据")
        df = pd.read_parquet(self.index_daily_path)
        if symbol:
            df = df[df['symbol'] == symbol]
        return df


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description='全市场A股数据仓库',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python -m emotion_validation.data_layer.data_warehouse --init
    python -m emotion_validation.data_layer.data_warehouse --init --start-date 20230101 --workers 16
    python -m emotion_validation.data_layer.data_warehouse --update
    python -m emotion_validation.data_layer.data_warehouse --status
'''
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--init', action='store_true', help='首次全量拉取')
    group.add_argument('--update', action='store_true', help='增量更新')
    group.add_argument('--status', action='store_true', help='查看仓库状态')

    parser.add_argument('--start-date', default='20240101',
                        help='全量拉取起始日期 (默认: 20240101)')
    parser.add_argument('--end-date', default=None,
                        help='截止日期 (默认: 今天)')
    parser.add_argument('--workers', type=int, default=8,
                        help='并发线程数 (默认: 8)')
    parser.add_argument('--data-dir', default=None,
                        help='数据目录 (默认: backtest/data)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='详细日志')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
    )

    warehouse = DataWarehouse(data_dir=args.data_dir)

    if args.init:
        ok = warehouse.init_full_data(
            start_date=args.start_date,
            end_date=args.end_date,
            max_workers=args.workers,
        )
        sys.exit(0 if ok else 1)

    elif args.update:
        ok = warehouse.update_data(max_workers=args.workers)
        sys.exit(0 if ok else 1)

    elif args.status:
        info = warehouse.status()
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
