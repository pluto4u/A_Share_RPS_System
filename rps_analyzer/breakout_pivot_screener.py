"""
================================================================================
左边高点突破 / 即将突破 选股 (Pivot Breakout)
================================================================================
设计目标:
    捕捉"经过一段回看后形成的左边高点(pivot)，价格正在突破或即将突破"的票，
    对应欧奈尔 / Minervini 的平台突破买点(pivot buy point)。

核心概念 —— 左边高点(左高 / pivot):
    在 [今天 - recent_gap - peak_lookback, 今天 - recent_gap] 这段"靠左"的窗口内
    取最高价，作为待突破的关键阻力。排除最近 recent_gap 天，避免把当前这波拉升
    本身误当成"左高"。左高之后到今天为止的回踩 = 基底(base)。

两档信号 (二选一即入选):

    [突破] BREAKOUT —— 已经站上左高:
        BO1  close 突破左高 (close > 左高 * (1 + bo_buffer))
        BO2  新鲜突破 (最近 bo_fresh_days 内才首次站上, 非已突破很久)
        BO3  放量确认 (突破段内量 >= bo_vol_x * 基准量)
        BO4  不过度追高 (close 距左高 <= bo_ext_max)

    [即将突破] PRE_BREAKOUT —— 贴在左高下方蓄势:
        PB1  逼近左高 (close 在左高下方 0 ~ pb_near_max 区间)
        PB2  平台收敛 (近 pb_tight_window 振幅 / close <= pb_tight_max)
        PB3  缩量盘整 (平台均量 <= pb_vol_dry * 基准量)

通用风控 (两档都要满足):
    G1  RPS_120 >= rps_min (趋势强度)
    G2  趋势向上 (close > MA20 且 close > MA(above_ma) 且 MA20 上行)
    G3  基底回撤可控 (左高 -> 基底最低 的回撤 <= base_depth_max)
    G4  未翻倍 (close < 250 日最低 * 2, 与洪战法风控一致)  [可关]
    G5  成交活跃 (amount >= min_amount)

数据格式: trade_date, ts_code, open, high, low, close, vol, amount,
          以及 RPS_120 (可选 RPS_250 / turnover)
依赖: pandas, numpy
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class BreakoutConfig:
    """左边高点突破可调参数"""

    # --- 左高(pivot) 定义 ---
    peak_lookback: int = 120        # 向左找高点的回看窗口(交易日)
    recent_gap: int = 10            # 排除最近 N 日, 避免把当前拉升算成左高

    # --- 通用强度 / 趋势 ---
    rps_min: float = 80.0           # RPS_120 下限
    above_ma: int = 50              # 须站上的趋势均线 (close > MAN)
    ma20_rise_lookback: int = 5     # MA20 相比 N 日前不走低 (上行)

    # --- 突破档 ---
    bo_buffer: float = 0.0          # close 须 > 左高 * (1 + bo_buffer)
    bo_fresh_days: int = 3          # 突破必须发生在最近 N 日内 (新鲜)
    bo_stale_window: int = 8        # 最近 bo_stale_window 日内站上左高的天数上限
    bo_stale_max_days: int = 4      # ↑ 上限, 超过视为"突破已久"
    bo_vol_x: float = 1.5           # 突破段内最大量 >= bo_vol_x * 基准量
    bo_ext_max: float = 0.12        # close 距左高涨幅 <= 12% (不追高)

    # --- 即将突破档 ---
    pb_near_max: float = 0.07       # close 在左高下方 0 ~ 7% 内
    pb_tight_window: int = 15       # 平台窗口
    pb_tight_max: float = 0.12      # 平台振幅 (HHV-LLV)/close <= 12%
    pb_vol_window: int = 10         # 平台均量窗口
    pb_vol_dry: float = 1.0         # 平台均量 <= pb_vol_dry * 基准量 (缩量)

    # --- 通用风控 ---
    base_depth_max: float = 0.30    # 左高 -> 基底最低 回撤 <= 30%
    vol_base_window: int = 60       # 基准量窗口
    not_doubled: bool = True        # 未翻倍 (close < LLV(low,250)*2)
    min_amount: float = 5e7         # 成交额下限 (5000 万)


def _add_indicators(df: pd.DataFrame, cfg: BreakoutConfig) -> pd.DataFrame:
    """补齐均线与基准量"""
    df = df.copy()
    g = df.groupby("ts_code", sort=False)
    periods = sorted({5, 10, 20, cfg.above_ma})
    for p in periods:
        df[f"MA{p}"] = g["close"].transform(lambda x: x.rolling(p, min_periods=p).mean())
    df["VOL_BASE"] = g["vol"].transform(
        lambda x: x.rolling(cfg.vol_base_window, min_periods=cfg.vol_base_window // 2).mean()
    )
    return df


def _streak_above(values: np.ndarray, level: float) -> int:
    """从末尾倒数, 连续 values > level 的天数"""
    cnt = 0
    for i in range(len(values) - 1, -1, -1):
        if pd.notna(values[i]) and values[i] > level:
            cnt += 1
        else:
            break
    return cnt


def _scan_one(sdf: pd.DataFrame, target_dt: pd.Timestamp,
              cfg: BreakoutConfig) -> Optional[dict]:
    sdf = sdf[sdf["trade_date"] <= target_dt]
    need = cfg.peak_lookback + cfg.recent_gap + 5
    if len(sdf) < need:
        return None
    sdf = sdf.reset_index(drop=True)
    today = sdf.iloc[-1]

    ma_above_col = f"MA{cfg.above_ma}"
    if (
        pd.isna(today.get("RPS_120"))
        or pd.isna(today.get("MA20"))
        or pd.isna(today.get(ma_above_col))
        or pd.isna(today.get("VOL_BASE"))
    ):
        return None
    if today.get("amount", 0) < cfg.min_amount:
        return None

    n = len(sdf)
    # --- 左高(pivot): 排除最近 recent_gap 日, 向左 peak_lookback 日的最高 ---
    peak_end = n - cfg.recent_gap                       # 不含
    peak_start = max(0, peak_end - cfg.peak_lookback)
    peak_slice = sdf.iloc[peak_start:peak_end]
    if peak_slice.empty:
        return None
    peak_pos = int(peak_slice["high"].values.argmax()) + peak_start
    pivot = float(sdf.iloc[peak_pos]["high"])
    pivot_date = sdf.iloc[peak_pos]["trade_date"]
    if pivot <= 0:
        return None

    close = float(today["close"])
    ext = close / pivot - 1                              # >0 已突破, <0 在下方

    # --- 基底回撤: 左高之后(含)到今天的最低 ---
    base_slice = sdf.iloc[peak_pos:]
    base_low = float(base_slice["low"].min())
    base_depth = (pivot - base_low) / pivot if pivot > 0 else 1.0

    vol_base = float(today["VOL_BASE"])

    # ===== 通用风控 =====
    g1 = today["RPS_120"] >= cfg.rps_min
    ma20_prev = sdf.iloc[-1 - cfg.ma20_rise_lookback]["MA20"] if n > cfg.ma20_rise_lookback else np.nan
    g2 = (
        close > today["MA20"]
        and close > today[ma_above_col]
        and (pd.isna(ma20_prev) or today["MA20"] >= ma20_prev)
    )
    g3 = base_depth <= cfg.base_depth_max
    if cfg.not_doubled:
        llv250 = float(sdf["low"].tail(250).min())
        g4 = close < llv250 * 2 if llv250 > 0 else True
    else:
        g4 = True
    g_all = bool(g1 and g2 and g3 and g4)

    # ===== 突破档 =====
    closes = sdf["close"].values
    above_streak = _streak_above(closes, pivot)
    recent_above = int((closes[-cfg.bo_stale_window:] > pivot).sum())
    bo1 = ext > cfg.bo_buffer
    bo2 = (1 <= above_streak <= cfg.bo_fresh_days) and (recent_above <= cfg.bo_stale_max_days)
    # 突破段(最近 above_streak 日)内最大量比
    seg = sdf.iloc[n - max(above_streak, 1):]
    seg_max_vol_x = float((seg["vol"] / vol_base).max()) if vol_base > 0 else np.nan
    bo3 = (not np.isnan(seg_max_vol_x)) and seg_max_vol_x >= cfg.bo_vol_x
    bo4 = ext <= cfg.bo_ext_max
    is_breakout = bool(bo1 and bo2 and bo3 and bo4 and g_all)

    # ===== 即将突破档 =====
    pb1 = (-cfg.pb_near_max <= ext <= 0)
    tight = sdf.tail(cfg.pb_tight_window)
    tight_amp = (tight["high"].max() - tight["low"].min()) / close if close > 0 else 1.0
    pb2 = tight_amp <= cfg.pb_tight_max
    pb_vol = float(sdf["vol"].tail(cfg.pb_vol_window).mean())
    pb3 = vol_base > 0 and pb_vol <= cfg.pb_vol_dry * vol_base
    is_pre_breakout = bool(pb1 and pb2 and pb3 and g_all)

    if not (is_breakout or is_pre_breakout):
        # 仍返回明细(便于诊断), 但 SIGNAL 为空
        signal = ""
    else:
        signal = "突破" if is_breakout else "即将突破"

    rec = {
        "ts_code": today["ts_code"],
        "trade_date": today["trade_date"],
        "close": close,
        "RPS_120": float(today["RPS_120"]),
        "pivot": round(pivot, 3),
        "pivot_date": pivot_date,
        "ext_pct": round(ext * 100, 2),
        "base_depth_pct": round(base_depth * 100, 2),
        "above_streak": above_streak,
        "seg_max_vol_x": round(seg_max_vol_x, 2) if not np.isnan(seg_max_vol_x) else np.nan,
        "tight_amp_pct": round(tight_amp * 100, 2),
        "pb_vol_x": round(pb_vol / vol_base, 2) if vol_base > 0 else np.nan,
        "G_RISK": g_all,
        "BREAKOUT": is_breakout,
        "PRE_BREAKOUT": is_pre_breakout,
        "SIGNAL": signal,
    }
    if "RPS_250" in today.index and not pd.isna(today.get("RPS_250")):
        rec["RPS_250"] = float(today["RPS_250"])
    return rec


def screen_breakout_pivot(
    df_daily: pd.DataFrame,
    target_date: Optional[str] = None,
    config: Optional[BreakoutConfig] = None,
    candidate_codes: Optional[list] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    全市场扫描"突破 / 即将突破左边高点"候选。

    参数
    ----
    df_daily : 全市场日线 (含 RPS_120 列), 至少 ~peak_lookback+recent_gap 天
    target_date : YYYYMMDD, None 取最新
    config : BreakoutConfig
    candidate_codes : 限定股票池 (None=全市场); 可传 L1 代码做交集

    返回
    ----
    pd.DataFrame: 通过任一档的票, 含 SIGNAL / pivot / ext_pct 等明细;
                  排序: 突破优先, 再按 RPS_120 降序
    """
    cfg = config or BreakoutConfig()
    df = df_daily
    if candidate_codes:
        df = df[df["ts_code"].isin(candidate_codes)].copy()

    df = _add_indicators(df, cfg)

    if target_date is None:
        target_dt = df["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)
        valid = df["trade_date"].unique()
        valid = valid[valid <= td]
        if len(valid) == 0:
            if verbose:
                print(f"[左高突破] {target_date} 之前无数据")
            return pd.DataFrame()
        target_dt = pd.Timestamp(valid.max())

    if verbose:
        print(f"[左高突破] 目标日: {target_dt.strftime('%Y-%m-%d')}, 股票池: {df['ts_code'].nunique()}")
        print(
            f"[左高突破] 左高=排除最近{cfg.recent_gap}日的{cfg.peak_lookback}日最高  "
            f"RPS≥{cfg.rps_min}  站上MA{cfg.above_ma}  基底回撤≤{cfg.base_depth_max:.0%}\n"
            f"           突破: 新鲜≤{cfg.bo_fresh_days}日 放量≥{cfg.bo_vol_x:.1f}x 距高≤{cfg.bo_ext_max:.0%} | "
            f"即将: 下方≤{cfg.pb_near_max:.0%} 平台振幅≤{cfg.pb_tight_max:.0%} 缩量≤{cfg.pb_vol_dry:.1f}x"
        )

    records: List[dict] = []
    for code, sdf in df.groupby("ts_code", sort=False):
        rec = _scan_one(sdf, target_dt, cfg)
        if rec is not None:
            records.append(rec)

    if not records:
        if verbose:
            print("[左高突破] 无可计算样本")
        return pd.DataFrame()

    sigs = pd.DataFrame(records)
    passed = sigs[sigs["SIGNAL"] != ""].copy()

    if verbose:
        total = len(sigs)
        bo = int(sigs["BREAKOUT"].sum())
        pb = int(sigs["PRE_BREAKOUT"].sum())
        print(f"  突破: {bo}/{total}  即将突破: {pb}/{total}  合计入选: {len(passed)}")

    if passed.empty:
        return passed

    # 排序: 突破优先, 再 RPS 降序; 即将突破按"越贴近左高越靠前"
    passed["_rank"] = np.where(passed["BREAKOUT"], 0, 1)
    passed = passed.sort_values(
        ["_rank", "RPS_120"], ascending=[True, False]
    ).drop(columns=["_rank"]).reset_index(drop=True)
    return passed


def print_breakout_summary(result: pd.DataFrame, top_n: int = 40) -> None:
    if result.empty:
        print("  无突破 / 即将突破候选")
        return
    bo = int(result["BREAKOUT"].sum())
    pb = int(result["PRE_BREAKOUT"].sum())
    print(f"\n  左高突破候选: {len(result)} 只 (突破 {bo} / 即将突破 {pb})，前 {min(top_n, len(result))} 只:")
    show = result.head(top_n)[[
        "ts_code", "SIGNAL", "close", "pivot", "ext_pct",
        "RPS_120", "base_depth_pct", "seg_max_vol_x", "tight_amp_pct",
    ]].copy()
    show["RPS_120"] = show["RPS_120"].round(1)
    show.columns = ["代码", "信号", "收盘", "左高", "距高%",
                    "RPS120", "基底回撤%", "突破量比", "平台振幅%"]
    print(show.to_string(index=False))


# =============================================================================
# CLI 入口
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="左边高点突破 / 即将突破选股")
    parser.add_argument("--data", type=str, required=True, help="日线数据 (parquet/csv, 含 RPS_120)")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYYMMDD")
    parser.add_argument("--rps-min", type=float, default=80.0, help="RPS_120 下限")
    parser.add_argument("--peak-lookback", type=int, default=120, help="向左找高点窗口")
    parser.add_argument("--recent-gap", type=int, default=10, help="排除最近 N 日")
    parser.add_argument("--output", type=str, default=None, help="输出 CSV 路径")
    args = parser.parse_args()

    if args.data.endswith(".parquet"):
        data = pd.read_parquet(args.data)
    else:
        data = pd.read_csv(args.data)

    cfg = BreakoutConfig(
        rps_min=args.rps_min,
        peak_lookback=args.peak_lookback,
        recent_gap=args.recent_gap,
    )
    res = screen_breakout_pivot(data, target_date=args.date, config=cfg)
    print_breakout_summary(res)

    if args.output and not res.empty:
        if args.output.endswith(".parquet"):
            res.to_parquet(args.output, index=False)
        else:
            res.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"\n结果已保存到 {args.output}")
