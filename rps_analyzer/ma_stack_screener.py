"""
多头均线站上筛选：指定日期收盘价同时位于 MA5、MA10、MA20 之上。

数据仅依赖通达信本地日线字段：
trade_date, ts_code, open, high, low, close, vol, amount。

这是一个条件明确的横截面扫描，不做买点分级，也不产生仓位建议。
除权提示：通达信 ``vipdoc/*/lday/*.day`` 是未复权原始价。窗口内发生除权时，
均线会被人为抬高，收盘价"跌破均线"是假信号。因此默认对最近窗口内出现
超过涨跌幅限制的向下跳空的股票打 ``ex_rights_suspect`` 标记并剔除。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from rps_analyzer.ma20_screener import resolve_target_date

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

# 20cm 板：创业板、科创板；30cm 板：北交所
PREFIXES_20CM = ("300", "301", "688", "689")
PREFIXES_30CM = ("43", "83", "87", "88", "92")


@dataclass(frozen=True)
class MaStackConfig:
    """均线站上筛选参数。比例均使用小数，例如 0.03 表示 3%。"""

    ma_periods: tuple[int, ...] = (5, 10, 20)
    require_ma_order: bool = False
    min_amount20: float = 0.0
    ex_rights_guard: bool = True
    ex_rights_buffer: float = 0.005
    target_date_coverage: float = 0.90
    target_date_lookback: int = 10

    @property
    def max_period(self) -> int:
        return max(self.ma_periods)

    @property
    def required_bars(self) -> int:
        # 多留 1 根用于计算跳空，多留 20 根用于 amount20
        return max(self.max_period, 20) + 1


def _normalise_codes(codes: Optional[Iterable[str]]) -> set[str]:
    if not codes:
        return set()
    return {str(code).strip().upper() for code in codes if str(code).strip()}


def _limit_ratio(ts_code: pd.Series) -> pd.Series:
    """按板块返回涨跌幅限制。ST 的 5% 限制无法从代码判断，按主板 10% 处理。"""
    pure = ts_code.str.split(".").str[0]
    limit = pd.Series(0.10, index=ts_code.index)
    limit[pure.str.startswith(PREFIXES_20CM)] = 0.20
    limit[pure.str.startswith(PREFIXES_30CM)] = 0.30
    return limit


def _quality_checked_window(
    df: pd.DataFrame,
    target_dt: pd.Timestamp,
    cfg: MaStackConfig,
) -> tuple[pd.DataFrame, dict]:
    """截取目标日之前的窗口，剔除重复、异常、陈旧和历史不足的股票。"""

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
        finite_mask & positive_price & ohlc_consistent & nonnegative_trade & identity_valid
    )

    bad_codes = set(work.loc[invalid_mask | duplicate_mask, "ts_code"])
    if bad_codes:
        work = work[~work["ts_code"].isin(bad_codes)]

    last_dates = work.groupby("ts_code")["trade_date"].max()
    stale_codes = set(last_dates[last_dates != target_dt].index)
    if stale_codes:
        work = work[~work["ts_code"].isin(stale_codes)]

    bar_counts = work.groupby("ts_code").size()
    short_codes = set(bar_counts[bar_counts < cfg.max_period].index)
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


def _add_indicators(df: pd.DataFrame, cfg: MaStackConfig) -> pd.DataFrame:
    work = df.sort_values(["ts_code", "trade_date"]).copy()
    group = work.groupby("ts_code", sort=False)
    close_group = group["close"]

    for period in cfg.ma_periods:
        work[f"ma{period}"] = close_group.transform(
            lambda values, period=period: values.rolling(period, min_periods=period).mean()
        )
    work["amount20"] = group["amount"].transform(
        lambda values: values.rolling(20, min_periods=1).mean()
    )

    prev_close = close_group.shift(1)
    gap = work["open"] / prev_close - 1.0
    limit = _limit_ratio(work["ts_code"])
    # 未复权数据里，向下跳空超过涨跌幅限制基本只可能来自除权除息
    work["ex_rights_bar"] = (gap < -(limit + cfg.ex_rights_buffer)).astype(float)
    work["ex_rights_suspect"] = work.groupby("ts_code", sort=False)["ex_rights_bar"].transform(
        lambda values: values.rolling(cfg.max_period, min_periods=1).max()
    ).fillna(0.0).astype(bool)
    return work


def screen_ma_stack(
    df: pd.DataFrame,
    target_date: Optional[str | pd.Timestamp] = None,
    config: Optional[MaStackConfig] = None,
    *,
    exclude_codes: Optional[Iterable[str]] = None,
    exclude_prefixes: Optional[Sequence[str]] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """筛选目标日收盘价同时站上各条均线的股票，审计信息写入 ``result.attrs['audit']``。"""

    cfg = config or MaStackConfig()
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

    ma_columns = [f"ma{period}" for period in cfg.ma_periods]
    indicators_valid = today[ma_columns].notna().all(axis=1)
    above_all = indicators_valid.copy()
    for column in ma_columns:
        above_all &= today["close"] > today[column]

    ordered = pd.Series(True, index=today.index)
    sorted_periods = sorted(cfg.ma_periods)
    for shorter, longer in zip(sorted_periods, sorted_periods[1:]):
        ordered &= today[f"ma{shorter}"] > today[f"ma{longer}"]
    today["ma_order_ok"] = indicators_valid & ordered

    selected = above_all & (today["vol"] > 0) & (today["amount"] > 0)
    if cfg.min_amount20 > 0:
        selected &= today["amount20"] >= cfg.min_amount20
    if cfg.require_ma_order:
        selected &= today["ma_order_ok"]
    ex_rights_dropped = int((selected & today["ex_rights_suspect"]).sum())
    if cfg.ex_rights_guard:
        selected &= ~today["ex_rights_suspect"]

    result = today.loc[selected].copy()
    if not result.empty:
        for period in cfg.ma_periods:
            result[f"vs_ma{period}_pct"] = (result["close"] / result[f"ma{period}"] - 1.0) * 100.0
        result["amount20_yi"] = result["amount20"] / 1e8
        result = result.sort_values(["amount20", "ts_code"], ascending=[False, True])

    columns = (
        ["trade_date", "ts_code", "close"]
        + ma_columns
        + [f"vs_ma{period}_pct" for period in cfg.ma_periods]
        + ["ma_order_ok", "amount20_yi", "open", "high", "low", "vol", "amount"]
    )
    if result.empty:
        result = pd.DataFrame(columns=columns)
    else:
        result = result[columns].reset_index(drop=True)

    audit.update(
        {
            "excluded_by_code": int(excluded_code_count),
            "excluded_by_prefix": int(excluded_prefix_count),
            "excluded_prefixes": list(prefixes),
            "screened_stocks": int(today["ts_code"].nunique()),
            "above_all_ma": int(above_all.sum()),
            "ex_rights_suspect_excluded": ex_rights_dropped if cfg.ex_rights_guard else 0,
            "ma_order_ok": int(today["ma_order_ok"].sum()),
            "selected_rows": int(len(result)),
            "price_adjustment": "unadjusted",
            "config": asdict(cfg),
        }
    )
    result.attrs["audit"] = audit

    if verbose:
        label = "/".join(f"MA{period}" for period in sorted_periods)
        print(f"[MA_STACK] 目标日: {target_dt:%Y-%m-%d}")
        print(
            "[MA_STACK] 数据质检: "
            f"异常代码 {audit['invalid_codes_excluded']} / "
            f"陈旧代码 {audit['stale_codes_excluded']} / "
            f"历史不足 {audit['short_history_codes_excluded']} / "
            f"参与筛选 {audit['screened_stocks']}"
        )
        print(
            f"[MA_STACK] 收盘价站上 {label}: {audit['above_all_ma']} 只 / "
            f"疑似除权剔除 {audit['ex_rights_suspect_excluded']} 只 / "
            f"最终 {audit['selected_rows']} 只"
        )
    return result


__all__ = ["MaStackConfig", "screen_ma_stack"]
