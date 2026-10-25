"""
================================================================================
核心候选形态过滤 v2 — 多层共振 + 入场价精准化
================================================================================
输入:
    output/persistence_{start}_{end}.csv     周/月持续性榜
    output/tdx_screener_{date}_*.csv         当日分层结果（用于标记是否在 L1/L2/...）
        - 主 csv 含 RPS_120 / RPS_250 (新版还有 RPS_50)
        - super.csv 含 tier (premium / extended)
        - huoche.csv 含 W_RPS_120 / HUOCHE_FIRST_W

逻辑:
    1) 从持续性榜筛出 super_rate>=阈值 且 layers_covered>=阈值 的核心候选
    2) 用 TDX 全市场日线为这些候选算 close、MA20、MA60、ret20、ret60、vs_ma20、vs_ma60
       以及 pivot_support / pivot_resistance / near_resistance / buy_zone_*
    3) 用 calculate_rps 算 RPS_50 / RPS_120 / RPS_250 当日值
    4) shape_tag 按 vs_ma20/vs_ma60 分类:
         reject_below_ma20  收盘跌破 MA20
         watch_overheated   ret20>50% 或 vs_ma60>50%
         action_pullback    vs_ma20<3%
         action_ok          vs_ma20<=12%
         watch_extended     其余
    5) entry_grade 三档共振:
         S 档 (金三角): tier=premium ∩ Huoche-First (or W_RPS_120>=92) ∩ super_rate>=0.6 ∩ RPS_50>=90
         A 档 (金双角): action 形态 ∩ layers_covered>=3 ∩ RPS_50>=80 ∩ (premium or Huoche or Early_L1)
         B 档:         综合 score_v2 前 N 且 shape_tag != reject
         near_resistance 触发自动降级 (S->A, A->B)

输出:
    output/core_shape_filter_{date}.csv      完整排序结果 + 新增列
    控制台打印 S / A / B 短名单及限价区间

用法:
    py -3 tools/core_shape_filter.py --date 20260522
    py -3 tools/core_shape_filter.py --date 20260522 \
        --persistence output/persistence_20260518_20260522.csv \
        --super-rate 0.5 --layers 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market  # noqa: E402
from rps_analyzer.rps_calculator import calculate_rps  # noqa: E402

OUTPUT_DIR = ROOT / "output"

LAYER_SUFFIX = {
    "in_l1": ".csv",
    "in_l2": "_L2.csv",
    "in_huoche": "_huoche.csv",
    "in_huoche_first": "_huoche_first_codes.csv",
    "in_early_l1": "_early_L1.csv",
    "in_super": "_super.csv",
    "in_super_premium": "_super_premium_codes.csv",
}

SHAPE_PRIORITY = {
    "action_pullback": 0,
    "action_ok": 1,
    "watch_extended": 2,
    "watch_overheated": 3,
    "reject_below_ma20": 4,
}

GRADE_PRIORITY = {"S": 0, "A": 1, "B": 2, "-": 3}


def find_persistence_csv(date: str) -> Path:
    """寻找最近一份包含 date 的 persistence CSV。"""
    if not OUTPUT_DIR.exists():
        raise FileNotFoundError(f"output 目录不存在: {OUTPUT_DIR}")

    candidates = sorted(OUTPUT_DIR.glob("persistence_*_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            "未找到 persistence_*.csv，请先跑 run_persistence_scan.py"
        )

    matched = [p for p in candidates if p.stem.endswith(f"_{date}")]
    if matched:
        return max(matched, key=lambda p: p.stat().st_mtime)

    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_layer_sets(date: str) -> dict[str, set[str]]:
    """读取当日各层 ts_code 集合，缺失文件返回空集合。"""
    out: dict[str, set[str]] = {}
    for name, suffix in LAYER_SUFFIX.items():
        path = OUTPUT_DIR / f"tdx_screener_{date}{suffix}"
        if not path.exists():
            out[name] = set()
            continue
        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            out[name] = set()
            continue
        if "ts_code" in df.columns:
            out[name] = set(df["ts_code"].astype(str))
        elif df.shape[1] == 1:
            out[name] = set(df.iloc[:, 0].astype(str))
        else:
            out[name] = set()
    return out


def load_super_tier(date: str) -> dict[str, str]:
    """从 super.csv 读 ts_code -> tier 映射。"""
    path = OUTPUT_DIR / f"tdx_screener_{date}_super.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return {}
    if "ts_code" not in df.columns or "tier" not in df.columns:
        return {}
    return dict(zip(df["ts_code"].astype(str), df["tier"].astype(str)))


def load_huoche_info(date: str) -> pd.DataFrame:
    """从 huoche.csv 读 ts_code, W_RPS_120, HUOCHE_FIRST_W。"""
    path = OUTPUT_DIR / f"tdx_screener_{date}_huoche.csv"
    if not path.exists():
        return pd.DataFrame(columns=["ts_code", "W_RPS_120", "HUOCHE_FIRST_W"])
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=["ts_code", "W_RPS_120", "HUOCHE_FIRST_W"])
    keep = [c for c in ["ts_code", "W_RPS_120", "HUOCHE_FIRST_W"] if c in df.columns]
    out = df[keep].copy()
    out["ts_code"] = out["ts_code"].astype(str)
    return out


def classify_shape(row: pd.Series) -> str:
    if not row["above_ma20"]:
        return "reject_below_ma20"
    if row["vs_ma60"] > 0.50 or row["ret20"] > 0.50:
        return "watch_overheated"
    if row["vs_ma20"] < 0.03:
        return "action_pullback"
    if row["vs_ma20"] <= 0.12:
        return "action_ok"
    return "watch_extended"


def compute_shapes(
    df_daily: pd.DataFrame, codes: list[str], target_date: str
) -> pd.DataFrame:
    """对候选股票算技术形态 + pivot 关键位 + 三档买入区。"""
    target_dt = pd.Timestamp(target_date)
    sub = df_daily[df_daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return pd.DataFrame()
    sub = sub.sort_values(["ts_code", "trade_date"])
    g = sub.groupby("ts_code", sort=False)
    sub["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    sub["MA60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).mean())
    sub["ret20"] = g["close"].transform(lambda x: x / x.shift(20) - 1)
    sub["ret60"] = g["close"].transform(lambda x: x / x.shift(60) - 1)
    sub["vs_ma20"] = sub["close"] / sub["MA20"] - 1
    sub["vs_ma60"] = sub["close"] / sub["MA60"] - 1
    sub["amount_1e8"] = sub["amount"] / 1e8

    # pivot 关键位：MA20 与近 20 日 close 第 20 分位取较高者为支撑；
    # 近 60 日 close 第 90 分位为软压力，若 close 已经超出则用 60d high 作硬压力
    sub["q20_close_20d"] = g["close"].transform(
        lambda x: x.rolling(20, min_periods=20).quantile(0.20)
    )
    sub["q90_close_60d"] = g["close"].transform(
        lambda x: x.rolling(60, min_periods=60).quantile(0.90)
    )
    sub["high_60d"] = g["high"].transform(
        lambda x: x.rolling(60, min_periods=60).max()
    )

    latest = (
        sub[sub["trade_date"] <= target_dt]
        .groupby("ts_code", sort=False)
        .tail(1)
        .reset_index(drop=True)
    )
    latest["above_ma20"] = latest["close"] > latest["MA20"]
    latest["above_ma60"] = latest["close"] > latest["MA60"]

    latest["pivot_support"] = np.maximum(latest["MA20"], latest["q20_close_20d"])
    latest["pivot_resistance"] = np.where(
        latest["close"] > latest["q90_close_60d"],
        latest["high_60d"],
        latest["q90_close_60d"],
    )
    latest["resistance_gap"] = (latest["pivot_resistance"] - latest["close"]) / latest["close"]
    latest["near_resistance"] = latest["resistance_gap"] < 0.05

    latest["buy_zone_ideal_lo"] = (latest["pivot_support"] * 0.99).round(2)
    latest["buy_zone_ideal_hi"] = (latest["pivot_support"] * 1.02).round(2)
    latest["buy_zone_accept_lo"] = (latest["pivot_support"] * 1.02).round(2)
    latest["buy_zone_accept_hi"] = (latest["pivot_support"] * 1.05).round(2)
    latest["buy_zone_chase"] = (latest["pivot_support"] * 1.08).round(2)
    latest["stop_init"] = (latest["pivot_support"] * 0.95).round(2)

    latest["trade_date"] = latest["trade_date"].dt.strftime("%Y%m%d")
    return latest


def compute_rps_today(df_daily: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """计算 target_date 当日 RPS_50/120/250。"""
    target_dt = pd.Timestamp(target_date)
    df = df_daily[df_daily["trade_date"] <= target_dt].copy()
    df = calculate_rps(df, periods=[50, 120, 250], verbose=False)
    valid = df["trade_date"].max()
    today = df[df["trade_date"] == valid][
        ["ts_code", "RPS_50", "RPS_120", "RPS_250"]
    ].copy()
    today["ts_code"] = today["ts_code"].astype(str)
    return today


def assign_entry_grade(row: pd.Series) -> str:
    """计算 entry_grade 原始档位（不含降级）

    v3 阈值（基于 0413-0417 / 0511-0515 历史回测调整）：
      - 历史样本显示"纯 SuperP"是接盘信号（胜率 16-50%）
      - "Huoche 单独存在" 是最稳定的胜率源（53-65%）
      - 因此 S 档以 Huoche 为主轴，不再硬要求 tier==premium
    """
    shape_ok = row["shape_tag"] in ("action_pullback", "action_ok")
    if not shape_ok:
        return "-"

    tier_premium = row.get("tier") == "premium"
    huoche_first = bool(row.get("in_huoche_first", False))
    in_huoche = bool(row.get("in_huoche", False))
    in_early_l1 = bool(row.get("in_early_l1", False))
    near_resistance = bool(row.get("near_resistance", False))
    w_rps = row.get("W_RPS_120")
    w_rps_strong = pd.notna(w_rps) and w_rps >= 92
    rps50 = row.get("RPS_50")
    rps50_v = float(rps50) if pd.notna(rps50) else 0.0

    layers_covered = int(row.get("layers_covered", 0))
    super_rate = float(row.get("super_rate", 0.0))

    # S 档 v3：Huoche 主轴 + 形态/位置健康
    #   触发任一条件：
    #     a) 周线火车首发 (Huoche_First) + 多层共振
    #     b) Huoche 在票 + 4 层共振 + W_RPS_120 强 (>=92)
    s_pass = (
        not near_resistance
        and layers_covered >= 4
        and (
            (huoche_first and layers_covered >= 3)
            or (in_huoche and w_rps_strong)
        )
    )
    if s_pass:
        return "S"

    # A 档：层数 >=3 + 短期不弱 + 至少在一个"启动池"
    a_pass = (
        layers_covered >= 3
        and rps50_v >= 80
        and (tier_premium or in_huoche or in_early_l1)
    )
    if a_pass:
        return "A"

    # 保留旧 S 档作为 A 档兜底（防止极端 premium 票被遗漏）
    legacy_s = (
        tier_premium
        and layers_covered >= 3
        and super_rate >= 0.6
        and rps50_v >= 90
    )
    if legacy_s:
        return "A"

    return "-"


def compute_score_v2(row: pd.Series) -> float:
    rps50 = float(row.get("RPS_50", 0) or 0)
    rps120 = float(row.get("RPS_120", 0) or 0)
    rps250 = float(row.get("RPS_250", 0) or 0)
    layers = float(row.get("layers_covered", 0) or 0)
    sr = float(row.get("super_rate", 0) or 0)
    vs60 = float(row.get("vs_ma60", 0) or 0)
    ret20 = float(row.get("ret20", 0) or 0)
    near_r = bool(row.get("near_resistance", False))

    score = (
        0.30 * rps50
        + 0.30 * rps120
        + 0.20 * rps250
        + 0.10 * layers * 20
        + 0.10 * sr * 100
    )
    if vs60 > 0.40:
        score -= 10
    if ret20 > 0.40:
        score -= 10
    if near_r:
        score -= 15
    return round(score, 2)


def downgrade(grade: str) -> str:
    if grade == "S":
        return "A"
    if grade == "A":
        return "B"
    return grade


def main() -> None:
    ap = argparse.ArgumentParser(description="核心候选形态过滤 v2")
    ap.add_argument("--date", required=True, help="目标日期 YYYYMMDD")
    ap.add_argument("--persistence", default=None, help="持续性 csv 路径，缺省自动找最近一份")
    ap.add_argument("--super-rate", type=float, default=0.6, help="入选 super_rate 下限")
    ap.add_argument("--layers", type=int, default=3, help="入选 layers_covered 下限")
    ap.add_argument("--tdx-dir", default=DEFAULT_TDX_DIR, help="通达信 vipdoc 路径，或 sh/sz lday 路径")
    ap.add_argument("--min-days", type=int, default=250)
    ap.add_argument("--top-b", type=int, default=10, help="B 档保留 top N")
    ap.add_argument("--out", default=None, help="输出 csv 路径，缺省 output/core_shape_filter_<date>.csv")
    args = ap.parse_args()

    persistence_path = Path(args.persistence) if args.persistence else find_persistence_csv(args.date)
    print(f"[核心过滤 v2] 持续性来源: {persistence_path}")

    persistence = pd.read_csv(persistence_path)
    missing = {
        "ts_code", "super_rate", "layers_covered",
        "L1", "L2", "Super", "Huoche", "Early_L1", "score",
    } - set(persistence.columns)
    if missing:
        raise ValueError(f"持续性 csv 缺少必要列: {sorted(missing)}")

    core = persistence[
        (persistence["super_rate"] >= args.super_rate)
        & (persistence["layers_covered"] >= args.layers)
    ].copy()

    if core.empty:
        print(f"[核心过滤 v2] 持续性榜内 super_rate>={args.super_rate} & layers>={args.layers} 无入选")
        return

    core["ts_code"] = core["ts_code"].astype(str)
    codes = core["ts_code"].tolist()
    print(f"[核心过滤 v2] 候选 {len(codes)} 只: {', '.join(codes)}")

    print(f"[核心过滤 v2] 读 TDX 全市场 {args.tdx_dir} ...")
    df = load_tdx_market(args.tdx_dir, min_days=args.min_days, verbose=False)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    shapes = compute_shapes(df, codes, args.date)
    if shapes.empty:
        print(f"[核心过滤 v2] 候选股票在 {args.date} 之前无可用日线")
        return

    print(f"[核心过滤 v2] 计算 RPS_50 / RPS_120 / RPS_250 ...")
    rps_today = compute_rps_today(df, args.date)

    layers = load_layer_sets(args.date)
    tier_map = load_super_tier(args.date)
    huoche_info = load_huoche_info(args.date)

    merged = core.merge(shapes, on="ts_code", how="left")
    merged = merged.merge(rps_today, on="ts_code", how="left")
    if not huoche_info.empty:
        merged = merged.merge(huoche_info, on="ts_code", how="left")
    for name, members in layers.items():
        merged[name] = merged["ts_code"].astype(str).isin(members)
    merged["tier"] = merged["ts_code"].map(tier_map).fillna("-")

    merged = merged.dropna(subset=["close", "MA20"])
    merged["shape_tag"] = merged.apply(classify_shape, axis=1)
    merged["shape_priority"] = merged["shape_tag"].map(SHAPE_PRIORITY)

    merged["entry_grade_raw"] = merged.apply(assign_entry_grade, axis=1)
    merged["score_v2"] = merged.apply(compute_score_v2, axis=1)

    # B 档：剩余的按 score_v2 取 top N，排除 reject
    in_sa = merged["entry_grade_raw"].isin(["S", "A"])
    rest = merged[(~in_sa) & (merged["shape_tag"] != "reject_below_ma20")].copy()
    rest = rest.sort_values("score_v2", ascending=False).head(args.top_b)
    merged["entry_grade"] = merged["entry_grade_raw"]
    merged.loc[rest.index, "entry_grade"] = "B"

    # 自动降级：near_resistance
    merged.loc[
        merged["near_resistance"] & (merged["entry_grade"] == "S"), "entry_grade"
    ] = "A"
    merged.loc[
        merged["near_resistance"] & (merged["entry_grade"] == "A"), "entry_grade"
    ] = "B"

    merged["entry_grade"] = merged["entry_grade"].fillna("-").replace("", "-")
    merged["grade_priority"] = merged["entry_grade"].map(GRADE_PRIORITY).fillna(99)
    merged = merged.sort_values(
        ["grade_priority", "score_v2", "shape_priority", "super_rate", "vs_ma60"],
        ascending=[True, False, True, False, True],
    ).reset_index(drop=True)

    cols = [
        "ts_code", "entry_grade", "shape_tag", "tier", "close",
        "RPS_50", "RPS_120", "RPS_250", "W_RPS_120",
        "vs_ma20", "vs_ma60", "ret20", "ret60", "amount_1e8",
        "pivot_support", "pivot_resistance", "near_resistance",
        "buy_zone_ideal_lo", "buy_zone_ideal_hi",
        "buy_zone_accept_lo", "buy_zone_accept_hi",
        "buy_zone_chase", "stop_init",
        "L1", "L2", "Super", "Huoche", "Early_L1",
        "layers_covered", "super_rate", "score", "score_v2",
        "in_super", "in_super_premium", "in_huoche", "in_huoche_first",
        "in_early_l1", "in_l1", "in_l2",
        "above_ma20", "above_ma60",
    ]
    cols = [c for c in cols if c in merged.columns]

    out_path = Path(args.out) if args.out else OUTPUT_DIR / f"core_shape_filter_{args.date}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged[cols].to_csv(out_path, index=False, encoding="utf-8-sig")

    show = merged[cols].copy()
    for col in ["vs_ma20", "vs_ma60", "ret20", "ret60", "super_rate"]:
        if col in show.columns:
            show[col] = (show[col] * 100).round(1)
    for col in ["RPS_50", "RPS_120", "RPS_250", "W_RPS_120"]:
        if col in show.columns:
            show[col] = show[col].round(1)
    if "amount_1e8" in show.columns:
        show["amount_1e8"] = show["amount_1e8"].round(1)

    print("\n[核心过滤 v2] 完整结果")
    show_simple_cols = [
        "ts_code", "entry_grade", "shape_tag", "tier",
        "RPS_50", "RPS_120", "vs_ma20", "vs_ma60", "ret20",
        "near_resistance", "score_v2",
    ]
    show_simple_cols = [c for c in show_simple_cols if c in show.columns]
    print(show[show_simple_cols].to_string(index=False))

    _print_shortlist(merged, "S", "S 档（金三角，核心仓位）")
    _print_shortlist(merged, "A", "A 档（金双角，机动仓位）")
    _print_shortlist(merged, "B", "B 档（综合评分前 N，观察）")

    print(f"\n[核心过滤 v2] 输出: {out_path}")


def _print_shortlist(merged: pd.DataFrame, grade: str, title: str) -> None:
    sub = merged[merged["entry_grade"] == grade]
    print(f"\n[核心过滤 v2] {title}")
    if sub.empty:
        print("  (无)")
        return
    for _, r in sub.iterrows():
        close = r["close"]
        i_lo = r.get("buy_zone_ideal_lo")
        i_hi = r.get("buy_zone_ideal_hi")
        a_lo = r.get("buy_zone_accept_lo")
        a_hi = r.get("buy_zone_accept_hi")
        chase = r.get("buy_zone_chase")
        stop = r.get("stop_init")
        nr = "[!压力] " if r.get("near_resistance") else ""
        print(
            f"  {r['ts_code']:<10} {nr}{r['shape_tag']:<16} "
            f"close={close:.2f}  ideal=[{i_lo:.2f},{i_hi:.2f}]  "
            f"accept=[{a_lo:.2f},{a_hi:.2f}]  chase>{chase:.2f}  stop={stop:.2f}"
        )


if __name__ == "__main__":
    main()
