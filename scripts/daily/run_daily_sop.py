"""
================================================================================
每日 SOP 串联入口
================================================================================
先跑 RPS 强势池扫描，再接持仓体检 / 候选排队；
若 --date 是周五（或显式 --with-weekly），自动补跑本周持续性 + 核心候选形态过滤。

用法:
    py -3 run_daily_sop.py --date 20260520
    py -3 run_daily_sop.py --date 20260522                  # 周五自动跑周收尾
    py -3 run_daily_sop.py --date 20260520 --with-weekly    # 强制跑周收尾
    py -3 run_daily_sop.py --date 20260522 --skip-portfolio-check
================================================================================
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR

OUTPUT_DIR = Path("output")


def _run_step(title: str, cmd: list[str]) -> int:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)
    print(" ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode


def _week_window(target_date: str) -> tuple[str, str]:
    """返回包含 target_date 的本周一 ~ 本周五"""
    dt = datetime.strptime(target_date, "%Y%m%d")
    monday = dt - timedelta(days=dt.weekday())
    friday = monday + timedelta(days=4)
    return monday.strftime("%Y%m%d"), friday.strftime("%Y%m%d")


def _count_codes(path: Path, missing_value: int = -1) -> int:
    """统计 codes.csv 内的代码数（无表头）。"""
    if not path.exists():
        return missing_value
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        return len(lines)
    except OSError:
        return -1


def _classify_temperature(
    n_l1: int, n_l2: int, n_super: int, n_huoche_first: int,
    n_huoche_first_prev: int = -1,
) -> tuple[str, str, list[str]]:
    """根据当日分层数判断市场温度。

    返回 (temperature, recommendation, hints)
    """
    hints: list[str] = []
    if n_l1 <= 0:
        return ("unknown", "数据不足，建议手动检查", ["L1 为空, 无法计算温度比"])

    ratio = n_l2 / n_l1
    hints.append(f"L2/L1 = {n_l2}/{n_l1} = {ratio:.2f}")
    hints.append(f"Super = {n_super}")
    hints.append(f"Huoche_First = {n_huoche_first}")

    # overheat 触发条件
    huoche_dead_2w = (
        n_huoche_first == 0
        and n_huoche_first_prev >= 0
        and n_huoche_first_prev == 0
    )
    if ratio >= 0.75 or huoche_dead_2w:
        temp = "overheat"
        rec = "仅看 S 档；无 S 就空仓 / 减半，等下周新血"
        if huoche_dead_2w:
            hints.append("注意: huoche_first 连续 2 周为 0")
    elif ratio >= 0.55:
        temp = "hot"
        rec = "仅 S 档建仓；A 档仅作观察"
    elif ratio >= 0.35:
        temp = "normal"
        rec = "S + A 档可建仓"
    else:
        temp = "cold"
        rec = "S + A + B 档全可，可适度加大 B 档观察转入"

    if n_super < 3 and temp != "overheat":
        # 选股层稀薄通常意味着真冷市
        hints.append(f"Super 数量低 (<3), 降级为 cold")
        temp = "cold"
        rec = "S + A + B 档全可，但留意整体活跃度低"

    return (temp, rec, hints)


def _print_market_temperature(date: str, week_start_prev: str | None = None) -> str:
    """读 Step 1 输出, 打印市场温度. 返回温度字符串."""
    # 当日扫描成功后，某层没有生成代码文件表示该层为 0 只；
    # 历史对照文件缺失仍保留 -1，表示“无从判断”。
    n_l1 = _count_codes(OUTPUT_DIR / f"tdx_screener_{date}_codes.csv", missing_value=0)
    n_l2 = _count_codes(OUTPUT_DIR / f"tdx_screener_{date}_L2_codes.csv", missing_value=0)
    n_super = _count_codes(OUTPUT_DIR / f"tdx_screener_{date}_super_codes.csv", missing_value=0)
    n_huoche_first = _count_codes(
        OUTPUT_DIR / f"tdx_screener_{date}_huoche_first_codes.csv",
        missing_value=0,
    )

    n_huoche_first_prev = -1
    if week_start_prev:
        # 找上周五的 huoche_first 数量
        prev_friday_dt = datetime.strptime(week_start_prev, "%Y%m%d") + timedelta(days=4)
        prev_friday = prev_friday_dt.strftime("%Y%m%d")
        n_huoche_first_prev = _count_codes(
            OUTPUT_DIR / f"tdx_screener_{prev_friday}_huoche_first_codes.csv"
        )

    temp, rec, hints = _classify_temperature(
        n_l1, n_l2, n_super, n_huoche_first, n_huoche_first_prev,
    )

    print("\n" + "-" * 70)
    print(f"[市场温度] {temp.upper()}")
    print("-" * 70)
    for h in hints:
        print(f"  {h}")
    print(f"  建议: {rec}")
    print("-" * 70)
    return temp


def main() -> None:
    parser = argparse.ArgumentParser(description="每日 RPS 强势池 + SOP 持仓体检串联入口")
    parser.add_argument("--date", required=True, help="目标日期 YYYYMMDD")
    parser.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 路径，或 sh/sz lday 路径")
    parser.add_argument("--min-days", type=int, default=250, help="最少交易天数")
    parser.add_argument("--price-basis", choices=("qfq", "raw"), default="qfq",
                        help="传给 run_tdx_screener.py 的价格口径")

    parser.add_argument("--portfolio", default="portfolio.csv", help="持仓 CSV，默认 portfolio.csv")
    parser.add_argument("--hold-days", type=int, default=3, help="候选 Super 连续 N 天才推荐")
    parser.add_argument("--lookback", type=int, default=21, help="信号历史回看自然日")
    parser.add_argument("--skip-portfolio-check", action="store_true", help="只跑强势池，不跑持仓体检")
    parser.add_argument("--strict-portfolio", action="store_true", help="portfolio.csv 缺失时直接失败")

    parser.add_argument("--skip-early", action="store_true", help="传给 run_tdx_screener.py")
    parser.add_argument("--skip-super", action="store_true", help="传给 run_tdx_screener.py")
    parser.add_argument("--skip-track", action="store_true", help="传给 run_tdx_screener.py")

    parser.add_argument("--with-weekly", action="store_true", help="强制跑周收尾（持续性 + 核心形态过滤）")
    parser.add_argument("--skip-weekly", action="store_true", help="即使周五也不跑周收尾")
    parser.add_argument("--super-rate", type=float, default=0.6, help="核心形态过滤 super_rate 下限")
    parser.add_argument("--layers", type=int, default=3, help="核心形态过滤 layers_covered 下限")
    args = parser.parse_args()

    py = sys.executable

    scan_cmd = [
        py,
        "run_tdx_screener.py",
        "--date",
        args.date,
        "--tdx-dir",
        args.tdx_dir,
        "--min-days",
        str(args.min_days),
        "--price-basis",
        args.price_basis,
    ]
    if args.skip_early:
        scan_cmd.append("--skip-early")
    if args.skip_super:
        scan_cmd.append("--skip-super")
    if args.skip_track:
        scan_cmd.append("--skip-track")

    rc = _run_step("Step 1: RPS 强势池扫描", scan_cmd)
    if rc != 0:
        raise SystemExit(f"RPS 扫描失败 exit={rc}")

    # 计算市场温度（基于 Step 1 输出的 codes.csv）
    target_dt = datetime.strptime(args.date, "%Y%m%d")
    monday = target_dt - timedelta(days=target_dt.weekday())
    week_prev_start = (monday - timedelta(days=7)).strftime("%Y%m%d")
    temperature = _print_market_temperature(args.date, week_start_prev=week_prev_start)

    portfolio_path = Path(args.portfolio)
    if args.skip_portfolio_check:
        print("\n[每日 SOP] 已跳过持仓体检。")
    elif not portfolio_path.exists():
        msg = (
            f"\n[每日 SOP] 持仓文件 {portfolio_path} 不存在。"
            "\n请复制 portfolio_template.csv -> portfolio.csv 并填写实际持仓后再跑持仓体检。"
        )
        if args.strict_portfolio:
            raise FileNotFoundError(msg)
        print(msg)
    else:
        check_cmd = [
            py,
            "run_portfolio_check.py",
            "--date",
            args.date,
            "--tdx-dir",
            args.tdx_dir,
            "--min-days",
            str(args.min_days),
            "--portfolio",
            args.portfolio,
            "--hold-days",
            str(args.hold_days),
            "--lookback",
            str(args.lookback),
        ]
        rc = _run_step("Step 2: SOP 持仓体检 / 候选排队", check_cmd)
        if rc != 0:
            print(f"[每日 SOP] 持仓体检 exit={rc}（继续执行后续步骤）")

    is_friday = target_dt.weekday() == 4
    run_weekly = args.with_weekly or (is_friday and not args.skip_weekly)

    if not run_weekly:
        if not args.skip_weekly and not is_friday:
            print("\n[每日 SOP] 非周五且未指定 --with-weekly，跳过周收尾。")
        print(f"\n[每日 SOP] 当日完成。市场温度={temperature.upper()}")
        return

    week_start, week_end = _week_window(args.date)
    persistence_cmd = [
        py,
        "run_persistence_scan.py",
        "--start",
        week_start,
        "--end",
        week_end,
        "--hold",
        "0",
        "--tdx-dir",
        args.tdx_dir,
        "--min-days",
        str(args.min_days),
    ]
    rc = _run_step(f"Step 3: 本周持续性扫描 {week_start}-{week_end}", persistence_cmd)
    if rc != 0:
        print(f"[每日 SOP] 持续性扫描失败 exit={rc}，跳过形态过滤")
        return

    shape_cmd = [
        py,
        "tools/core_shape_filter.py",
        "--date",
        args.date,
        "--persistence",
        f"output/persistence_{week_start}_{week_end}.csv",
        "--super-rate",
        str(args.super_rate),
        "--layers",
        str(args.layers),
        "--tdx-dir",
        args.tdx_dir,
        "--min-days",
        str(args.min_days),
    ]
    rc = _run_step("Step 4: 核心候选形态过滤 v2", shape_cmd)
    if rc != 0:
        print(f"[每日 SOP] 核心形态过滤 exit={rc}")

    print(f"\n[每日 SOP] 周收尾完成。市场温度={temperature.upper()}")
    if temperature == "overheat":
        print("  -> 仅按 S 档建仓；若无 S 候选，建议空仓 / 减半，等下周新血")
    elif temperature == "hot":
        print("  -> 仅 S 档建仓；A 档观察，B 档不建议")
    elif temperature == "normal":
        print("  -> S + A 档可建仓；B 档可作储备")
    elif temperature == "cold":
        print("  -> S + A + B 档全可")


if __name__ == "__main__":
    main()
