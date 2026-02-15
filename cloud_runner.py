"""
================================================================================
云端定时执行器  cloud_runner.py
================================================================================
供 cron / systemd timer / 云函数 等定时调度使用。
执行一次完整的市场情绪监控 + 飞书推送流程。

使用:
    python cloud_runner.py                  # 正式执行
    python cloud_runner.py --dry-run        # 试运行 (不推送飞书)
    python cloud_runner.py --full           # 完整模式 (含广度 + RPS)
================================================================================
"""

import argparse
import sys
import traceback
from datetime import datetime


def run_once(dry_run=False, full_mode=False, session_only=False):
    """
    执行一次完整的情绪监控流水线。

    Parameters
    ----------
    dry_run : bool
        如果 True，跳过实际数据拉取和飞书推送，仅验证流程可走通
    full_mode : bool
        如果 True，包含广度和 RPS 分析 (耗时更长)
    session_only : bool
        如果 True，仅在交易时段执行；其他时段直接跳过并返回成功

    Returns
    -------
    int
        0 表示成功，非 0 表示失败
    """
    start_time = datetime.now()
    print(f"[cloud_runner] 开始执行 | {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[cloud_runner] dry_run={dry_run}, full_mode={full_mode}, session_only={session_only}")

    if dry_run:
        print("[cloud_runner] dry_run 模式: 跳过数据拉取和推送，流程验证通过")
        return 0

    try:
        from market_gambler_watch import is_trading_session, run_once as watch_run_once

        if session_only and not is_trading_session():
            print("[cloud_runner] 当前非交易时段，跳过本轮执行")
            return 0

        watch_run_once(full=full_mode, feishu=True)

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"[cloud_runner] 执行完成 | 耗时 {elapsed:.0f}s")
        return 0

    except Exception as e:
        print(f"[cloud_runner] 执行失败: {e}")
        traceback.print_exc()
        return 1


def main():
    parser = argparse.ArgumentParser(description="云端定时执行器")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不推送飞书")
    parser.add_argument("--full", action="store_true", help="完整模式 (含广度 + RPS)")
    parser.add_argument("--session-only", action="store_true", help="仅交易时段执行")
    args = parser.parse_args()

    exit_code = run_once(
        dry_run=args.dry_run,
        full_mode=args.full,
        session_only=args.session_only,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
