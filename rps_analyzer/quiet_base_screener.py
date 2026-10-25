"""
================================================================================
静默筑底 · 低位再启动 (Quiet Base Relaunch)
================================================================================
市场背景:
    科技极致抱团后的 A 杀造成流动性问题，抱团期又对其他板块大量吸血。
    本策略反向去找"被吸血板块里已经跌透、横盘充分、筹码干净"的低位票，
    并用量价关系锁定"刚站上 MA20、可以买入"的时点。

    注意: 本策略与仓内 breakout_pivot / early_launch / super_strong 方向相反 ——
    那些以 RPS_120 >= 80 的高位强势股为前提，本策略专找低位横盘票，
    因此用 RPS 拐头(相对强度正在改善)替代绝对强度门槛。

窗口结构 (滚动):

    [ 下跌段 drop ]        [ 基底期 base ]         [ 近期 recent ]
    ...前期高点 ↘          窄幅横盘 / 地量          站上MA20 / 温和放量
    T-(gap+base+250)       T-(gap+base) .. T-gap    T-gap+1 .. T(今天)

    默认 base_window=42 / recent_gap=20; 以 2026-07-29 为目标日时,
    基底期约落在 2026-05-06 ~ 2026-06-30 (即 5-6 月的调整横盘段)。

选股条件 (A/B/C/D/E 五组):

    [A] 调整充分 —— A 杀已经发生
        A1  深度回撤 (1 - base_low_raw / pre_high >= min_drawdown)
        A2  仍在低位 (close <= pre_high * (1 - min_below_pre_high))

    [B] 横盘充分 / 筹码干净 —— 基底期内
        B1  箱体收窄 ((box_high - box_low)/box_low <= box_range_max)
            箱体上下沿取基底期收盘价的 q90 / q10, 而非 max(high)/min(low) ——
            A 股 42 日的极值振幅中位数高达 48%, 用极值会把几乎所有票筛掉;
            分位数口径才对应实盘肉眼判断的"箱体", 且抗单日插针 / 一字板。
        B2  横盘连续 (基底期自然日跨度可控, 期间无停牌/零成交)
        B3  无趋势漂移 (基底期收盘价的最小二乘回归线首尾落差/均价, |drift| <= base_drift_max)
            用回归而非首尾相比: 后者识别不出"缓慢单向下台阶", 详见 _regression_drift
        B4  地量 (基底期均量 / 下跌段均量 <= vol_dry_max)  抛压枯竭
        B5  成交价高度集中 (基底期收盘价 std/mean <= close_cv_max)

    [C] 量价关系 / 可买入 —— 近期段
        C1  刚站上 MA20 (close > MA20, 连续站上天数在区间内)
        C2  MA20 转平走升 (MA20 >= MA20[T-ma20_rise_lookback])
        C3  贴近箱体上沿 (close/box_high - 1 在 [box_ext_min, box_ext_max])
        C4  放量确认 (近期最大量 >= vol_expand_x * 基底均量, 且涨日量 > 跌日量)
        C5  不追高 (ret5 <= max_ret5, ret20 <= max_ret20)
        C6  买点位置 (close/MA20 - 1 <= max_vs_ma20)

    [D] RPS 拐头
        D1  RPS_120 - RPS_120[T-rps_slope_lookback] >= rps_rise_min
        D2  RPS_120 >= rps_floor (地板, 防持续阴跌垃圾股; 可关)

    [E] 风控
        E1  流动性 (近 20 日均额 >= min_amount)
        E2  K 线数 >= min_bars (排除次新)
        E3  今日有成交 (未停牌)

分档:
    BUY   (可买入): A + B + C + D + E 全过
    WATCH (观察)  : A + B + D + E 过, 箱体结构未被跌破(box_pos >= watch_box_pos_min)
                    且未追高, 并满足 C5/C6, 但尚未放量站上 MA20 —— 仍在箱体内蓄势

    实测(2026-07-27): A+B+D+E 候选池约 35 只, 其中已放量站上 MA20 的只有个位数。
    底部区域 BUY 档天然稀疏, WATCH 池才是主要产出, BUY 是从 WATCH 池逐日"毕业"的。

数据格式: trade_date, ts_code, open, high, low, close, vol, amount, RPS_120
          (RPS_120 需为历史序列, 由 rps_calculator.calculate_rps 生成)
依赖: pandas, numpy
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Sequence


@dataclass
class QuietBaseConfig:
    """静默筑底 · 低位再启动 可调参数"""

    # --- 窗口结构 ---
    base_window: int = 42               # 横盘基底窗口(交易日)
    recent_gap: int = 20                # 基底期距今天的间隔 = 近期段长度
    drop_lookback: int = 250            # 下跌段 / 前期高点回看窗口

    # --- [A] 调整充分 ---
    min_drawdown: float = 0.30          # A1 前期高点 -> 基底最低 回撤下限
    min_below_pre_high: float = 0.25    # A2 今收须低于前期高点至少 25%

    # --- [B] 横盘充分 / 筹码干净 ---
    box_quantile_hi: float = 0.90       # B1 箱体上沿取收盘价分位数(抗单日插针)
    box_quantile_lo: float = 0.10       # B1 箱体下沿取收盘价分位数
    box_range_max: float = 0.22         # B1 箱体振幅上限
    base_span_max_ratio: float = 2.0    # B2 基底期自然日跨度 / 交易日数 上限
    base_drift_max: float = 0.10        # B3 基底期回归漂移绝对值上限
    vol_dry_max: float = 0.85           # B4 基底均量 / 下跌段均量 上限(地量)
    close_cv_max: float = 0.09          # B5 基底期收盘价 std/mean 上限

    # --- [C] 量价关系 / 可买入 ---
    ma20_streak_min: int = 2            # C1 连续站上 MA20 天数下限
    ma20_streak_max: int = 25           # C1 上限(超过说明已启动很久)
    ma20_rise_lookback: int = 5         # C2 MA20 与 N 日前比较
    ma20_flat_tol: float = 0.01         # C2 WATCH 档允许 MA20 走平的容差
    box_ext_min: float = -0.03          # C3 距箱体上沿下限(-3% 即贴近上沿), BUY 档用
    box_ext_max: float = 0.12           # C3 距箱体上沿上限(+12% 不追高), 两档通用
    watch_box_pos_min: float = 0.0      # C3 WATCH 档: 箱体内相对位置下限(0=未跌破箱底)
    vol_confirm_window: int = 10        # C4 放量确认窗口
    vol_expand_x: float = 1.8           # C4 窗口内最大量 / 基底均量 下限
    up_down_vol_ratio_min: float = 1.10  # C4 涨日均量 / 跌日均量 下限
    max_ret5: float = 0.15              # C5 5 日涨幅上限
    max_ret20: float = 0.35             # C5 20 日涨幅上限
    max_vs_ma20: float = 0.10           # C6 close/MA20 - 1 上限

    # --- [D] RPS 拐头 ---
    rps_slope_lookback: int = 20        # D1 RPS 抬升比较窗口
    rps_rise_min: float = 10.0          # D1 RPS_120 抬升的百分位点数下限
    use_rps_floor: bool = True          # D2 是否启用 RPS 地板
    rps_floor: float = 40.0             # D2 RPS_120 地板

    # --- [E] 风控 ---
    min_amount: float = 8e7             # E1 近 20 日均额下限(8000 万)
    min_bars: int = 300                 # E2 K 线数下限(排除次新)


def _add_indicators(df: pd.DataFrame, cfg: QuietBaseConfig) -> pd.DataFrame:
    """补齐 MA20 与 20 日均额"""
    df = df.copy()
    g = df.groupby("ts_code", sort=False)
    df["MA20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    df["AMT20"] = g["amount"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    return df


def _streak_above(values: np.ndarray, levels: np.ndarray) -> int:
    """从末尾倒数, 连续 values > levels 的天数"""
    cnt = 0
    for i in range(len(values) - 1, -1, -1):
        if pd.notna(values[i]) and pd.notna(levels[i]) and values[i] > levels[i]:
            cnt += 1
        else:
            break
    return cnt


def _regression_drift(values: np.ndarray) -> float:
    """
    基底期的趋势漂移 = 收盘价对时间的最小二乘回归线首尾落差 / 均价。

    用回归而非"首尾(或前后 N 日均值)相比"是必要的: 后者只用到两端的点,
    识别不出"缓慢单向下台阶" —— 一只从 58 一路滑到 51 的票, 首尾 5 日均值
    只差 -8.7%, 箱体宽度也只有 14.6%, 两条都能过关, 但它根本不是横盘。
    回归用上全部样本点, 对持续单向漂移敏感得多。
    """
    y = values.astype(float)
    m = len(y)
    if m < 2:
        return 1.0
    y_mean = y.mean()
    if y_mean <= 0:
        return 1.0
    x = np.arange(m, dtype=float)
    x_dev = x - x.mean()
    denom = float((x_dev ** 2).sum())
    if denom <= 0:
        return 1.0
    slope = float((x_dev * (y - y_mean)).sum()) / denom
    return slope * (m - 1) / y_mean


def _clip01(x: float) -> float:
    if pd.isna(x):
        return 0.0
    return float(min(max(x, 0.0), 1.0))


def _base_score(box_range: float, vol_dry_x: float, rps_chg: float,
                vol_expand_x: float, drawdown: float, cfg: QuietBaseConfig) -> float:
    """
    综合评分 (0~100), 用于排序。
    越缩量、箱体越窄、RPS 抬升越多、量价配合越好、跌得越透 -> 分越高。
    """
    s_vol_dry = _clip01((cfg.vol_dry_max - vol_dry_x) / cfg.vol_dry_max)
    s_box = _clip01((cfg.box_range_max - box_range) / cfg.box_range_max)
    s_rps = _clip01(rps_chg / 30.0)
    s_vp = _clip01((vol_expand_x - 1.0) / 3.0)
    s_dd = _clip01((drawdown - cfg.min_drawdown) / 0.30)
    score = 0.25 * s_vol_dry + 0.20 * s_box + 0.25 * s_rps + 0.20 * s_vp + 0.10 * s_dd
    return round(score * 100, 1)


def _scan_one(sdf: pd.DataFrame, target_dt: pd.Timestamp,
              cfg: QuietBaseConfig) -> Optional[dict]:
    sdf = sdf[sdf["trade_date"] <= target_dt]
    if len(sdf) < cfg.min_bars:
        return None
    sdf = sdf.reset_index(drop=True)
    today = sdf.iloc[-1]

    # E3 今日有成交(未停牌) —— 最后一根 K 线必须就是目标日
    if today["trade_date"] != target_dt:
        return None
    if today.get("vol", 0) <= 0:
        return None

    if (
        pd.isna(today.get("RPS_120"))
        or pd.isna(today.get("MA20"))
        or pd.isna(today.get("AMT20"))
    ):
        return None

    n = len(sdf)
    base_end = n - cfg.recent_gap                    # 不含
    base_start = base_end - cfg.base_window
    drop_start = max(0, base_start - cfg.drop_lookback)
    if base_start <= drop_start:
        return None

    base = sdf.iloc[base_start:base_end]
    drop = sdf.iloc[drop_start:base_start]
    recent = sdf.iloc[base_end:]
    if base.empty or drop.empty or recent.empty:
        return None

    close = float(today["close"])
    # 箱体上下沿用收盘价分位数, 抗单日插针 / 一字板; 真实极值另存供参考与回撤计算
    box_high = float(base["close"].quantile(cfg.box_quantile_hi))
    box_low = float(base["close"].quantile(cfg.box_quantile_lo))
    base_high_raw = float(base["high"].max())
    base_low_raw = float(base["low"].min())
    pre_high = float(drop["high"].max())
    if box_low <= 0 or box_high <= 0 or base_low_raw <= 0 or pre_high <= 0:
        return None

    base_vol = float(base["vol"].mean())
    drop_vol = float(drop["vol"].mean())
    if base_vol <= 0 or drop_vol <= 0:
        return None

    # ===== [A] 调整充分 =====
    drawdown = 1 - base_low_raw / pre_high
    vs_pre_high = close / pre_high - 1
    a1 = drawdown >= cfg.min_drawdown
    a2 = close <= pre_high * (1 - cfg.min_below_pre_high)
    a_ok = bool(a1 and a2)

    # ===== [B] 横盘充分 / 筹码干净 =====
    box_range = (box_high - box_low) / box_low
    b1 = box_range <= cfg.box_range_max

    base_span_days = (base.iloc[-1]["trade_date"] - base.iloc[0]["trade_date"]).days
    b2 = (
        base_span_days <= cfg.base_window * cfg.base_span_max_ratio
        and bool((base["vol"] > 0).all())
    )

    base_drift = _regression_drift(base["close"].values)
    b3 = abs(base_drift) <= cfg.base_drift_max

    vol_dry_x = base_vol / drop_vol
    b4 = vol_dry_x <= cfg.vol_dry_max

    base_close_mean = float(base["close"].mean())
    close_cv = float(base["close"].std()) / base_close_mean if base_close_mean > 0 else 1.0
    b5 = close_cv <= cfg.close_cv_max

    b_ok = bool(b1 and b2 and b3 and b4 and b5)

    # ===== [C] 量价关系 / 可买入 =====
    ma20_streak = _streak_above(sdf["close"].values, sdf["MA20"].values)
    c1 = close > float(today["MA20"]) and cfg.ma20_streak_min <= ma20_streak <= cfg.ma20_streak_max

    ma20_prev = sdf.iloc[-1 - cfg.ma20_rise_lookback]["MA20"] if n > cfg.ma20_rise_lookback else np.nan
    if pd.isna(ma20_prev):
        c2 = True
        c2_watch = True
    else:
        c2 = float(today["MA20"]) >= float(ma20_prev)
        c2_watch = float(today["MA20"]) >= float(ma20_prev) * (1 - cfg.ma20_flat_tol)

    box_ext = close / box_high - 1
    c3 = cfg.box_ext_min <= box_ext <= cfg.box_ext_max
    # WATCH 档还在箱体内蓄势, 不该要求它贴着箱顶。
    # 用箱体内相对位置(而非距箱顶的绝对百分比)判断: 后者对宽窄箱体含义不一致 ——
    # 振幅 16% 的箱体里"距箱顶 -10%"其实只在箱体 27% 处, 属下半部。
    # 只要箱体结构没被跌破(box_pos >= 0) 且未追高, 就值得挂进观察池。
    box_pos = (close - box_low) / (box_high - box_low) if box_high > box_low else 0.0
    c3_watch = box_pos >= cfg.watch_box_pos_min and box_ext <= cfg.box_ext_max

    vol_expand_x = float(sdf["vol"].tail(cfg.vol_confirm_window).max()) / base_vol
    # 涨日 / 跌日均量: 多取一根 K 线以便对首日做 diff
    rec2 = sdf.iloc[max(0, base_end - 1):]
    chg = rec2["close"].diff()
    up_vol = float(rec2.loc[chg > 0, "vol"].mean())
    dn_vol = float(rec2.loc[chg < 0, "vol"].mean())
    if pd.isna(up_vol):
        up_down_vol_ratio = 0.0
    elif pd.isna(dn_vol) or dn_vol <= 0:
        up_down_vol_ratio = np.inf
    else:
        up_down_vol_ratio = up_vol / dn_vol
    c4 = vol_expand_x >= cfg.vol_expand_x and up_down_vol_ratio >= cfg.up_down_vol_ratio_min

    close5 = float(sdf.iloc[-6]["close"]) if n > 6 else np.nan
    close20 = float(sdf.iloc[-21]["close"]) if n > 21 else np.nan
    ret5 = close / close5 - 1 if close5 and close5 > 0 else 0.0
    ret20 = close / close20 - 1 if close20 and close20 > 0 else 0.0
    c5 = ret5 <= cfg.max_ret5 and ret20 <= cfg.max_ret20

    vs_ma20 = close / float(today["MA20"]) - 1
    c6 = vs_ma20 <= cfg.max_vs_ma20

    c_ok = bool(c1 and c2 and c3 and c4 and c5 and c6)

    # ===== [D] RPS 拐头 =====
    rps_now = float(today["RPS_120"])
    rps_prev = (
        sdf.iloc[-1 - cfg.rps_slope_lookback]["RPS_120"]
        if n > cfg.rps_slope_lookback else np.nan
    )
    if pd.isna(rps_prev):
        return None
    rps_chg = rps_now - float(rps_prev)
    d1 = rps_chg >= cfg.rps_rise_min
    d2 = (rps_now >= cfg.rps_floor) if cfg.use_rps_floor else True
    d_ok = bool(d1 and d2)

    # ===== [E] 风控 =====
    amount20_avg = float(today["AMT20"])
    e_ok = bool(amount20_avg >= cfg.min_amount)

    # ===== 分档 =====
    is_buy = bool(a_ok and b_ok and c_ok and d_ok and e_ok)
    # WATCH: 位置合理(在箱体上半部 + 不追高) 但尚未放量站上 MA20 —— 仍在箱体内蓄势
    is_watch = bool(
        not is_buy
        and a_ok and b_ok and d_ok and e_ok
        and c3_watch and c5 and c6 and c2_watch
    )
    signal = "可买入" if is_buy else ("观察" if is_watch else "")

    return {
        "ts_code": today["ts_code"],
        "trade_date": today["trade_date"],
        "close": close,
        "SIGNAL": signal,
        "base_start": base.iloc[0]["trade_date"],
        "base_end": base.iloc[-1]["trade_date"],
        "box_high": round(box_high, 3),
        "box_low": round(box_low, 3),
        "base_high_raw": round(base_high_raw, 3),
        "base_low_raw": round(base_low_raw, 3),
        "box_range_pct": round(box_range * 100, 2),
        "base_drift_pct": round(base_drift * 100, 2),
        "close_cv": round(close_cv, 4),
        "vol_dry_x": round(vol_dry_x, 2),
        "drawdown_pct": round(drawdown * 100, 2),
        "vs_pre_high_pct": round(vs_pre_high * 100, 2),
        "ma20_streak": ma20_streak,
        "vs_ma20_pct": round(vs_ma20 * 100, 2),
        "box_ext_pct": round(box_ext * 100, 2),
        "box_pos": round(box_pos, 3),
        "vol_expand_x": round(vol_expand_x, 2),
        "up_down_vol_ratio": round(up_down_vol_ratio, 2) if np.isfinite(up_down_vol_ratio) else np.inf,
        "ret5_pct": round(ret5 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "RPS_120": round(rps_now, 1),
        "rps_chg20": round(rps_chg, 1),
        "amount20_avg": round(amount20_avg, 0),
        "base_score": _base_score(box_range, vol_dry_x, rps_chg, vol_expand_x, drawdown, cfg),
        "A_OK": a_ok,
        "B_OK": b_ok,
        "C_OK": c_ok,
        "D_OK": d_ok,
        "E_OK": e_ok,
        "BUY": is_buy,
        "WATCH": is_watch,
    }


def screen_quiet_base(
    df_daily: pd.DataFrame,
    target_date: Optional[str] = None,
    config: Optional[QuietBaseConfig] = None,
    candidate_codes: Optional[list] = None,
    exclude_codes: Optional[Sequence[str]] = None,
    exclude_prefixes: Optional[Sequence[str]] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    全市场扫描"深跌后缩量横盘、现已站上 MA20"的低位再启动候选。

    参数
    ----
    df_daily : 全市场日线 (含 RPS_120 历史序列), 至少 ~min_bars 天
    target_date : YYYYMMDD, None 取最新
    config : QuietBaseConfig
    candidate_codes : 限定股票池 (None=全市场)
    exclude_codes : 排除清单 (如 ST / 退市股, 需人工维护)
    exclude_prefixes : 排除板块前缀 (如 ["688", "300"] 排除科创/创业板)

    返回
    ----
    pd.DataFrame: 通过 BUY / WATCH 任一档的票, 含基底与量价明细;
                  排序: 可买入优先, 再按 base_score 降序
    """
    cfg = config or QuietBaseConfig()
    df = df_daily
    if candidate_codes:
        df = df[df["ts_code"].isin(candidate_codes)]
    if exclude_codes:
        df = df[~df["ts_code"].isin(set(exclude_codes))]
    if exclude_prefixes:
        drop_mask = pd.Series(False, index=df.index)
        for pref in exclude_prefixes:
            drop_mask |= df["ts_code"].str.startswith(pref)
        df = df[~drop_mask]

    if df.empty:
        if verbose:
            print("[静默筑底] 股票池为空")
        return pd.DataFrame()

    if "RPS_120" not in df.columns:
        raise ValueError("df_daily 缺少 RPS_120 列，请先调用 rps_calculator.calculate_rps")

    df = _add_indicators(df, cfg)

    if target_date is None:
        target_dt = df["trade_date"].max()
    else:
        td = pd.Timestamp(target_date)
        valid = df["trade_date"].unique()
        valid = valid[valid <= td]
        if len(valid) == 0:
            if verbose:
                print(f"[静默筑底] {target_date} 之前无数据")
            return pd.DataFrame()
        target_dt = pd.Timestamp(valid.max())

    if verbose:
        print(f"[静默筑底] 目标日: {target_dt.strftime('%Y-%m-%d')}, 股票池: {df['ts_code'].nunique()}")
        print(
            f"[静默筑底] 基底期=倒数第{cfg.recent_gap + cfg.base_window}~{cfg.recent_gap}根K线({cfg.base_window}日)  "
            f"近期段={cfg.recent_gap}日\n"
            f"           A 调整: 回撤≥{cfg.min_drawdown:.0%} 距前高≥{cfg.min_below_pre_high:.0%} | "
            f"B 横盘: 箱体≤{cfg.box_range_max:.0%} 漂移≤{cfg.base_drift_max:.0%} "
            f"地量≤{cfg.vol_dry_max:.2f}x CV≤{cfg.close_cv_max:.2f}\n"
            f"           C 买点: 站上MA20 {cfg.ma20_streak_min}~{cfg.ma20_streak_max}日 "
            f"距箱顶{cfg.box_ext_min:.0%}~{cfg.box_ext_max:.0%} 放量≥{cfg.vol_expand_x:.1f}x | "
            f"D RPS: 近{cfg.rps_slope_lookback}日抬升≥{cfg.rps_rise_min:.0f}"
        )

    records: List[dict] = []
    for _code, sdf in df.groupby("ts_code", sort=False):
        rec = _scan_one(sdf, target_dt, cfg)
        if rec is not None:
            records.append(rec)

    if not records:
        if verbose:
            print("[静默筑底] 无可计算样本")
        return pd.DataFrame()

    sigs = pd.DataFrame(records)
    passed = sigs[sigs["SIGNAL"] != ""].copy()

    if verbose:
        total = len(sigs)
        print(
            f"  可计算样本: {total}  "
            f"A 调整充分: {int(sigs['A_OK'].sum())}  "
            f"B 横盘干净: {int((sigs['A_OK'] & sigs['B_OK']).sum())}  "
            f"D RPS拐头: {int((sigs['A_OK'] & sigs['B_OK'] & sigs['D_OK']).sum())}"
        )
        print(
            f"  可买入: {int(sigs['BUY'].sum())}  观察: {int(sigs['WATCH'].sum())}  "
            f"合计入选: {len(passed)}"
        )

    if passed.empty:
        return passed

    passed["_rank"] = np.where(passed["BUY"], 0, 1)
    passed = passed.sort_values(
        ["_rank", "base_score"], ascending=[True, False]
    ).drop(columns=["_rank"]).reset_index(drop=True)
    return passed


