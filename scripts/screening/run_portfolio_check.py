"""
================================================================================
持仓体检 + 候选排队 — 配合 docs/strategy_daily_sop.md 的每日操作工具
================================================================================
读取 portfolio.csv 当前持仓 + output/ 历史 codes，输出:

  1. 持仓体检：每只票当日 close、浮盈%、距 MA20/MA60、卖出触发器命中情况
  2. 候选排队：Super 榜连续 ≥ HOLD_DAYS 且未持有的票（推荐限价）
  3. 行业/前缀分散提醒
  4. 今日动作建议（按 SOP §3 / §4 规则）

用法:
    py -3 run_portfolio_check.py --date 20260417
    py -3 run_portfolio_check.py --date 20260417 --portfolio my_holding.csv
    py -3 run_portfolio_check.py --date 20260417 --hold-days 3
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set

import pandas as pd

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market
from rps_analyzer.rps_calculator import calculate_rps
from rps_analyzer.codes_csv import read_codes_csv

OUTPUT_DIR = Path("output")

LAYER_FILES = {
    "L1": "_codes.csv",
    "L2": "_L2_codes.csv",
    "Super": "_super_codes.csv",
    "Huoche": "_huoche_codes.csv",
    "Huoche_FIRST": "_huoche_first_codes.csv",
    "Early_L1": "_early_L1_codes.csv",
}


def load_portfolio(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"持仓文件 {path} 不存在。请复制 portfolio_template.csv -> portfolio.csv 并填写。"
        )
    df = pd.read_csv(path, comment="#")
    df = df.dropna(subset=["ts_code"])
    if df.empty:
        return df
    df["buy_date"] = df["buy_date"].astype(str)
    df["cost_price"] = df["cost_price"].astype(float)
    df["shares"] = df["shares"].astype(int)
    return df


def load_codes_history(layer_suffix: str, end_date: str, lookback_days: int) -> Dict[str, Set[str]]:
    """读取 [end - lookback, end] 范围内某层 codes 的历史快照"""
    end = datetime.strptime(end_date, "%Y%m%d")
    history: Dict[str, Set[str]] = {}
    for i in range(lookback_days):
        d = end - timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        path = OUTPUT_DIR / f"tdx_screener_{ds}{layer_suffix}"
        if path.exists():
            history[ds] = set(read_codes_csv(path))
    return history


def streak_in(history: Dict[str, Set[str]], code: str, dates_desc: List[str]) -> int:
    """从最新日期往前数，连续在 history[d] 集合里的天数"""
    cnt = 0
    for d in dates_desc:
        if code in history.get(d, set()):
            cnt += 1
        else:
            break
    return cnt


def streak_not_in(history: Dict[str, Set[str]], code: str, dates_desc: List[str]) -> int:
    cnt = 0
    for d in dates_desc:
        if code not in history.get(d, set()):
            cnt += 1
        else:
            break
    return cnt


def compute_indicators(df: pd.DataFrame, codes: List[str], target_date: str) -> Dict[str, dict]:
    """对一组 ts_code 计算当日 close + MA20/MA60 + 高点回撤 + 连续 < MA20 天数"""
    target_dt = pd.Timestamp(target_date)
    out: Dict[str, dict] = {}

    sub = df[df["ts_code"].isin(codes)].copy()
    sub = sub.sort_values(["ts_code", "trade_date"])

    g = sub.groupby("ts_code", sort=False)
    sub["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    sub["MA60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).mean())

    for code, sdf in sub.groupby("ts_code", sort=False):
        s = sdf[sdf["trade_date"] <= target_dt].sort_values("trade_date")
        if s.empty:
            continue
        today = s.iloc[-1]
        last_60 = s.tail(60)
        max_close_60 = last_60["close"].max()

        below_ma20_streak = 0
        for _, row in s.tail(10).iloc[::-1].iterrows():
            if pd.notna(row["MA20"]) and row["close"] < row["MA20"]:
                below_ma20_streak += 1
            else:
                break

        out[code] = {
            "close": float(today["close"]),
            "ma20": float(today["MA20"]) if pd.notna(today["MA20"]) else None,
            "ma60": float(today["MA60"]) if pd.notna(today["MA60"]) else None,
            "max_close_60d": float(max_close_60),
            "below_ma20_streak": below_ma20_streak,
            "below_ma60": pd.notna(today["MA60"]) and today["close"] < today["MA60"],
        }
    return out


def check_holding(
    row: pd.Series,
    indicator: dict,
    super_history: Dict[str, Set[str]],
    super_dates_desc: List[str],
    target_date: str,
) -> dict:
    """对单只持仓评估卖出触发器"""
    code = row["ts_code"]
    cost = row["cost_price"]
    buy_date = row["buy_date"]
    slot = str(row.get("slot_type", "核心")).strip()

    close = indicator.get("close")
    triggers: List[str] = []
    suggested_action = "持有"

    if close is None:
        return {
            "code": code,
            "close": None,
            "pnl_pct": None,
            "triggers": ["数据缺失"],
            "suggested_action": "人工核实",
        }

    pnl = close / cost - 1

    held_days = 0
    try:
        bd = datetime.strptime(buy_date, "%Y%m%d")
        td = datetime.strptime(target_date, "%Y%m%d")
        held_days = (td - bd).days
    except ValueError:
        held_days = 0

    if pnl <= -0.08:
        triggers.append(f"硬止损 {pnl:.1%} ≤ -8%")
        suggested_action = "全部清仓"
    elif indicator.get("below_ma60"):
        triggers.append(f"收盘 {close:.2f} < MA60 {indicator['ma60']:.2f}")
        suggested_action = "全部清仓"
    elif indicator.get("below_ma20_streak", 0) >= 2:
        if slot == "核心":
            triggers.append(f"连续 {indicator['below_ma20_streak']} 天 < MA20")
            suggested_action = "核心减半"
        else:
            triggers.append(f"连续 {indicator['below_ma20_streak']} 天 < MA20")
            suggested_action = "机动全清"

    not_in_super_streak = streak_not_in(super_history, code, super_dates_desc)
    if slot == "核心" and not_in_super_streak >= 10:
        triggers.append(f"已 {not_in_super_streak} 天不在 Super 榜")
        if suggested_action == "持有":
            suggested_action = "核心全清"
    elif slot == "核心" and not_in_super_streak >= 5:
        triggers.append(f"已 {not_in_super_streak} 天不在 Super 榜")
        if suggested_action == "持有":
            suggested_action = "核心减半"
    elif slot == "机动" and not_in_super_streak >= 3:
        triggers.append(f"机动: {not_in_super_streak} 天不在 Super")
        if suggested_action == "持有":
            suggested_action = "机动全清"

    drawdown_from_high = (close / indicator["max_close_60d"] - 1) if indicator.get("max_close_60d") else 0
    if pnl >= 0.25 and suggested_action == "持有":
        triggers.append(f"浮盈 {pnl:.1%} ≥ 25%")
        suggested_action = "止盈减半"
    elif drawdown_from_high <= -0.15 and pnl > 0:
        triggers.append(f"自高点回撤 {drawdown_from_high:.1%}")
        if suggested_action == "持有":
            suggested_action = "保盈清仓"

    if held_days >= 30 and pnl < 0.05 and suggested_action == "持有":
        triggers.append(f"持仓 {held_days} 天浮盈 {pnl:.1%} < 5%")
        suggested_action = "时间止损"

    return {
        "code": code,
        "slot": slot,
        "close": close,
        "ma20": indicator.get("ma20"),
        "ma60": indicator.get("ma60"),
        "pnl_pct": pnl,
        "held_days": held_days,
        "not_in_super_streak": not_in_super_streak,
        "triggers": triggers,
        "suggested_action": suggested_action,
    }


def find_candidates(
    super_history: Dict[str, Set[str]],
    super_dates_desc: List[str],
    held_codes: Set[str],
    hold_days: int,
    df: pd.DataFrame,
    target_date: str,
) -> List[dict]:
    """从 Super 历史里找连续 ≥ hold_days 且未持有的候选"""
    if not super_dates_desc:
        return []
    today_super = super_history.get(super_dates_desc[0], set())
    candidates = []
    for code in today_super:
        if code in held_codes:
            continue
        streak = streak_in(super_history, code, super_dates_desc)
        if streak >= hold_days:
            candidates.append({"code": code, "super_streak": streak})

    if not candidates:
        return []

    codes = [c["code"] for c in candidates]
    ind = compute_indicators(df, codes, target_date)
    for c in candidates:
        info = ind.get(c["code"], {})
        c["close"] = info.get("close")
        c["limit_low"] = info.get("close", 0) * 0.99 if info.get("close") else None
        c["limit_high"] = info.get("close", 0) * 1.01 if info.get("close") else None

    candidates.sort(key=lambda x: -x["super_streak"])
    return candidates


def industry_concentration(holdings_codes: Set[str]) -> Dict[str, int]:
    """按代码前缀粗分布（不需要行业映射，提示用）"""
    buckets: Dict[str, int] = {}
    for c in holdings_codes:
        prefix = c[:3]
        buckets[prefix] = buckets.get(prefix, 0) + 1
    return buckets


def main():
    ap = argparse.ArgumentParser(description="持仓体检 + 候选排队")
    ap.add_argument("--date", required=True, help="目标日期 YYYYMMDD")
    ap.add_argument("--portfolio", default="portfolio.csv", help="持仓 csv 文件")
    ap.add_argument("--hold-days", type=int, default=3, help="候选 Super 持续 ≥ N 天才推荐 (默认3)")
    ap.add_argument("--lookback", type=int, default=21, help="历史 codes 回看自然日 (默认21)")
    ap.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR)
    ap.add_argument("--min-days", type=int, default=250)
    args = ap.parse_args()

    portfolio_path = Path(args.portfolio)
    pf = load_portfolio(portfolio_path)

    super_history = load_codes_history(LAYER_FILES["Super"], args.date, args.lookback)
    huoche_first_history = load_codes_history(LAYER_FILES["Huoche_FIRST"], args.date, args.lookback)

    if not super_history:
        print(
            f"\n[!] 未找到任何 super_codes 历史 (查找过去 {args.lookback} 天)。"
            f"\n    请先跑 run_tdx_screener.py 生成至少一份 tdx_screener_{args.date}_super_codes.csv\n"
        )
        return

    super_dates_desc = sorted(super_history.keys(), reverse=True)
    huoche_dates_desc = sorted(huoche_first_history.keys(), reverse=True)

    print("=" * 70)
    print(f"持仓体检 / 候选排队 — 目标日 {args.date}")
    print("=" * 70)
    print(
        f"持仓数: {len(pf)}    Super 历史快照: {len(super_history)} 天 "
        f"(最新 {super_dates_desc[0] if super_dates_desc else '无'})"
    )

    t0 = time.time()
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days, verbose=False)
    df = calculate_rps(df, periods=[120, 250], verbose=False)
    print(f"  数据加载 + RPS: {time.time()-t0:.1f}s")

    held_codes = set(pf["ts_code"]) if not pf.empty else set()

    print("\n" + "=" * 70)
    print("第 1 部分：持仓体检")
    print("=" * 70)
    if pf.empty:
        print("  (空仓 — 持仓清单为空)")
    else:
        ind = compute_indicators(df, list(held_codes), args.date)
        rows = []
        for _, h in pf.iterrows():
            r = check_holding(h, ind.get(h["ts_code"], {}), super_history, super_dates_desc, args.date)
            rows.append(r)

        for r in rows:
            slot = r.get("slot", "?")
            close = r.get("close")
            close_s = f"{close:.2f}" if close else "N/A"
            pnl = r.get("pnl_pct")
            pnl_s = f"{pnl:+.1%}" if pnl is not None else "N/A"
            ma20 = r.get("ma20")
            ma60 = r.get("ma60")
            ma_s = ""
            if close and ma20 and ma60:
                rel = "上方" if close > ma20 else "下方"
                ma_s = f"close vs MA20 {rel}, MA60={ma60:.2f}"
            held = r.get("held_days", 0)
            ns = r.get("not_in_super_streak", 0)

            print(f"\n  {r['code']:<12} [{slot}] 收盘 {close_s}  浮盈 {pnl_s}  持仓 {held} 天")
            if ma_s:
                print(f"    {ma_s}")
            print(f"    Super 已退出 {ns} 天")
            if r["triggers"]:
                print(f"    触发: {' | '.join(r['triggers'])}")
            print(f"    >>> 建议: {r['suggested_action']}")

    print("\n" + "=" * 70)
    print(f"第 2 部分：候选排队 (Super 连续 ≥ {args.hold_days} 天 且 未持有)")
    print("=" * 70)
    cands = find_candidates(super_history, super_dates_desc, held_codes, args.hold_days, df, args.date)
    if not cands:
        print("  无符合条件的新候选")
    else:
        print(f"  共 {len(cands)} 只候选 (按 Super 连续天数降序)")
        print(f"\n  {'代码':<12}{'连续Super':>10}{'收盘':>10}{'限价区间':>20}")
        for c in cands[:20]:
            close = c.get("close")
            if close:
                lo = c["limit_low"]
                hi = c["limit_high"]
                limit_s = f"{lo:.2f} ~ {hi:.2f}"
            else:
                close = 0.0
                limit_s = "N/A"
            print(f"  {c['code']:<12}{c['super_streak']:>10}{close:>10.2f}{limit_s:>20}")

    if huoche_dates_desc:
        latest_first = huoche_first_history.get(huoche_dates_desc[0], set())
        if latest_first:
            new_first = latest_first - held_codes
            if new_first:
                print(f"\n  [Huoche FIRST_W 当日新触发] {len(new_first)} 只 (强信号, 可越级建机动仓):")
                for c in sorted(new_first):
                    print(f"    {c}")

    print("\n" + "=" * 70)
    print("第 3 部分：行业 / 前缀分散提醒")
    print("=" * 70)
    if held_codes:
        buckets = industry_concentration(held_codes)
        sorted_buckets = sorted(buckets.items(), key=lambda x: -x[1])
        for prefix, n in sorted_buckets:
            mark = "  ⚠️" if n >= 3 else ""
            print(f"  代码前缀 {prefix}xx : {n} 只{mark}")
        if any(n >= 3 for _, n in sorted_buckets):
            print("  (⚠️ 同前缀 ≥ 3 只可能集中在同板块, 建议分散)")
    else:
        print("  (空仓)")

    print("\n" + "=" * 70)
    print("第 4 部分：今日动作建议")
    print("=" * 70)
    weekday = datetime.strptime(args.date, "%Y%m%d").weekday()
    weekday_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]
    print(f"  今日是 {args.date} ({weekday_name})")

    if not pf.empty:
        sells = [r for r in rows if r["suggested_action"] not in ("持有", "人工核实")]
        if sells:
            print("\n  [建议卖出] (按 SOP §3 不等周一, 趋势/止损类立即执行):")
            for r in sells:
                print(f"    {r['code']}  -> {r['suggested_action']}  ({' | '.join(r['triggers'])})")
        else:
            print("\n  [持仓] 无卖出触发, 按兵不动")

    if cands and weekday in (0, 2):
        print(f"\n  [建议买入] {weekday_name}集中下单日, 候选池前 3:")
        for c in cands[:3]:
            print(f"    {c['code']}  限价 {c.get('limit_low', 0):.2f} ~ {c.get('limit_high', 0):.2f}")
    elif cands:
        print(f"\n  [候选观察] 今日非集中下单日 ({weekday_name}), 仅记录候选, 周一/周三再下单")

    print(f"\n  耗时: {time.time()-t0:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
