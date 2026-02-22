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
    python cloud_runner.py --pipeline       # 全量量化流水线
    python cloud_runner.py --pipeline --tos # 全量流水线 + TOS 数据同步
================================================================================
"""

import argparse
import sys
import traceback
from datetime import datetime


def _tos_sync_before_pipeline():
    """流水线执行前: 从 TOS 增量下载最新数据"""
    try:
        from tos_storage import TosStorage
        storage = TosStorage()
        print(f"[cloud_runner] TOS 同步 (角色={storage.role}): 下载远端数据...")
        stats = storage.sync_download()
        print(f"[cloud_runner] TOS 下载完成: {stats['downloaded']} 文件, "
              f"{stats['total_mb']:.1f} MB, 跳过: {stats['skipped']}")
    except Exception as e:
        print(f"[cloud_runner] TOS 下载失败 (非致命，继续执行): {e}")


def _tos_sync_after_pipeline():
    """流水线执行后: 将更新的数据增量上传到 TOS (cloud_ecs 身份自动跳过冲突检测)"""
    try:
        from tos_storage import TosStorage
        storage = TosStorage()
        print(f"[cloud_runner] TOS 同步 (角色={storage.role}): 上传更新数据...")
        stats = storage.sync_upload(force=True)
        print(f"[cloud_runner] TOS 上传完成: {stats['uploaded']} 文件, "
              f"{stats['total_mb']:.1f} MB, 跳过: {stats['skipped']}")
    except Exception as e:
        print(f"[cloud_runner] TOS 上传失败 (非致命): {e}")


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
    parser.add_argument("--pipeline", action="store_true", help="触发完整的量化计算（更新数据、情绪分、轮动及策略执行）")
    parser.add_argument("--tos", action="store_true", help="流水线前后自动同步 TOS 数据（需配置 tos_config.json）")
    args = parser.parse_args()

    # 如果是全流程跑批指令
    if args.pipeline:
        print(f"[cloud_runner] 触发全量量化流水线 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        import subprocess

        # TOS 同步: 流水线开始前从 TOS 下载最新数据
        if args.tos:
            _tos_sync_before_pipeline()

        try:
            # 1. 获取全A日线及16因子计算 (耗时30秒-2分钟)
            subprocess.run([sys.executable, "-m", "emotion_validation.pipeline"], check=True)
            # 2. 更新 ETF 池日线数据 (耗时15秒)
            subprocess.run([sys.executable, "-c", "from emotion_validation.etf_rotation.etf_data import ETFDataManager; ETFDataManager().fetch_all()"], check=True)
            # 3. 运行回测与 5/10日极速动量轮动筛选 (耗时10秒)
            subprocess.run([sys.executable, "-m", "emotion_validation.etf_rotation.backtest_rotation"], check=True)
            # 4. 生成今日交易指令与S5判定 (包含紧急情况防守)
            with open("daily_signal_output.txt", "w", encoding="utf-8") as f:
                subprocess.run([sys.executable, "daily_signal.py"], stdout=f, stderr=subprocess.STDOUT, check=True)
            
            # 5. 推送飞书
            from feishu_bot import FeishuBot
            with open("daily_signal_output.txt", "r", encoding="utf-8") as f:
                msg = f.read()
            bot = FeishuBot()
            bot.send_text(msg)

            # TOS 同步: 流水线完成后将更新的数据上传 TOS
            if args.tos:
                _tos_sync_after_pipeline()

            print("[cloud_runner] 全量量化流水线执行成功并已推送")
            return 0
        except Exception as e:
            print(f"[cloud_runner] 流水线执行失败: {e}")
            from feishu_bot import FeishuBot
            FeishuBot().send_text(f"🚨 量化流水线执行失败:\n{str(e)}")
            return 1

    exit_code = run_once(
        dry_run=args.dry_run,
        full_mode=args.full,
        session_only=args.session_only,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
