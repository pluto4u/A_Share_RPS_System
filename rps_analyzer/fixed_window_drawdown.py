"""固定日期窗口的最大回撤选股。

核心口径是“前一交易日及更早的最高价 -> 后续交易日最低价”。
这样不会把月内低点先出现、随后才创出高点的情况误判为回撤，也不假设
同一根日 K 内最高价和最低价的先后顺序。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from rps_analyzer.ma20_screener import REQUIRED_COLUMNS, resolve_target_date


@dataclass(frozen=True)
class FixedWindowDrawdownConfig:
    window_start: str = "2026-07-01"
    window_end: str = "2026-07-31"
    reference_date: str = "2026-07-21"
    target_date: str = "2026-08-06"
    min_drawdown: float = 0.30
    max_drawdown: float = 0.45
    short_ma: int = 5
    long_ma: int = 20
    min_window_bars: int = 20
    target_date_coverage: float = 0.90
    target_date_lookback: int = 10
    recommended_min_amount20: float = 1e8
    recommended_min_above_reference: float = 0.03
    recommended_max_above_reference: float = 0.25
    recommended_max_vs_ma20: float = 0.10
    recommended_max_ret5: float = 0.15

    def dates(self) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
        start = pd.Timestamp(self.window_start)
        end = pd.Timestamp(self.window_end)
        reference = pd.Timestamp(self.reference_date)
        target = pd.Timestamp(self.target_date)
        if not start <= reference <= end < target:
            raise ValueError("日期必须满足 window_start <= reference_date <= window_end < target_date")
        if not 0 <= self.min_drawdown <= self.max_drawdown < 1:
            raise ValueError("最大回撤阈值必须满足 0 <= min <= max < 1")
        if self.short_ma <= 0 or self.long_ma <= self.short_ma:
            raise ValueError("均线周期必须满足 0 < short_ma < long_ma")
        return start, end, reference, target


def _normalise_codes(codes: Optional[Iterable[str]]) -> set[str]:
    if not codes:
        return set()
    return {str(code).strip().upper() for code in codes if str(code).strip()}


def _prepare_and_audit(
    df: pd.DataFrame,
    cfg: FixedWindowDrawdownConfig,
) -> tuple[pd.DataFrame, dict]:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")

    start, end, reference, requested_target = cfg.dates()
    resolved_target = resolve_target_date(
        df,
        requested_target,
        coverage_ratio=cfg.target_date_coverage,
        lookback=cfg.target_date_lookback,
    )
    if resolved_target != requested_target:
        raise ValueError(
            f"目标日 {requested_target:%Y-%m-%d} 数据覆盖不足；"
            f"最新可靠交易日仅为 {resolved_target:%Y-%m-%d}，拒绝使用不完整数据"
        )

    source_rows = len(df)
    source_stocks = int(df["ts_code"].nunique())
    work = df.loc[:, REQUIRED_COLUMNS].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work["ts_code"] = work["ts_code"].astype(str).str.strip().str.upper()
    for column in ("open", "high", "low", "close", "vol", "amount"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work[work["trade_date"] <= requested_target]
    work = work.sort_values(["ts_code", "trade_date"])

    duplicate_mask = work.duplicated(["ts_code", "trade_date"], keep=False)
    finite_mask = np.isfinite(work[["open", "high", "low", "close", "vol", "amount"]]).all(axis=1)
    positive_price = (work[["open", "high", "low", "close"]] > 0).all(axis=1)
    ohlc_consistent = (
        (work["high"] >= work[["open", "close", "low"]].max(axis=1))
        & (work["low"] <= work[["open", "close", "high"]].min(axis=1))
        & (work["high"] >= work["low"])
    )
    nonnegative_trade = (work["vol"] >= 0) & (work["amount"] >= 0)
    identity_valid = work["trade_date"].notna() & work["ts_code"].ne("")
    invalid_mask = ~(
        finite_mask & positive_price & ohlc_consistent & nonnegative_trade & identity_valid
    )

    bad_codes = set(work.loc[duplicate_mask | invalid_mask, "ts_code"])
    if bad_codes:
        work = work[~work["ts_code"].isin(bad_codes)]

    last_dates = work.groupby("ts_code")["trade_date"].max()
    stale_codes = set(last_dates[last_dates != requested_target].index)
    if stale_codes:
        work = work[~work["ts_code"].isin(stale_codes)]

    required_dates = {start, end, reference, requested_target}
    date_sets = work.groupby("ts_code")["trade_date"].agg(lambda values: set(values))
    missing_anchor_codes = {code for code, dates in date_sets.items() if not required_dates.issubset(dates)}
    if missing_anchor_codes:
        work = work[~work["ts_code"].isin(missing_anchor_codes)]

    window_counts = work[work["trade_date"].between(start, end)].groupby("ts_code").size()
    short_window_codes = set(window_counts[window_counts < cfg.min_window_bars].index)
    if short_window_codes:
        work = work[~work["ts_code"].isin(short_window_codes)]

    history_counts = work.groupby("ts_code").cumcount() + 1
    target_rows = work["trade_date"].eq(requested_target)
    short_history_codes = set(work.loc[target_rows & (history_counts < cfg.long_ma), "ts_code"])
    if short_history_codes:
        work = work[~work["ts_code"].isin(short_history_codes)]

    audit = {
        "target_date": requested_target.strftime("%Y-%m-%d"),
        "window_start": start.strftime("%Y-%m-%d"),
        "window_end": end.strftime("%Y-%m-%d"),
        "reference_date": reference.strftime("%Y-%m-%d"),
        "source_rows": int(source_rows),
        "source_stocks": source_stocks,
        "duplicate_rows": int(duplicate_mask.sum()),
        "invalid_rows": int(invalid_mask.sum()),
        "invalid_codes_excluded": int(len(bad_codes)),
        "stale_codes_excluded": int(len(stale_codes)),
        "missing_anchor_codes_excluded": int(len(missing_anchor_codes)),
        "short_window_codes_excluded": int(len(short_window_codes)),
        "short_history_codes_excluded": int(len(short_history_codes)),
        "quality_pass_stocks": int(work["ts_code"].nunique()),
        "config": asdict(cfg),
    }
    return work.reset_index(drop=True), audit


def _one_stock_metrics(group: pd.DataFrame, cfg: FixedWindowDrawdownConfig) -> Optional[dict]:
    start, end, reference, target = cfg.dates()
    group = group.sort_values("trade_date").reset_index(drop=True)
    july = group[group["trade_date"].between(start, end)].copy()
    target_rows = group[group["trade_date"].eq(target)]
    reference_rows = group[group["trade_date"].eq(reference)]
    if len(july) < cfg.min_window_bars or target_rows.empty or reference_rows.empty:
        return None

    # 只允许“此前高点 -> 后续低点”。shift(1) 排除同一根日 K 内未知的先后顺序。
    july["prior_peak"] = july["high"].cummax().shift(1)
    july["drawdown"] = (july["prior_peak"] - july["low"]) / july["prior_peak"]
    july["drawdown"] = july["drawdown"].clip(lower=0)
    valid_dd = july.dropna(subset=["drawdown", "prior_peak"])
    if valid_dd.empty:
        return None

    trough_row = valid_dd.loc[valid_dd["drawdown"].idxmax()]
    prior = july[july["trade_date"] < trough_row["trade_date"]]
    peak_row = prior.loc[prior["high"].idxmax()]

    target_position = int(group.index[group["trade_date"].eq(target)][0])
    if target_position + 1 < cfg.long_ma:
        return None
    history = group.iloc[: target_position + 1]
    close = float(history.iloc[-1]["close"])
    ma5_series = history["close"].rolling(cfg.short_ma, min_periods=cfg.short_ma).mean()
    ma20_series = history["close"].rolling(cfg.long_ma, min_periods=cfg.long_ma).mean()
    ma5 = float(ma5_series.iloc[-1])
    ma20 = float(ma20_series.iloc[-1])
    ma20_ref5 = float(ma20_series.iloc[-6]) if len(ma20_series) >= cfg.long_ma + 5 else np.nan
    amount20 = float(history["amount"].tail(20).mean())
    vol20 = float(history["vol"].tail(20).mean())
    ret1 = float(history["close"].pct_change(1, fill_method=None).iloc[-1])
    ret5 = float(history["close"].pct_change(5, fill_method=None).iloc[-1])
    prev_close = float(history.iloc[-2]["close"])
    reference_high = float(reference_rows.iloc[-1]["high"])

    july_high_row = july.loc[july["high"].idxmax()]
    july_low_row = july.loc[july["low"].idxmin()]
    max_drawdown = float(trough_row["drawdown"])
    range_drop = float(1.0 - july_low_row["low"] / july_high_row["high"])

    close_returns = july["close"].pct_change(fill_method=None)
    min_daily_return = float(close_returns.min()) if close_returns.notna().any() else np.nan
    suspected_corporate_action = bool(min_daily_return < -0.22)

    return {
        "trade_date": target,
        "ts_code": str(group.iloc[-1]["ts_code"]),
        "close": close,
        "prev_close": prev_close,
        "reference_high_20260721": reference_high,
        "above_reference_high_pct": (close / reference_high - 1.0) * 100.0,
        "first_breakout_today": prev_close <= reference_high < close,
        "ma5": ma5,
        "ma20": ma20,
        "ma5_above_ma20_pct": (ma5 / ma20 - 1.0) * 100.0,
        "close_vs_ma20_pct": (close / ma20 - 1.0) * 100.0,
        "ma20_slope5_pct": (ma20 / ma20_ref5 - 1.0) * 100.0,
        "ret1_pct": ret1 * 100.0,
        "ret5_pct": ret5 * 100.0,
        "vol_ratio20": float(history.iloc[-1]["vol"]) / vol20 if vol20 > 0 else np.nan,
        "amount20_yi": amount20 / 1e8,
        "july_max_drawdown_pct": max_drawdown * 100.0,
        "drawdown_peak_date": pd.Timestamp(peak_row["trade_date"]),
        "drawdown_peak_price": float(peak_row["high"]),
        "drawdown_trough_date": pd.Timestamp(trough_row["trade_date"]),
        "drawdown_trough_price": float(trough_row["low"]),
        "july_range_drop_pct": range_drop * 100.0,
        "july_high_date": pd.Timestamp(july_high_row["trade_date"]),
        "july_high": float(july_high_row["high"]),
        "july_low_date": pd.Timestamp(july_low_row["trade_date"]),
        "july_low": float(july_low_row["low"]),
        "july_bars": int(len(july)),
        "min_july_daily_return_pct": min_daily_return * 100.0,
        "suspected_corporate_action": suspected_corporate_action,
    }


def screen_fixed_window_drawdown(
    df: pd.DataFrame,
    config: Optional[FixedWindowDrawdownConfig] = None,
    *,
    exclude_codes: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """执行固定窗口最大回撤选股，并在 ``attrs['audit']`` 中保存数据审计。"""

    cfg = config or FixedWindowDrawdownConfig()
    work, audit = _prepare_and_audit(df, cfg)
    excluded = _normalise_codes(exclude_codes)
    quality_codes = set(work["ts_code"].unique())
    audit["explicit_codes_excluded"] = int(len(quality_codes & excluded))
    if excluded:
        work = work[~work["ts_code"].isin(excluded)]

    rows = []
    for _, group in work.groupby("ts_code", sort=False):
        metrics = _one_stock_metrics(group, cfg)
        if metrics is not None:
            rows.append(metrics)

    columns = [
        "trade_date", "ts_code", "close", "prev_close", "reference_high_20260721",
        "above_reference_high_pct", "first_breakout_today", "ma5", "ma20", "ma5_above_ma20_pct",
        "close_vs_ma20_pct", "ma20_slope5_pct", "ret1_pct", "ret5_pct",
        "vol_ratio20", "amount20_yi",
        "july_max_drawdown_pct", "drawdown_peak_date", "drawdown_peak_price",
        "drawdown_trough_date", "drawdown_trough_price", "july_range_drop_pct",
        "july_high_date", "july_high", "july_low_date", "july_low", "july_bars",
        "min_july_daily_return_pct", "suspected_corporate_action",
    ]
    metrics_df = pd.DataFrame(rows, columns=columns)
    if metrics_df.empty:
        result = metrics_df
    else:
        accepted = (
            metrics_df["july_max_drawdown_pct"].between(
                cfg.min_drawdown * 100.0,
                cfg.max_drawdown * 100.0,
                inclusive="both",
            )
            & (metrics_df["close"] > metrics_df["reference_high_20260721"])
            & (metrics_df["ma5"] > metrics_df["ma20"])
        )
        result = metrics_df.loc[accepted].sort_values(
            ["suspected_corporate_action", "above_reference_high_pct", "july_max_drawdown_pct"],
            ascending=[True, False, True],
        ).reset_index(drop=True)

        if not result.empty:
            strict = (
                ~result["suspected_corporate_action"]
                & (result["close"] > result["ma5"])
                & (result["ma20_slope5_pct"] > 0)
                & (result["amount20_yi"] >= cfg.recommended_min_amount20 / 1e8)
                & result["above_reference_high_pct"].between(
                    cfg.recommended_min_above_reference * 100.0,
                    cfg.recommended_max_above_reference * 100.0,
                    inclusive="both",
                )
                & (result["close_vs_ma20_pct"] <= cfg.recommended_max_vs_ma20 * 100.0)
                & (result["ret5_pct"] <= cfg.recommended_max_ret5 * 100.0)
            )
            result["recommended"] = strict
            result["correction_flags"] = ""
            checks = [
                (result["suspected_corporate_action"], "疑似除权/异常跳变"),
                (result["close"] <= result["ma5"], "收盘未站上MA5"),
                (result["ma20_slope5_pct"] <= 0, "MA20未上行"),
                (result["amount20_yi"] < cfg.recommended_min_amount20 / 1e8, "流动性不足"),
                (result["above_reference_high_pct"] < cfg.recommended_min_above_reference * 100.0, "突破0721高点幅度不足"),
                (result["above_reference_high_pct"] > cfg.recommended_max_above_reference * 100.0, "超过0721高点幅度过大"),
                (result["close_vs_ma20_pct"] > cfg.recommended_max_vs_ma20 * 100.0, "偏离MA20过大"),
                (result["ret5_pct"] > cfg.recommended_max_ret5 * 100.0, "近5日涨幅过大"),
            ]
            for mask, label in checks:
                result.loc[mask, "correction_flags"] = result.loc[mask, "correction_flags"].map(
                    lambda value: f"{value}；{label}" if value else label
                )
            result = result.sort_values(
                ["recommended", "suspected_corporate_action", "above_reference_high_pct"],
                ascending=[False, True, False],
            ).reset_index(drop=True)

    audit["formula_match_stocks"] = int(len(result))
    audit["suspected_corporate_action_matches"] = (
        0 if result.empty else int(result["suspected_corporate_action"].sum())
    )
    audit["recommended_stocks"] = (
        0 if result.empty or "recommended" not in result else int(result["recommended"].sum())
    )
    result.attrs["audit"] = audit
    return result


def screen_reference_high_breakout(
    df: pd.DataFrame,
    config: Optional[FixedWindowDrawdownConfig] = None,
    *,
    exclude_codes: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """筛选目标日首次收盘突破参考日最高价的股票。

    这是早期反转扫描，不强制 MA5 已在 MA20 上方。截图所示的快速反转通常是
    收盘先站上 MA20，随后 MA5 才形成金叉，因此用 ``close > ma20`` 做及时确认，
    同时把 7 月最大回撤作为 S/A 分级而不是一刀切。
    """

    cfg = config or FixedWindowDrawdownConfig()
    work, audit = _prepare_and_audit(df, cfg)
    excluded = _normalise_codes(exclude_codes)
    quality_codes = set(work["ts_code"].unique())
    audit["explicit_codes_excluded"] = int(len(quality_codes & excluded))
    if excluded:
        work = work[~work["ts_code"].isin(excluded)]

    rows = []
    for _, group in work.groupby("ts_code", sort=False):
        metrics = _one_stock_metrics(group, cfg)
        if metrics is not None:
            rows.append(metrics)

    result = pd.DataFrame(rows)
    if result.empty:
        audit["first_breakout_stocks"] = 0
        audit["core_breakout_stocks"] = 0
        result.attrs["audit"] = audit
        return result

    first_break = (
        (result["prev_close"] <= result["reference_high_20260721"])
        & (result["close"] > result["reference_high_20260721"])
    )
    result = result.loc[first_break].copy()
    if result.empty:
        audit["first_breakout_stocks"] = 0
        audit["core_breakout_stocks"] = 0
        result.attrs["audit"] = audit
        return result

    timely_confirmation = (
        (result["close"] > result["ma20"])
        & (result["vol_ratio20"] >= 1.20)
        & (result["amount20_yi"] >= 1.0)
        & ~result["suspected_corporate_action"]
    )
    s_grade = timely_confirmation & result["july_max_drawdown_pct"].between(
        30.0, 45.0, inclusive="both"
    )
    a_grade = timely_confirmation & result["july_max_drawdown_pct"].gt(45.0) & result[
        "july_max_drawdown_pct"
    ].le(60.0)
    result["breakout_grade"] = np.select(
        [s_grade, a_grade, timely_confirmation],
        ["S_原始回撤30-45", "A_深跌回撤45-60", "B_趋势量价通过"],
        default="观察",
    )
    result["core_breakout"] = s_grade | a_grade
    result["ma5_above_ma20"] = result["ma5"] > result["ma20"]
    grade_order = result["breakout_grade"].map(
        {"S_原始回撤30-45": 0, "A_深跌回撤45-60": 1, "B_趋势量价通过": 2, "观察": 3}
    )
    result = result.assign(_grade_order=grade_order).sort_values(
        ["_grade_order", "vol_ratio20", "above_reference_high_pct"],
        ascending=[True, False, False],
    ).drop(columns="_grade_order").reset_index(drop=True)

    audit["first_breakout_stocks"] = int(len(result))
    audit["s_grade_stocks"] = int(s_grade.sum())
    audit["a_grade_stocks"] = int(a_grade.sum())
    audit["core_breakout_stocks"] = int((s_grade | a_grade).sum())
    result.attrs["audit"] = audit
    return result


def screen_reference_close_window_breakout(
    df: pd.DataFrame,
    config: Optional[FixedWindowDrawdownConfig] = None,
    *,
    breakout_start: str = "2026-08-03",
    breakout_end: Optional[str] = None,
    exclude_codes: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """筛选一个日期窗口内首次收盘突破参考日收盘价、且目标日仍守住的股票。"""

    cfg = config or FixedWindowDrawdownConfig()
    start = pd.Timestamp(breakout_start)
    end = pd.Timestamp(breakout_end or cfg.target_date)
    _, _, reference_date, target = cfg.dates()
    if not reference_date < start <= end == target:
        raise ValueError("必须满足 reference_date < breakout_start <= breakout_end == target_date")

    work, audit = _prepare_and_audit(df, cfg)
    excluded = _normalise_codes(exclude_codes)
    quality_codes = set(work["ts_code"].unique())
    audit["explicit_codes_excluded"] = int(len(quality_codes & excluded))
    if excluded:
        work = work[~work["ts_code"].isin(excluded)]

    rows = []
    for _, group in work.groupby("ts_code", sort=False):
        group = group.sort_values("trade_date").reset_index(drop=True)
        base = _one_stock_metrics(group, cfg)
        if base is None:
            continue
        reference_rows = group[group["trade_date"].eq(reference_date)]
        if reference_rows.empty:
            continue
        reference_close = float(reference_rows.iloc[-1]["close"])
        group["prev_close_for_cross"] = group["close"].shift(1)
        crossing = group[
            group["trade_date"].between(start, end)
            & (group["prev_close_for_cross"] <= reference_close)
            & (group["close"] > reference_close)
        ]
        if crossing.empty or float(group.iloc[-1]["close"]) <= reference_close:
            continue

        break_index = int(crossing.index[0])
        break_row = group.loc[break_index]
        history = group.iloc[: break_index + 1]
        break_ma20 = float(history["close"].tail(20).mean())
        break_vol20 = float(history["vol"].tail(20).mean())
        break_amount20 = float(history["amount"].tail(20).mean())
        day_range = float(break_row["high"] - break_row["low"])
        if day_range > 0:
            break_clv = float((break_row["close"] - break_row["low"]) / day_range)
            break_body = float(abs(break_row["close"] - break_row["open"]) / day_range)
            break_upper = float(
                (break_row["high"] - max(break_row["open"], break_row["close"])) / day_range
            )
        else:
            break_clv, break_body, break_upper = 0.5, 0.0, 0.0
        post_break = group.loc[break_index:]

        base.update(
            {
                "reference_close_20260721": reference_close,
                "break_date": pd.Timestamp(break_row["trade_date"]),
                "break_close": float(break_row["close"]),
                "breakout_pct": (float(break_row["close"]) / reference_close - 1.0) * 100.0,
                "hold_above_reference_pct": (float(group.iloc[-1]["close"]) / reference_close - 1.0)
                * 100.0,
                "break_vol_ratio20": (
                    float(break_row["vol"]) / break_vol20 if break_vol20 > 0 else np.nan
                ),
                "break_amount20_yi": break_amount20 / 1e8,
                "break_vs_ma20_pct": (float(break_row["close"]) / break_ma20 - 1.0) * 100.0,
                "break_clv": break_clv,
                "break_body_ratio": break_body,
                "break_upper_shadow_ratio": break_upper,
                "held_above_reference_since_break": bool(
                    (post_break["close"] > reference_close).all()
                ),
            }
        )
        rows.append(base)

    result = pd.DataFrame(rows)
    if result.empty:
        audit.update(
            {
                "breakout_window_start": start.strftime("%Y-%m-%d"),
                "breakout_window_end": end.strftime("%Y-%m-%d"),
                "raw_window_breakouts": 0,
                "quality_breakouts": 0,
                "core_drawdown_breakouts": 0,
                "shape_priority": 0,
            }
        )
        result.attrs["audit"] = audit
        return result

    quality = (
        (result["break_vol_ratio20"] >= 1.20)
        & (result["break_amount20_yi"] >= 1.0)
        & (result["close"] > result["ma20"])
        & ~result["suspected_corporate_action"]
    )
    core = quality & result["july_max_drawdown_pct"].between(30.0, 60.0, inclusive="both")
    good_break_candle = (
        result["breakout_pct"].between(0.5, 8.0, inclusive="both")
        & (result["break_clv"] >= 0.75)
        & (result["break_body_ratio"] >= 0.60)
        & (result["break_upper_shadow_ratio"] <= 0.25)
    )
    current_not_extended = (
        result["hold_above_reference_pct"].between(1.0, 12.0, inclusive="both")
        & result["close_vs_ma20_pct"].between(0.0, 8.0, inclusive="both")
        & (result["ret1_pct"] < 10.0)
        & (result["ret5_pct"] <= 15.0)
    )
    priority = (
        core
        & good_break_candle
        & current_not_extended
        & result["held_above_reference_since_break"]
    )
    observe = (
        core
        & ~priority
        & result["held_above_reference_since_break"]
        & (result["breakout_pct"] <= 12.0)
        & (result["hold_above_reference_pct"] <= 16.0)
        & (result["close_vs_ma20_pct"] <= 12.0)
        & (result["break_clv"] >= 0.65)
        & (result["break_upper_shadow_ratio"] <= 0.35)
    )
    result["quality_pass"] = quality
    result["core_drawdown_30_60"] = core
    result["strict_original_pass"] = (
        quality
        & result["july_max_drawdown_pct"].between(30.0, 45.0, inclusive="both")
        & (result["ma5"] > result["ma20"])
        & result["held_above_reference_since_break"]
    )
    result["strict_priority"] = result["strict_original_pass"] & priority
    result["shape_verdict"] = np.select(
        [priority, observe, core],
        ["A_形态优先", "B_继续观察", "C_暂时剔除"],
        default="宽口径记录",
    )
    result["trend_stage"] = np.where(result["ma5"] > result["ma20"], "MA5已上穿", "早期反转")
    result["drawdown_grade"] = np.select(
        [
            result["july_max_drawdown_pct"].between(30.0, 45.0, inclusive="both"),
            result["july_max_drawdown_pct"].gt(45.0)
            & result["july_max_drawdown_pct"].le(60.0),
        ],
        ["S_回撤30-45", "A_回撤45-60"],
        default="其他",
    )
    order = result["shape_verdict"].map(
        {"A_形态优先": 0, "B_继续观察": 1, "C_暂时剔除": 2, "宽口径记录": 3}
    )
    result = result.assign(_order=order).sort_values(
        ["_order", "break_date", "july_max_drawdown_pct", "break_vol_ratio20"],
        ascending=[True, False, True, False],
    ).drop(columns="_order").reset_index(drop=True)

    audit.update(
        {
            "breakout_window_start": start.strftime("%Y-%m-%d"),
            "breakout_window_end": end.strftime("%Y-%m-%d"),
            "raw_window_breakouts": int(len(result)),
            "quality_breakouts": int(quality.sum()),
            "core_drawdown_breakouts": int(core.sum()),
            "shape_priority": int(priority.sum()),
            "shape_observe": int(observe.sum()),
            "shape_reject": int((core & ~priority & ~observe).sum()),
            "strict_original_pass": int(result["strict_original_pass"].sum()),
            "strict_priority": int(result["strict_priority"].sum()),
        }
    )
    result.attrs["audit"] = audit
    return result
