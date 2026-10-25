"""每日复盘总控：数据新鲜度检查 → 看板 →（可选）前瞻账本 → 小红书素材。适合手动运行，也适合定时任务。

    python scripts/daily/run_daily_review.py                       # 用通达信最新交易日，手动运行
    python scripts/daily/run_daily_review.py --require-today       # 定时任务用：必须是今天的收盘数据
    python scripts/daily/run_daily_review.py --date 2026-10-23 --force
    python scripts/daily/run_daily_review.py --with-forward-log    # 同时记录前瞻账本（首次运行会冻结规则）

退出码：0 完成；1 某一步失败；2 数据没准备好（未收盘、通达信还没下载当天日线，或休市）。
已完成的步骤会跳过（除非 --force），所以失败后可以直接重跑。不发布任何内容。
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import LEGACY_OUTPUT_DIR, OUTPUT_ROOT, PROJECT_ROOT

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
CLOSE_READY = dtime(15, 30)   # 收盘后才认为当天日线是完整的
POLL_SECONDS = 300


def latest_tdx_session(tdx_root: Path) -> date:
    raw = (tdx_root / "vipdoc/sh/lday/sh000001.day").read_bytes()[-32:]
    return datetime.strptime(str(int.from_bytes(raw[:4], "little")), "%Y%m%d").date()


def decide(now: datetime, latest: date, require_today: bool, explicit: date | None) -> tuple[str, date | None, str]:
    """返回 (动作, 目标日期, 说明)。动作：run / wait / skip / stop。纯函数，便于测试。"""
    if explicit is not None:
        if explicit > latest:
            return "stop", None, f"通达信数据只到 {latest}，没有 {explicit}"
        return "run", explicit, f"指定日期 {explicit}"
    if not require_today:
        return "run", latest, f"使用通达信最新交易日 {latest}"
    today = now.date()
    if today.weekday() >= 5:
        return "skip", None, f"{today} 是周末，无需复盘"
    if now.time() < CLOSE_READY:
        return "wait", None, f"现在 {now:%H:%M}，尚未到 15:30，当天日线还不完整"
    if latest < today:
        return "wait", None, f"通达信最新交易日是 {latest}，还没有 {today} 的日线（尚未下载，或今天休市）"
    return "run", latest, f"今天 {today} 的日线已就绪"


class Runner:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def say(self, msg: str) -> None:
        line = f"[{datetime.now(TZ):%H:%M:%S}] {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def step(self, name: str, argv: list[str]) -> bool:
        self.say(f"开始：{name}")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONWARNINGS": "ignore"}
        t0 = time.time()
        with self.log_path.open("a", encoding="utf-8") as fh:
            p = subprocess.run([sys.executable, *argv], cwd=PROJECT_ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT)
        ok = p.returncode == 0
        self.say(f"{'完成' if ok else '失败(退出码 %d，详见日志)' % p.returncode}：{name}，用时 {time.time() - t0:.0f} 秒")
        return ok


def exists(name: str) -> bool:
    return (OUTPUT_ROOT / name).exists() or (LEGACY_OUTPUT_DIR / name).exists()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--require-today", action="store_true", help="必须是今天收盘后的数据（定时任务用）")
    ap.add_argument("--wait-minutes", type=int, default=0, help="数据没准备好时最多等待几分钟，每5分钟检查一次")
    ap.add_argument("--with-forward-log", action="store_true", help="同时运行前瞻账本（首次运行会冻结规则）")
    ap.add_argument("--force", action="store_true", help="已完成的步骤也重新运行")
    ap.add_argument("--open", action="store_true", help="完成后打开素材文件夹")
    args = ap.parse_args()

    explicit = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    deadline = time.time() + args.wait_minutes * 60
    log = Runner(OUTPUT_ROOT / "logs" / "daily_review" / f"{datetime.now(TZ):%Y%m%d}.log")
    while True:
        action, target, msg = decide(datetime.now(TZ), latest_tdx_session(args.tdx_root), args.require_today, explicit)
        log.say(msg)
        if action == "run":
            break
        if action == "skip":
            return 0
        if action == "stop" or time.time() + POLL_SECONDS > deadline:
            log.say("数据没有准备好，本次不运行。" + ("请先在通达信里下载当天日线再重跑。" if action == "wait" else ""))
            return 2
        log.say(f"{POLL_SECONDS // 60} 分钟后重试")
        time.sleep(POLL_SECONDS)

    tag, iso = f"{target:%Y%m%d}", f"{target:%Y-%m-%d}"
    post = OUTPUT_ROOT / "xhs" / tag / "post.md"
    if not args.force and post.exists() and (not args.with_forward_log):
        log.say(f"{iso} 的复盘素材已存在：{post.parent}（加 --force 重新生成）")
        return 0

    steps: list[tuple[str, list[str], bool]] = [
        ("每日综合看板", ["scripts/daily/run_daily_board.py", "--date", iso], args.force or not exists(f"daily_board_{tag}.json")),
    ]
    if args.with_forward_log:
        steps.append(("前瞻账本（只记账不下单）", ["scripts/daily/run_forward_log.py"], True))
    steps.append(("小红书素材", ["scripts/reporting/xhs_daily_post.py", "--date", iso], args.force or not post.exists()))
    for name, argv, needed in steps:
        if not needed:
            log.say(f"跳过（已完成）：{name}")
            continue
        if not log.step(name, argv):
            return 1
    log.say(f"全部完成。素材：{post.parent}")
    log.say(f"看板：{OUTPUT_ROOT / f'daily_board_{tag}.html'}")
    if args.open and hasattr(os, "startfile"):
        os.startfile(post.parent)  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
