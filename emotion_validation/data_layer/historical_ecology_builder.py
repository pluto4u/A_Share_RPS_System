#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史情绪生态特征构建器

从全市场日线数据逐日统计涨停/跌停/炸板/连板/涨跌家数等市场生态特征，
生成与 LimitUpEcologyParser 兼容的 DataFrame。

解决问题: akshare 涨停池 API 只有近 2 周数据, 无法回溯历史, 导致
emotion_score 恒定 42.5。本模块从日线数据自行还原全部历史特征。

CLI:
    python -m emotion_validation.data_layer.historical_ecology_builder --build
    python -m emotion_validation.data_layer.historical_ecology_builder --build --start-date 20230101
"""

import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / 'backtest' / 'data'

LIMIT_UP_THRESHOLD = 9.8
LIMIT_DOWN_THRESHOLD = -9.8
BIG_LOSS_THRESHOLD = -8.0
ST_LIMIT_THRESHOLD = 4.8


class HistoricalEcologyBuilder:
    """从全市场日线数据构建历史情绪生态特征"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.stock_daily_path = self.data_dir / 'stock_daily.parquet'
        self.stock_daily_csv_path = self.data_dir / 'stock_daily.csv'
        self.ecology_cache_path = self.data_dir / 'ecology_cache.csv'

        self._stock_data: Optional[pd.DataFrame] = None
        self._ecology_df: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_stock_data(self) -> pd.DataFrame:
        """加载全市场日线数据"""
        if self._stock_data is not None:
            return self._stock_data

        if self.stock_daily_path.exists():
            logger.info(f"从 parquet 加载: {self.stock_daily_path}")
            df = pd.read_parquet(self.stock_daily_path)
        elif self.stock_daily_csv_path.exists():
            logger.info(f"从 CSV 加载: {self.stock_daily_csv_path}")
            df = pd.read_csv(self.stock_daily_csv_path)
        else:
            raise FileNotFoundError(
                "无个股日线数据, 请先运行 data_warehouse --init"
            )

        df['trade_date'] = pd.to_datetime(df['trade_date'])

        if 'pct_chg' not in df.columns:
            if 'close' in df.columns:
                df = df.sort_values(['ts_code', 'trade_date'])
                df['pct_chg'] = df.groupby('ts_code')['close'].pct_change() * 100

        self._stock_data = df
        logger.info(f"日线数据: {len(df):,} 行, {df['ts_code'].nunique()} 只股票")
        return df

    # ------------------------------------------------------------------
    # Daily ecology statistics
    # ------------------------------------------------------------------

    def _compute_daily_ecology(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        逐日统计市场生态特征

        Returns:
            DataFrame with columns:
                trade_date, limit_up_count, limit_down_count,
                attempt_limit_up_count, broken_limit_count,
                big_loss_count, up_count, down_count, flat_count,
                ad_ratio, total_stocks
        """
        logger.info("逐日统计市场生态特征...")

        required_cols = {'trade_date', 'ts_code', 'pct_chg', 'high', 'close', 'open'}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"缺少必要字段: {missing}")

        records = []
        for date, group in df.groupby('trade_date'):
            pct = group['pct_chg']
            n = len(group)

            limit_up = (pct >= LIMIT_UP_THRESHOLD).sum()
            limit_down = (pct <= LIMIT_DOWN_THRESHOLD).sum()
            big_loss = (pct <= BIG_LOSS_THRESHOLD).sum()
            up = (pct > 0).sum()
            down = (pct < 0).sum()
            flat = (pct == 0).sum()

            # 炸板估算: 最高价触及涨停（日内最高涨幅 >= 9.8%）但收盘未封住
            if 'open' in group.columns and 'high' in group.columns:
                prev_close = group['close'] / (1 + group['pct_chg'] / 100)
                high_pct = (group['high'] - prev_close) / prev_close * 100
                touched_limit = (high_pct >= LIMIT_UP_THRESHOLD).sum()
                attempt_limit_up = touched_limit
                broken_limit = max(0, attempt_limit_up - limit_up)
            else:
                attempt_limit_up = limit_up
                broken_limit = 0

            ad_ratio = round(up / max(down, 1), 2)

            records.append({
                'trade_date': date,
                'limit_up_count': int(limit_up),
                'limit_down_count': int(limit_down),
                'attempt_limit_up_count': int(attempt_limit_up),
                'broken_limit_count': int(broken_limit),
                'big_loss_count': int(big_loss),
                'up_count': int(up),
                'down_count': int(down),
                'flat_count': int(flat),
                'ad_ratio': float(ad_ratio),
                'total_stocks': int(n),
            })

        ecology = pd.DataFrame(records)
        ecology['trade_date'] = pd.to_datetime(ecology['trade_date'])
        ecology = ecology.sort_values('trade_date').reset_index(drop=True)

        logger.info(f"生态特征: {len(ecology)} 个交易日")
        return ecology

    # ------------------------------------------------------------------
    # Consecutive board tracking (max_board_height)
    # ------------------------------------------------------------------

    def _compute_board_tracking(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        追踪连板高度:
        - 维护每只股票的连续涨停天数
        - 每日输出 max_board_height, avg_board_height, board_2_count, board_3plus_count
        - 计算次日溢价: 昨日涨停股今日平均涨跌幅
        """
        logger.info("追踪连板高度...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        df_sorted['is_limit_up'] = df_sorted['pct_chg'] >= LIMIT_UP_THRESHOLD

        # vectorised consecutive-day counter per stock
        df_sorted['block'] = (
            df_sorted.groupby('ts_code')['is_limit_up']
            .transform(lambda s: (~s).cumsum())
        )
        df_sorted['consec_days'] = (
            df_sorted[df_sorted['is_limit_up']]
            .groupby(['ts_code', 'block'])
            .cumcount() + 1
        )
        df_sorted['consec_days'] = df_sorted['consec_days'].fillna(0).astype(int)

        board_daily = []
        for date, group in df_sorted.groupby('trade_date'):
            limit_up_stocks = group[group['is_limit_up']]
            consec = limit_up_stocks['consec_days'] if not limit_up_stocks.empty else pd.Series([0])

            max_height = int(consec.max()) if len(consec) > 0 else 0
            avg_height = round(consec.mean(), 2) if len(consec) > 0 and consec.max() > 0 else 0
            board_2 = int((consec >= 2).sum())
            board_3plus = int((consec >= 3).sum())

            board_daily.append({
                'trade_date': date,
                'max_board_height': max_height,
                'avg_board_height': avg_height,
                'board_2_count': board_2,
                'board_3plus_count': board_3plus,
            })

        board_df = pd.DataFrame(board_daily)
        board_df['trade_date'] = pd.to_datetime(board_df['trade_date'])

        logger.info(f"连板追踪完成: 最高连板 {board_df['max_board_height'].max()} 板")
        return board_df

    # ------------------------------------------------------------------
    # Next-day premium for limit-up stocks
    # ------------------------------------------------------------------

    def _compute_next_day_premium(self, df: pd.DataFrame) -> pd.DataFrame:
        """昨日涨停股今日平均涨跌幅（次日溢价率）"""
        logger.info("计算次日溢价...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        df_sorted['is_limit_up'] = df_sorted['pct_chg'] >= LIMIT_UP_THRESHOLD
        df_sorted['next_pct_chg'] = df_sorted.groupby('ts_code')['pct_chg'].shift(-1)

        # for each date, get stocks that were limit-up, and their NEXT day pct_chg
        lu_stocks = df_sorted[df_sorted['is_limit_up']].copy()

        premium = (
            lu_stocks.groupby('trade_date')['next_pct_chg']
            .agg(['mean', 'median', 'count'])
            .reset_index()
        )
        premium.columns = [
            'trade_date',
            'avg_return_limit_up_pct',
            'median_return_limit_up_pct',
            'yesterday_limit_up_count',
        ]
        premium['trade_date'] = pd.to_datetime(premium['trade_date'])

        # shift forward: the premium is observed on T+1, so attach to T+1's date
        # We keep it attached to T (the limit-up date) for the pipeline to use as T-1 feature
        premium['avg_return_limit_up_pct'] = premium['avg_return_limit_up_pct'].round(2)
        premium['median_return_limit_up_pct'] = premium['median_return_limit_up_pct'].round(2)

        logger.info(f"次日溢价计算完成: {len(premium)} 个交易日")
        return premium

    # ------------------------------------------------------------------
    # F8: Realized volatility (全市场实现波动率)
    # ------------------------------------------------------------------

    def _compute_realized_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        """F8: 全市场 20 日滚动收益率标准差（A股版 VIX 替代）"""
        logger.info("计算实现波动率...")

        daily_avg = df.groupby('trade_date')['pct_chg'].agg(['mean', 'std']).reset_index()
        daily_avg.columns = ['trade_date', 'market_avg_return', 'market_cross_sectional_vol']

        daily_avg = daily_avg.sort_values('trade_date')
        daily_avg['realized_vol_20d'] = (
            daily_avg['market_avg_return'].rolling(20, min_periods=5).std() * np.sqrt(250)
        ).round(4)

        daily_avg['trade_date'] = pd.to_datetime(daily_avg['trade_date'])
        logger.info(f"实现波动率: 均值 {daily_avg['realized_vol_20d'].mean():.4f}")
        return daily_avg[['trade_date', 'realized_vol_20d', 'market_cross_sectional_vol']]

    # ------------------------------------------------------------------
    # F9: Turnover anomaly (换手率异常)
    # ------------------------------------------------------------------

    def _compute_turnover_anomaly(self, df: pd.DataFrame) -> pd.DataFrame:
        """F9: 全市场当日均换手率 / 20 日均值"""
        logger.info("计算换手率异常...")

        if 'turnover' not in df.columns:
            logger.warning("无换手率字段, 跳过 F9")
            return pd.DataFrame(columns=['trade_date', 'turnover_anomaly'])

        daily_turnover = df.groupby('trade_date')['turnover'].mean().reset_index()
        daily_turnover.columns = ['trade_date', 'avg_turnover']
        daily_turnover = daily_turnover.sort_values('trade_date')

        daily_turnover['turnover_ma20'] = daily_turnover['avg_turnover'].rolling(20, min_periods=5).mean()
        daily_turnover['turnover_anomaly'] = (
            daily_turnover['avg_turnover'] / daily_turnover['turnover_ma20'].replace(0, np.nan)
        ).round(4)

        daily_turnover['trade_date'] = pd.to_datetime(daily_turnover['trade_date'])
        return daily_turnover[['trade_date', 'avg_turnover', 'turnover_anomaly']]

    # ------------------------------------------------------------------
    # F10: Volume-price divergence (量价背离)
    # ------------------------------------------------------------------

    def _compute_volume_price_divergence(self, df: pd.DataFrame) -> pd.DataFrame:
        """F10: 放量下跌股占比 — vol 超过 20 日均量 1.5 倍但 pct_chg < 0"""
        logger.info("计算量价背离...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        df_sorted['vol_ma20'] = df_sorted.groupby('ts_code')['vol'].transform(
            lambda x: x.rolling(20, min_periods=5).mean()
        )
        df_sorted['vol_ratio'] = df_sorted['vol'] / df_sorted['vol_ma20'].replace(0, np.nan)

        df_sorted['is_vol_up_price_down'] = (df_sorted['vol_ratio'] > 1.5) & (df_sorted['pct_chg'] < 0)

        daily = df_sorted.groupby('trade_date').agg(
            vol_price_diverge_count=('is_vol_up_price_down', 'sum'),
            total=('ts_code', 'count'),
        ).reset_index()
        daily['vol_price_diverge_pct'] = (daily['vol_price_diverge_count'] / daily['total'] * 100).round(2)

        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        return daily[['trade_date', 'vol_price_diverge_count', 'vol_price_diverge_pct']]

    # ------------------------------------------------------------------
    # F11: Gap frequency (跳空缺口)
    # ------------------------------------------------------------------

    def _compute_gap_frequency(self, df: pd.DataFrame) -> pd.DataFrame:
        """F11: 当日 open vs 昨日 close 的缺口均值（衡量隔夜恐慌/狂热）"""
        logger.info("计算跳空缺口...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        df_sorted['prev_close'] = df_sorted.groupby('ts_code')['close'].shift(1)
        df_sorted['gap_pct'] = (
            (df_sorted['open'] - df_sorted['prev_close']) / df_sorted['prev_close'].replace(0, np.nan) * 100
        )

        daily = df_sorted.dropna(subset=['gap_pct']).groupby('trade_date').agg(
            avg_gap_pct=('gap_pct', 'mean'),
            gap_down_count=('gap_pct', lambda x: (x < -1).sum()),
            gap_up_count=('gap_pct', lambda x: (x > 1).sum()),
        ).reset_index()
        daily['avg_gap_pct'] = daily['avg_gap_pct'].round(4)

        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        return daily[['trade_date', 'avg_gap_pct', 'gap_down_count', 'gap_up_count']]

    # ------------------------------------------------------------------
    # F12: Market range (市场振幅)
    # ------------------------------------------------------------------

    def _compute_market_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """F12: 全市场 (high-low)/close 均值 — 日内波动剧烈度"""
        logger.info("计算市场振幅...")

        df_copy = df.copy()
        df_copy['intraday_range'] = (df_copy['high'] - df_copy['low']) / df_copy['close'].replace(0, np.nan) * 100

        daily = df_copy.groupby('trade_date')['intraday_range'].agg(['mean', 'median']).reset_index()
        daily.columns = ['trade_date', 'avg_intraday_range', 'median_intraday_range']
        daily['avg_intraday_range'] = daily['avg_intraday_range'].round(4)
        daily['median_intraday_range'] = daily['median_intraday_range'].round(4)

        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        return daily

    # ------------------------------------------------------------------
    # F13: RSI extreme ratio (RSI 极端占比)
    # ------------------------------------------------------------------

    def _compute_rsi_extreme_ratio(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """F13: RSI > 80 或 RSI < 20 的股票占比"""
        logger.info("计算 RSI 极端占比...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        delta = df_sorted.groupby('ts_code')['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        avg_gain = gain.groupby(df_sorted['ts_code']).transform(
            lambda x: x.rolling(period, min_periods=period).mean()
        )
        avg_loss = loss.groupby(df_sorted['ts_code']).transform(
            lambda x: x.rolling(period, min_periods=period).mean()
        )
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df_sorted['rsi'] = (100 - 100 / (1 + rs))

        df_sorted['rsi_overbought'] = df_sorted['rsi'] > 80
        df_sorted['rsi_oversold'] = df_sorted['rsi'] < 20

        daily = df_sorted.dropna(subset=['rsi']).groupby('trade_date').agg(
            rsi_overbought_pct=('rsi_overbought', 'mean'),
            rsi_oversold_pct=('rsi_oversold', 'mean'),
            total_with_rsi=('rsi', 'count'),
        ).reset_index()

        daily['rsi_overbought_pct'] = (daily['rsi_overbought_pct'] * 100).round(2)
        daily['rsi_oversold_pct'] = (daily['rsi_oversold_pct'] * 100).round(2)
        daily['rsi_extreme_pct'] = daily['rsi_overbought_pct'] + daily['rsi_oversold_pct']

        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        return daily[['trade_date', 'rsi_overbought_pct', 'rsi_oversold_pct', 'rsi_extreme_pct']]

    # ------------------------------------------------------------------
    # F19: Sector concentration (行业集中度 — 从日线数据计算)
    # ------------------------------------------------------------------

    def _compute_sector_concentration(self, df: pd.DataFrame) -> pd.DataFrame:
        """F19: 板块涨跌集中度 — 按股票代码前缀分组, 看涨跌是否集中于少数板块"""
        logger.info("计算行业集中度...")

        df_copy = df.copy()
        df_copy['sector'] = df_copy['ts_code'].str[:3]

        daily_records = []
        for date, group in df_copy.groupby('trade_date'):
            sector_ret = group.groupby('sector')['pct_chg'].mean()
            top3_ret = sector_ret.nlargest(3).mean()
            bottom3_ret = sector_ret.nsmallest(3).mean()
            sector_std = sector_ret.std()

            daily_records.append({
                'trade_date': date,
                'sector_dispersion': round(sector_std, 4),
                'sector_top3_avg_ret': round(top3_ret, 4),
                'sector_bottom3_avg_ret': round(bottom3_ret, 4),
                'sector_spread': round(top3_ret - bottom3_ret, 4),
            })

        result = pd.DataFrame(daily_records)
        result['trade_date'] = pd.to_datetime(result['trade_date'])
        return result

    # ------------------------------------------------------------------
    # F20: New high / new low ratio (创新高/创新低比)
    # ------------------------------------------------------------------

    def _compute_new_high_low(self, df: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
        """F20: 创 60 日新高/新低股票数及比值"""
        logger.info("计算创新高/创新低...")

        df_sorted = df.sort_values(['ts_code', 'trade_date']).copy()
        df_sorted['rolling_high'] = df_sorted.groupby('ts_code')['close'].transform(
            lambda x: x.rolling(lookback, min_periods=20).max()
        )
        df_sorted['rolling_low'] = df_sorted.groupby('ts_code')['close'].transform(
            lambda x: x.rolling(lookback, min_periods=20).min()
        )

        df_sorted['is_new_high'] = df_sorted['close'] >= df_sorted['rolling_high']
        df_sorted['is_new_low'] = df_sorted['close'] <= df_sorted['rolling_low']

        daily = df_sorted.dropna(subset=['rolling_high']).groupby('trade_date').agg(
            new_high_count=('is_new_high', 'sum'),
            new_low_count=('is_new_low', 'sum'),
            total=('ts_code', 'count'),
        ).reset_index()

        daily['new_high_pct'] = (daily['new_high_count'] / daily['total'] * 100).round(2)
        daily['new_low_pct'] = (daily['new_low_count'] / daily['total'] * 100).round(2)
        daily['high_low_ratio'] = (
            daily['new_high_count'] / daily['new_low_count'].replace(0, 1)
        ).round(4)

        daily['trade_date'] = pd.to_datetime(daily['trade_date'])
        return daily[['trade_date', 'new_high_count', 'new_low_count',
                       'new_high_pct', 'new_low_pct', 'high_low_ratio']]

    # ------------------------------------------------------------------
    # F15: Margin balance change (融资余额变化率)
    # ------------------------------------------------------------------

    def _load_margin_data(self) -> pd.DataFrame:
        """F15: 加载融资融券数据"""
        margin_path = self.data_dir / 'margin_data.csv'
        if not margin_path.exists():
            logger.warning("无融资融券数据, 跳过 F15")
            return pd.DataFrame(columns=['trade_date'])

        logger.info("加载融资融券数据...")
        df = pd.read_csv(margin_path)
        df['trade_date'] = pd.to_datetime(df['trade_date'])

        cols = ['trade_date']
        for c in ['margin_balance', 'margin_buy', 'margin_balance_chg_pct', 'total_margin_balance']:
            if c in df.columns:
                cols.append(c)

        logger.info(f"融资融券: {len(df)} 天")
        return df[cols]

    # ------------------------------------------------------------------
    # Build all
    # ------------------------------------------------------------------

    def build(self, start_date: str = None, end_date: str = None,
              save: bool = True) -> pd.DataFrame:
        """
        构建完整的历史情绪生态特征表

        Returns:
            完整生态特征 DataFrame
        """
        df = self._load_stock_data()

        if start_date:
            df = df[df['trade_date'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['trade_date'] <= pd.to_datetime(end_date)]

        ecology = self._compute_daily_ecology(df)
        board = self._compute_board_tracking(df)
        premium = self._compute_next_day_premium(df)
        margin = self._load_margin_data()
        vol = self._compute_realized_volatility(df)
        turnover = self._compute_turnover_anomaly(df)
        diverge = self._compute_volume_price_divergence(df)
        gap = self._compute_gap_frequency(df)
        mkt_range = self._compute_market_range(df)
        rsi = self._compute_rsi_extreme_ratio(df)
        sector = self._compute_sector_concentration(df)
        new_hl = self._compute_new_high_low(df)

        result = ecology.merge(board, on='trade_date', how='left')
        result = result.merge(premium, on='trade_date', how='left')
        for extra in [vol, turnover, diverge, gap, mkt_range, rsi, sector, new_hl, margin]:
            if not extra.empty:
                result = result.merge(extra, on='trade_date', how='left')

        result = result.fillna(0)
        result = result.sort_values('trade_date').reset_index(drop=True)

        if save:
            result.to_csv(self.ecology_cache_path, index=False, encoding='utf-8-sig')
            logger.info(f"生态缓存已保存: {self.ecology_cache_path}")
            logger.info(f"  交易日数: {len(result)}")
            logger.info(f"  日期范围: {result['trade_date'].min()} ~ {result['trade_date'].max()}")

        self._ecology_df = result
        return result

    # ------------------------------------------------------------------
    # Load cache
    # ------------------------------------------------------------------

    def load_ecology(self) -> pd.DataFrame:
        """读取生态特征缓存，无缓存则自动构建"""
        if self._ecology_df is not None:
            return self._ecology_df

        if self.ecology_cache_path.exists():
            size_kb = self.ecology_cache_path.stat().st_size / 1024
            if size_kb > 1:  # skip tiny test data
                df = pd.read_csv(self.ecology_cache_path)
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                if 'limit_up_count' in df.columns and len(df) > 10:
                    self._ecology_df = df
                    logger.info(f"从缓存加载生态特征: {len(df)} 天")
                    return df

        logger.info("缓存不存在或过小, 自动构建...")
        return self.build()


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description='历史情绪生态特征构建器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python -m emotion_validation.data_layer.historical_ecology_builder --build
    python -m emotion_validation.data_layer.historical_ecology_builder --build --start-date 20230101
'''
    )

    parser.add_argument('--build', action='store_true', help='构建生态特征')
    parser.add_argument('--start-date', default=None, help='起始日期')
    parser.add_argument('--end-date', default=None, help='截止日期')
    parser.add_argument('--data-dir', default=None, help='数据目录')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
    )

    builder = HistoricalEcologyBuilder(data_dir=args.data_dir)

    if args.build:
        ecology = builder.build(start_date=args.start_date, end_date=args.end_date)
        print(f"\n=== 生态特征构建完成 ===")
        print(f"交易日数: {len(ecology)}")
        print(f"日期范围: {ecology['trade_date'].min()} ~ {ecology['trade_date'].max()}")
        print(f"\n特征概览:")
        print(ecology.describe().to_string())
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
