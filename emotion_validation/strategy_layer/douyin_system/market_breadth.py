import pandas as pd
from pathlib import Path
from typing import Dict, Optional


class MarketBreadth:
    def __init__(self, stock_daily_path: str = 'backtest/data/stock_daily.csv'):
        self.stock_daily_path = stock_daily_path
        self.breadth_cache: Dict[str, Dict] = {}
        
    def load_data(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        path = Path(self.stock_daily_path)
        if path.suffix == '.parquet':
            df = pd.read_parquet(self.stock_daily_path)
        else:
            df = pd.read_csv(self.stock_daily_path)

        df['trade_date'] = pd.to_datetime(df['trade_date'])

        if start_date:
            df = df[df.trade_date >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.trade_date <= pd.Timestamp(end_date)]
        return df
    
    def calculate_daily_breadth(self, df: pd.DataFrame) -> pd.DataFrame:
        grouped = df.groupby('trade_date').agg(
            total_count=('ts_code', 'count'),
            up_count=('pct_chg', lambda x: (x > 0).sum()),
            down_count=('pct_chg', lambda x: (x < 0).sum()),
            flat_count=('pct_chg', lambda x: (x == 0).sum()),
        ).reset_index()
        return grouped
    
    def load_ecology_cache(self) -> pd.DataFrame:
        ecology_path = Path(self.stock_daily_path).parent / 'ecology_cache.csv'
        ecology_df = pd.read_csv(ecology_path)
        ecology_df['trade_date'] = pd.to_datetime(ecology_df['trade_date'])
        return ecology_df
    
    def merge_with_ecology(self, breadth_df: pd.DataFrame, ecology_df: pd.DataFrame) -> pd.DataFrame:
        merged = breadth_df.merge(
            ecology_df[['trade_date', 'limit_up_count', 'limit_down_count', 'attempt_limit_up_count']],
            on='trade_date',
            how='left'
        )
        return merged
    
    def compute_signals(self, merged_df: pd.DataFrame) -> pd.DataFrame:
        merged_df['is_buy_eligible'] = merged_df['up_count'] >= 3000
        merged_df['is_half_risk'] = merged_df['down_count'] >= 3000
        merged_df['is_full_risk'] = merged_df['down_count'] >= 4000
        return merged_df
    
    def get_daily_signal(self, date: str) -> Dict:
        if date not in self.breadth_cache:
            self.compute_all_breadth()
        return self.breadth_cache.get(date, {})
    
    def compute_all_breadth(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        df = self.load_data(start_date, end_date)
        breadth_df = self.calculate_daily_breadth(df)
        ecology_df = self.load_ecology_cache()
        merged_df = self.merge_with_ecology(breadth_df, ecology_df)
        result_df = self.compute_signals(merged_df)
        
        for _, row in result_df.iterrows():
            date_str = row['trade_date'].strftime('%Y-%m-%d')
            self.breadth_cache[date_str] = {
                'up_count': int(row['up_count']),
                'down_count': int(row['down_count']),
                'flat_count': int(row['flat_count']),
                'limit_up_count': int(row.get('limit_up_count', 0)),
                'limit_down_count': int(row.get('limit_down_count', 0)),
                'is_buy_eligible': bool(row['is_buy_eligible']),
                'is_half_risk': bool(row['is_half_risk']),
                'is_full_risk': bool(row['is_full_risk']),
            }
        
        return result_df
    
    def get_date_range(self, start_date: str, end_date: str) -> pd.DataFrame:
        return self.compute_all_breadth(start_date, end_date)
