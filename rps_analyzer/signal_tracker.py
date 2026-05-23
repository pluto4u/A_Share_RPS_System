"""
================================================================================
选股信号连续跟踪 — 新进入 / 退出 / 持续
================================================================================
对比前后两天的选股结果 CSV，标注:
  - NEW:  今天新进入的票
  - EXIT: 昨天有、今天没有的票
  - HOLD: 连续入选的票

支持多层（L1, L2, Weekly）独立跟踪。
"""

from pathlib import Path
from typing import Optional, List, Tuple

from .codes_csv import read_codes_csv


def load_codes_from_csv(path: Path) -> set:
    """从 _codes.csv 读取代码集合（无表头单列 或 旧版 ts_code 列）"""
    if not path.exists():
        return set()
    return set(read_codes_csv(path))


def find_previous_file(
    output_dir: Path,
    current_date: str,
    prefix: str = "tdx_screener_",
    suffix: str = "_codes.csv",
) -> Optional[Path]:
    """
    找到当前日期之前最近的一个同类型选股结果文件。

    例: current_date="20260318", prefix="tdx_screener_", suffix="_codes.csv"
    -> 找 tdx_screener_20260317_codes.csv, 20260316, ... 最多回溯 10 天
    """
    from datetime import datetime, timedelta
    dt = datetime.strptime(current_date, "%Y%m%d")

    for i in range(1, 11):
        prev_dt = dt - timedelta(days=i)
        prev_date = prev_dt.strftime("%Y%m%d")
        prev_path = output_dir / f"{prefix}{prev_date}{suffix}"
        if prev_path.exists():
            return prev_path

    return None


def compare_signals(
    today_codes: set,
    prev_codes: set,
) -> dict:
    """
    对比两天的选股集合。

    返回:
      {
        "new": [...],      新进入
        "exit": [...],     退出
        "hold": [...],     持续
        "today_count": N,
        "prev_count": M,
      }
    """
    new_codes = sorted(today_codes - prev_codes)
    exit_codes = sorted(prev_codes - today_codes)
    hold_codes = sorted(today_codes & prev_codes)

    return {
        "new": new_codes,
        "exit": exit_codes,
        "hold": hold_codes,
        "today_count": len(today_codes),
        "prev_count": len(prev_codes),
    }


def track_signals(
    output_dir: Path,
    target_date: str,
    layers: Optional[List[Tuple[str, str]]] = None,
) -> dict:
    """
    对多层选股结果做连续跟踪。

    参数:
      layers: [(layer_name, suffix), ...]
        默认 [("L1", "_codes.csv"), ("L2", "_L2_codes.csv")]

    返回:
      {
        "L1": {compare_signals result + "prev_date": ...},
        "L2": {...},
      }
    """
    if layers is None:
        layers = [
            ("L1", "_codes.csv"),
            ("L2", "_L2_codes.csv"),
        ]

    results = {}
    for layer_name, suffix in layers:
        today_path = output_dir / f"tdx_screener_{target_date}{suffix}"
        today_codes = load_codes_from_csv(today_path)

        prev_path = find_previous_file(output_dir, target_date, suffix=suffix)
        if prev_path:
            prev_codes = load_codes_from_csv(prev_path)
            prev_date = prev_path.stem.replace("tdx_screener_", "").replace(suffix.replace(".csv", ""), "")
        else:
            prev_codes = set()
            prev_date = None

        comp = compare_signals(today_codes, prev_codes)
        comp["prev_date"] = prev_date
        results[layer_name] = comp

    return results


def print_tracking_report(tracking: dict) -> None:
    """打印跟踪报告"""
    for layer_name, comp in tracking.items():
        prev_info = f" (vs {comp['prev_date']})" if comp["prev_date"] else " (无前日数据)"
        print(f"\n{'='*60}")
        print(f"[{layer_name}] 信号跟踪{prev_info}")
        print(f"{'='*60}")
        print(f"  今日: {comp['today_count']} 只  |  前日: {comp['prev_count']} 只")
        print(f"  新进入: {len(comp['new'])} 只  |  退出: {len(comp['exit'])} 只  |  持续: {len(comp['hold'])} 只")

        if comp["new"]:
            print(f"\n  [NEW] 新进入:")
            for i, code in enumerate(comp["new"]):
                end = "\n" if (i + 1) % 8 == 0 else ""
                print(f"  {code}", end=end)
            print()

        if comp["exit"]:
            print(f"\n  [EXIT] 退出:")
            for i, code in enumerate(comp["exit"]):
                end = "\n" if (i + 1) % 8 == 0 else ""
                print(f"  {code}", end=end)
            print()
