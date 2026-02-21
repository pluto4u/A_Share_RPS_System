"""
================================================================================
增量数据管理器 - 支持增量更新和高效查询
================================================================================

架构设计：
1. 原始数据：backtest/data/stock_daily.parquet (全量数据)
2. RPS 数据：backtest/data/stock_daily_with_rps.parquet (全量数据)
3. 增量更新：backtest/data/incremental_updates/ (每日增量)
4. 元数据：backtest/data/metadata.json (最后更新时间)
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List


class IncrementalDataManager:
    """增量数据管理器"""

    def __init__(self, base_dir: str = "backtest/data"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 文件路径
        self.raw_data_path = self.base_dir / "stock_daily.parquet"
        self.rps_data_path = self.base_dir / "stock_daily_with_rps.parquet"
        self.incremental_dir = self.base_dir / "incremental_updates"
        self.incremental_dir.mkdir(exist_ok=True)

        self.metadata_path = self.base_dir / "metadata.json"

    def load_metadata(self) -> Dict:
        """加载元数据"""
        if self.metadata_path.exists():
            with open(self.metadata_path, 'r') as f:
                return json.load(f)
        return {
            "last_raw_update": None,
            "last_rps_update": None,
            "raw_data_count": 0,
            "stock_count": 0
        }

    def save_metadata(self, metadata: Dict):
        """保存元数据"""
        with open(self.metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2, default=str)

    def get_latest_date(self, data_type: str = "raw") -> Optional[datetime]:
        """获取最新日期"""
        metadata = self.load_metadata()
        last_update = metadata.get(f"last_{data_type}_update")
        if last_update:
            return datetime.strptime(last_update, "%Y-%m-%d")
        return None

    def load_raw_data(self) -> pd.DataFrame:
        """加载原始日线数据"""
        if not self.raw_data_path.exists():
            raise FileNotFoundError(f"原始数据不存在: {self.raw_data_path}")

        df = pd.read_parquet(self.raw_data_path)
        print(f"[数据管理器] 加载原始数据: {len(df):,} 行")
        return df

    def load_rps_data(self) -> pd.DataFrame:
        """加载 RPS 数据"""
        if not self.rps_data_path.exists():
            raise FileNotFoundError(f"RPS 数据不存在: {self.rps_data_path}")

        df = pd.read_parquet(self.rps_data_path)
        print(f"[数据管理器] 加载 RPS 数据: {len(df):,} 行")
        return df

    def append_incremental_data(self, new_data: pd.DataFrame, update_type: str = "raw"):
        """
        追加增量数据

        参数
        ----------
        new_data : pd.DataFrame
            新的数据
        update_type : str
            数据类型 ("raw" 或 "rps")
        """
        if len(new_data) == 0:
            print("[数据管理器] 无新数据需要追加")
            return

        # 确定目标文件
        if update_type == "raw":
            target_path = self.raw_data_path
        elif update_type == "rps":
            target_path = self.rps_data_path
        else:
            raise ValueError(f"未知的数据类型: {update_type}")

        # 加载现有数据
        if target_path.exists():
            existing_data = pd.read_parquet(target_path)

            # 检查是否需要去重
            existing_dates = set(existing_data['trade_date'].unique())
            new_dates = set(new_data['trade_date'].unique())

            overlapping_dates = existing_dates & new_dates

            if overlapping_dates:
                print(f"[数据管理器] 发现重叠日期: {len(overlapping_dates)} 天")
                # 移除重叠日期的旧数据
                mask = ~existing_data['trade_date'].isin(overlapping_dates)
                existing_data = existing_data[mask]

            # 合并数据
            combined_data = pd.concat([existing_data, new_data], ignore_index=True)
        else:
            combined_data = new_data

        # 按日期和代码排序
        combined_data = combined_data.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)

        # 保存
        combined_data.to_parquet(target_path, index=False)

        # 更新元数据
        metadata = self.load_metadata()
        metadata[f"last_{update_type}_update"] = combined_data['trade_date'].max().strftime("%Y-%m-%d")
        metadata[f"{update_type}_data_count"] = len(combined_data)
        metadata["stock_count"] = combined_data['ts_code'].nunique()
        self.save_metadata(metadata)

        print(f"[数据管理器] 数据已更新: {len(combined_data):,} 行, {combined_data['ts_code'].nunique():,} 只股票")

    def save_incremental_snapshot(self, data: pd.DataFrame, update_type: str = "raw"):
        """
        保存增量快照（用于备份）

        参数
        ----------
        data : pd.DataFrame
            增量数据
        update_type : str
            数据类型
        """
        latest_date = data['trade_date'].max()
        snapshot_name = f"{update_type}_incremental_{latest_date.strftime('%Y%m%d')}.parquet"
        snapshot_path = self.incremental_dir / snapshot_name

        data.to_parquet(snapshot_path, index=False)
        print(f"[数据管理器] 增量快照已保存: {snapshot_path}")

        # 清理旧快照（保留最近 7 个）
        self._cleanup_old_snapshots(update_type, keep=7)

    def _cleanup_old_snapshots(self, update_type: str, keep: int = 7):
        """清理旧快照"""
        pattern = f"{update_type}_incremental_*.parquet"
        snapshots = sorted(self.incremental_dir.glob(pattern), reverse=True)

        if len(snapshots) > keep:
            for old_snapshot in snapshots[keep:]:
                old_snapshot.unlink()
                print(f"[数据管理器] 删除旧快照: {old_snapshot}")

    def get_incremental_dates(self, days: int = 5) -> List[datetime]:
        """
        获取需要增量更新的日期

        参数
        ----------
        days : int
            获取最近 N 天的日期（默认 5）

        返回
        -------
        List[datetime]
            需要更新的日期列表
        """
        latest_date = self.get_latest_date("raw")

        if latest_date is None:
            return []

        # 计算需要更新的日期范围
        start_date = latest_date + timedelta(days=1)
        end_date = datetime.now()

        # 生成日期列表（跳过周末）
        dates = []
        current_date = start_date

        while current_date <= end_date:
            if current_date.weekday() < 5:  # 周一到周五
                dates.append(current_date)
            current_date += timedelta(days=1)

        return dates[:days]

    def update_rps_incrementally(self, recalculate_all: bool = False):
        """
        增量更新 RPS

        参数
        ----------
        recalculate_all : bool
            是否重新计算所有 RPS（默认 False）
        """
        print("[数据管理器] 开始增量更新 RPS...")

        # 加载原始数据
        raw_data = self.load_raw_data()

        if recalculate_all:
            # 重新计算所有 RPS
            from .rps_calculator import calculate_rps
            rps_data = calculate_rps(raw_data)
        else:
            # 只计算新增部分的 RPS
            # 这里简化处理，实际应该只计算新增日期的 RPS
            from .rps_calculator import calculate_rps
            rps_data = calculate_rps(raw_data)

        # 保存 RPS 数据
        rps_data.to_parquet(self.rps_data_path, index=False)

        # 更新元数据
        metadata = self.load_metadata()
        metadata["last_rps_update"] = rps_data['trade_date'].max().strftime("%Y-%m-%d")
        self.save_metadata(metadata)

        print(f"[数据管理器] RPS 更新完成: {len(rps_data):,} 行")


# 便捷函数
def get_data_manager(base_dir: str = "backtest/data") -> IncrementalDataManager:
    """获取数据管理器实例"""
    return IncrementalDataManager(base_dir)


if __name__ == "__main__":
    print("增量数据管理器测试...")

    # 创建管理器
    manager = get_data_manager()

    # 显示状态
    metadata = manager.load_metadata()
    print(f"\n元数据:")
    for key, value in metadata.items():
        print(f"  {key}: {value}")