def print_quiet_base_summary(result: pd.DataFrame, top_n: int = 40) -> None:
    if result.empty:
        print("  无静默筑底候选")
        return
    buy = int(result["BUY"].sum())
    watch = int(result["WATCH"].sum())
    print(f"\n  静默筑底候选: {len(result)} 只 (可买入 {buy} / 观察 {watch})，前 {min(top_n, len(result))} 只:")
    show = result.head(top_n)[[
        "ts_code", "SIGNAL", "close", "box_low", "box_high", "box_range_pct",
        "vol_dry_x", "drawdown_pct", "ma20_streak", "box_pos", "box_ext_pct",
        "vol_expand_x", "RPS_120", "rps_chg20", "base_score",
    ]].copy()
    show.columns = ["代码", "信号", "收盘", "箱底", "箱顶", "箱体振幅%",
                    "地量比", "回撤%", "站上MA20天", "箱内位置", "距箱顶%",
                    "放量比", "RPS120", "RPS抬升", "评分"]
    print(show.to_string(index=False))


# =============================================================================
# CLI 入口
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="静默筑底 · 低位再启动选股")
    parser.add_argument("--data", type=str, required=True, help="日线数据 (parquet/csv, 含 RPS_120)")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYYMMDD")
    parser.add_argument("--base-window", type=int, default=42, help="横盘基底窗口")
    parser.add_argument("--recent-gap", type=int, default=20, help="近期段长度")
    parser.add_argument("--box-range-max", type=float, default=0.22, help="箱体振幅上限")
    parser.add_argument("--vol-dry-max", type=float, default=0.85, help="地量比上限")
    parser.add_argument("--rps-rise-min", type=float, default=10.0, help="RPS 抬升下限")
    parser.add_argument("--output", type=str, default=None, help="输出 CSV 路径")
    args = parser.parse_args()

    if args.data.endswith(".parquet"):
        data = pd.read_parquet(args.data)
    else:
        data = pd.read_csv(args.data)
    data["trade_date"] = pd.to_datetime(data["trade_date"])

    cfg = QuietBaseConfig(
        base_window=args.base_window,
        recent_gap=args.recent_gap,
        box_range_max=args.box_range_max,
        vol_dry_max=args.vol_dry_max,
        rps_rise_min=args.rps_rise_min,
    )
    res = screen_quiet_base(data, target_date=args.date, config=cfg)
    print_quiet_base_summary(res)

    if args.output and not res.empty:
        if args.output.endswith(".parquet"):
            res.to_parquet(args.output, index=False)
        else:
            res.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"\n结果已保存到 {args.output}")
