"""
20 日均线趋势选股。

数据仅依赖通达信本地日线字段：
trade_date, ts_code, open, high, low, close, vol, amount。

策略把“站上 MA20”分成三个层次：
1. 放量收复 MA20：昨日在线下，今日放量阳线重新站上；
2. 缩量回踩 MA20：趋势中回踩均线、缩量并收回；
3. 趋势观察：趋势仍有效，但当天没有形成上述买点。

这不是“close > MA20”一条条件的宽泛扫描。公共过滤器还要求 MA20 上行、
MA20 不低于 MA60、价格不过度偏离 MA20、流动性达标，并排除陈旧或异常 K 线。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = (
    "trade_date",
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
)


@dataclass(frozen=True)
class Ma20Config:
    """20 日均线策略参数。比例均使用小数，例如 0.03 表示 3%。"""

    ma_period: int = 20
    trend_period: int = 60
    slope_lookback: int = 5
    min_ma20_slope: float = 0.0

    max_vs_ma20: float = 0.06
    reclaim_max_vs_ma20: float = 0.035
    pullback_max_vs_ma20: float = 0.025
    pullback_touch_buffer: float = 0.015
    pullback_lookback: int = 10
    pullback_min_prior_extension: float = 0.04

    reclaim_min_vol_ratio: float = 1.20
    reclaim_max_vol_ratio: float = 3.50
    pullback_max_vol_ratio: float = 0.95

    max_ret1: float = 0.07
    max_ret5: float = 0.15
    max_ret60: float = 0.60
    min_amount20: float = 8e7
    min_bars: int = 80

    target_date_coverage: float = 0.90
    target_date_lookback: int = 10

    @property
    def required_bars(self) -> int:
        return max(
            self.min_bars,
            self.trend_period + self.slope_lookback + 2,
            self.ma_period + self.pullback_lookback + 2,
        )


def resolve_target_date(
    df: pd.DataFrame,
    target_date: Optional[str | pd.Timestamp] = None,
    *,
    coverage_ratio: float = 0.90,
    lookback: int = 10,
) -> pd.Timestamp:
    """
    选择可靠的目标交易日。

    不直接取全表最大日期，因为单个损坏或误写到未来的 .day 文件可能污染最大值。
    在最近若干个日期中，只有覆盖股票数达到该窗口最大覆盖数一定比例的日期才有效。
    若传入周末/节假日，则向前回退至最近一个满足覆盖率要求的交易日。
    """

    if "trade_date" not in df.columns or "ts_code" not in df.columns:
        raise ValueError("数据必须包含 trade_date 与 ts_code")

    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    valid = pd.DataFrame({"trade_date": dates, "ts_code": df["ts_code"]}).dropna()
    if target_date is not None:
        requested = pd.Timestamp(target_date)
        valid = valid[valid["trade_date"] <= requested]
    if valid.empty:
        raise ValueError("目标日期之前没有可用日线数据")

    counts = valid.groupby("trade_date")["ts_code"].nunique().sort_index().tail(lookback)
    if counts.empty:
        raise ValueError("无法统计目标日期的数据覆盖率")
    threshold = float(counts.max()) * coverage_ratio
    eligible = counts[counts >= threshold]
    if eligible.empty:
        raise ValueError("最近交易日均未达到数据覆盖率门槛")
    return pd.Timestamp(eligible.index.max())


def _normalise_codes(codes: Optional[Iterable[str]]) -> set[str]:
    if not codes:
        return set()
    return {str(code).strip().upper() for code in codes if str(code).strip()}


def _quality_checked_window(
    df: pd.DataFrame,
    target_dt: pd.Timestamp,
    cfg: Ma20Config,
) -> tuple[pd.DataFrame, dict]:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")

    source_rows = len(df)
    source_stocks = int(df["ts_code"].nunique())
    work = df.loc[:, REQUIRED_COLUMNS].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work = work[work["trade_date"] <= target_dt]
    work["ts_code"] = work["ts_code"].astype(str).str.strip().str.upper()
    for column in ("open", "high", "low", "close", "vol", "amount"):
        work[column] = pd.to_numeric(work[column], errors="coerce")

    work = work.sort_values(["ts_code", "trade_date"])
    work = work.groupby("ts_code", sort=False, group_keys=False).tail(cfg.required_bars)

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
        finite_mask
        & positive_price
        & ohlc_consistent
        & nonnegative_trade
        & identity_valid
    )

    bad_codes = set(work.loc[invalid_mask | duplicate_mask, "ts_code"])
    if bad_codes:
        work = work[~work["ts_code"].isin(bad_codes)]

    last_dates = work.groupby("ts_code")["trade_date"].max()
    stale_codes = set(last_dates[last_dates != target_dt].index)
    if stale_codes:
        work = work[~work["ts_code"].isin(stale_codes)]

    bar_counts = work.groupby("ts_code").size()
    short_codes = set(bar_counts[bar_counts < cfg.required_bars].index)
    if short_codes:
        work = work[~work["ts_code"].isin(short_codes)]

    audit = {
        "target_date": target_dt.strftime("%Y-%m-%d"),
        "source_rows": int(source_rows),
        "source_stocks": source_stocks,
        "lookback_rows_checked": int(len(work)),
        "duplicate_rows": int(duplicate_mask.sum()),
        "invalid_rows": int(invalid_mask.sum()),
        "invalid_codes_excluded": int(len(bad_codes)),
        "stale_codes_excluded": int(len(stale_codes)),
        "short_history_codes_excluded": int(len(short_codes)),
        "quality_pass_stocks": int(work["ts_code"].nunique()),
    }
    return work.reset_index(drop=True), audit


def _add_indicators(df: pd.DataFrame, cfg: Ma20Config) -> pd.DataFrame:
    work = df.sort_values(["ts_code", "trade_date"]).copy()
    group = work.groupby("ts_code", sort=False)
    close_group = group["close"]
    vol_group = group["vol"]
    amount_group = group["amount"]

    work["ma20"] = close_group.transform(
        lambda values: values.rolling(cfg.ma_period, min_periods=cfg.ma_period).mean()
    )
    work["ma60"] = close_group.transform(
        lambda values: values.rolling(cfg.trend_period, min_periods=cfg.trend_period).mean()
    )
    work["vol20"] = vol_group.transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    work["amount20"] = amount_group.transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    work["ma20_ref"] = group["ma20"].shift(cfg.slope_lookback)
    work["prev_close"] = close_group.shift(1)
    work["prev_ma20"] = group["ma20"].shift(1)
    work["ret1"] = close_group.pct_change(1, fill_method=None)
    work["ret5"] = close_group.pct_change(5, fill_method=None)
    work["ret60"] = close_group.pct_change(60, fill_method=None)
    work["vs_ma20"] = work["close"] / work["ma20"] - 1.0
    work["ma20_slope"] = work["ma20"] / work["ma20_ref"] - 1.0
    work["vol_ratio20"] = work["vol"] / work["vol20"].replace(0, np.nan)

    prior_vs = group["vs_ma20"].shift(1)
    work["prior_max_vs_ma20"] = prior_vs.groupby(work["ts_code"], sort=False).transform(
        lambda values: values.rolling(
            cfg.pullback_lookback,
            min_periods=cfg.pullback_lookback,
        ).max()
    )
    return work


def _score_row(row: pd.Series, cfg: Ma20Config, signal: str) -> float:
    slope_score = np.clip(float(row["ma20_slope"]) / 0.03, 0.0, 1.0) * 30.0
    proximity_score = np.clip(1.0 - float(row["vs_ma20"]) / cfg.max_vs_ma20, 0.0, 1.0) * 30.0
    liquidity_ratio = max(float(row["amount20"]) / cfg.min_amount20, 1.0)
    liquidity_score = np.clip(np.log10(liquidity_ratio + 1.0) / np.log10(11.0), 0.0, 1.0) * 10.0
    signal_score = {
        "放量收复MA20": 30.0,
        "缩量回踩MA20": 28.0,
        "趋势观察": 10.0,
    }[signal]
    return round(float(np.clip(slope_score + proximity_score + liquidity_score + signal_score, 0, 100)), 1)


def screen_ma20(
    df: pd.DataFrame,
    target_date: Optional[str | pd.Timestamp] = None,
    config: Optional[Ma20Config] = None,
    *,
    exclude_codes: Optional[Iterable[str]] = None,
    exclude_prefixes: Optional[Sequence[str]] = None,
    include_watch: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """执行 20 日均线选股，并将数据质量审计结果写入 ``result.attrs['audit']``。"""

    cfg = config or Ma20Config()
    target_dt = resolve_target_date(
        df,
        target_date,
        coverage_ratio=cfg.target_date_coverage,
        lookback=cfg.target_date_lookback,
    )
    work, audit = _quality_checked_window(df, target_dt, cfg)

    excluded = _normalise_codes(exclude_codes)
    prefixes = tuple(str(prefix).strip() for prefix in (exclude_prefixes or []) if str(prefix).strip())
    quality_codes = set(work["ts_code"].unique())
    excluded_code_count = len(quality_codes & excluded)
    excluded_prefix_count = 0
    if excluded:
        work = work[~work["ts_code"].isin(excluded)]
    if prefixes:
        pure_codes = work["ts_code"].str.split(".").str[0]
        prefix_mask = pure_codes.str.startswith(prefixes)
        excluded_prefix_count = int(work.loc[prefix_mask, "ts_code"].nunique())
        work = work[~prefix_mask]

    work = _add_indicators(work, cfg)
    today = work[work["trade_date"] == target_dt].copy()

    indicators_valid = today[
        ["ma20", "ma60", "ma20_ref", "vol20", "amount20", "prev_close", "prev_ma20"]
    ].notna().all(axis=1)
    common = (
        indicators_valid
        & (today["vol"] > 0)
        & (today["amount"] > 0)
        & (today["amount20"] >= cfg.min_amount20)
        & (today["ma20_slope"] >= cfg.min_ma20_slope)
        & (today["ma20"] >= today["ma60"])
        & (today["close"] >= today["ma20"])
        & (today["vs_ma20"] <= cfg.max_vs_ma20)
        & (today["ret1"] <= cfg.max_ret1)
        & (today["ret5"] <= cfg.max_ret5)
        & (today["ret60"] <= cfg.max_ret60)
    )

    bullish_day = (today["close"] > today["open"]) & (today["close"] > today["prev_close"])
    reclaim = (
        common
        & (today["prev_close"] <= today["prev_ma20"])
        & (today["close"] > today["ma20"])
        & (today["vs_ma20"] <= cfg.reclaim_max_vs_ma20)
        & bullish_day
        & today["vol_ratio20"].between(
            cfg.reclaim_min_vol_ratio,
            cfg.reclaim_max_vol_ratio,
            inclusive="both",
        )
    )

    pullback = (
        common
        & ~reclaim
        & (today["prev_close"] > today["prev_ma20"])
        & (today["low"] <= today["ma20"] * (1.0 + cfg.pullback_touch_buffer))
        & (today["vs_ma20"] <= cfg.pullback_max_vs_ma20)
        & (today["prior_max_vs_ma20"] >= cfg.pullback_min_prior_extension)
        & bullish_day
        & (today["vol_ratio20"] <= cfg.pullback_max_vol_ratio)
    )

    today["signal"] = np.select(
        [reclaim, pullback, common],
        ["放量收复MA20", "缩量回踩MA20", "趋势观察"],
        default="排除",
    )
    today["action"] = np.where(today["signal"].isin(["放量收复MA20", "缩量回踩MA20"]), "候选", "观察")

    accepted = today["signal"].ne("排除")
    if not include_watch:
        accepted &= today["action"].eq("候选")
    result = today.loc[accepted].copy()
    if not result.empty:
        result["score"] = [
            _score_row(row, cfg, str(row["signal"])) for _, row in result.iterrows()
        ]
        result["stop_reference"] = result["ma20"] * 0.98
        result["vs_ma20_pct"] = result["vs_ma20"] * 100.0
        result["ma20_slope5_pct"] = result["ma20_slope"] * 100.0
        result["ret1_pct"] = result["ret1"] * 100.0
        result["ret5_pct"] = result["ret5"] * 100.0
        result["ret60_pct"] = result["ret60"] * 100.0
        result["amount20_yi"] = result["amount20"] / 1e8
        result["prior_extension_pct"] = result["prior_max_vs_ma20"] * 100.0
        priority = result["signal"].map({"放量收复MA20": 0, "缩量回踩MA20": 1, "趋势观察": 2})
        result = result.assign(_priority=priority).sort_values(
            ["_priority", "score", "amount20"],
            ascending=[True, False, False],
        ).drop(columns="_priority")

    columns = [
        "trade_date",
        "ts_code",
        "signal",
        "action",
        "score",
        "close",
        "ma20",
        "ma60",
        "stop_reference",
        "vs_ma20_pct",
        "ma20_slope5_pct",
        "vol_ratio20",
        "amount20_yi",
        "ret1_pct",
        "ret5_pct",
        "ret60_pct",
        "prior_extension_pct",
        "open",
        "high",
        "low",
        "vol",
        "amount",
    ]
    if result.empty:
        result = pd.DataFrame(columns=columns)
    else:
        result = result[columns].reset_index(drop=True)

    audit.update(
        {
            "excluded_by_code": int(excluded_code_count),
            "excluded_by_prefix": int(excluded_prefix_count),
            "excluded_prefixes": list(prefixes),
            "eligible_common_trend": int(common.sum()),
            "reclaim_signals": int(reclaim.sum()),
            "pullback_signals": int(pullback.sum()),
            "watch_signals": int((common & ~reclaim & ~pullback).sum()),
            "selected_rows": int(len(result)),
            "config": asdict(cfg),
        }
    )
    result.attrs["audit"] = audit

    if verbose:
        print(f"[MA20] 目标日: {target_dt:%Y-%m-%d}")
        print(
            "[MA20] 数据质检: "
            f"异常代码 {audit['invalid_codes_excluded']} / "
            f"陈旧代码 {audit['stale_codes_excluded']} / "
            f"有效股票 {audit['quality_pass_stocks']}"
        )
        print(
            "[MA20] 信号: "
            f"放量收复 {audit['reclaim_signals']} / "
            f"缩量回踩 {audit['pullback_signals']} / "
            f"趋势观察 {audit['watch_signals']}"
        )
    return result


__all__ = ["Ma20Config", "resolve_target_date", "screen_ma20"]
