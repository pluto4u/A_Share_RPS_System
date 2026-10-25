"""
================================================================================
通达信日线等比前复权（按目标日截断）
================================================================================
只使用目标日及以前已经发生的 gbbq 除权事件，最新一根价格与原始价一致。
算法与 run_local_tdx_compare.adjust 相同；该文件已被研究协议登记哈希，不在原处改动。

仅调整 open/high/low/close；成交量、成交额保持原值。
特殊复权类别 11–15 无法可靠还原，整只排除并返回原因，不猜测。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PRICE = ["open", "high", "low", "close"]
EVENT_FIELDS = [
    "hongli_panqianliutong",
    "peigujia_qianzongguben",
    "songgu_qianzongguben",
    "peigu_houzongguben",
]


def default_gbbq_path(vipdoc_dir: str) -> Path:
    """vipdoc 同级的 T0002/hq_cache/gbbq。"""
    return Path(vipdoc_dir).parent / "T0002" / "hq_cache" / "gbbq"


def load_gbbq(path: Path) -> dict[str, pd.DataFrame]:
    from pytdx.reader import GbbqReader

    if not Path(path).is_file():
        raise FileNotFoundError(f"缺少除权除息文件: {path}")
    events = GbbqReader().get_df(str(path))
    events["date"] = pd.to_datetime(events.datetime.astype(str), format="%Y%m%d")
    events["ts_code"] = events.code + events.market.map({0: ".SZ", 1: ".SH"}).fillna(".OTHER")
    return dict(tuple(events.groupby("ts_code")))


def adjust_one(raw: pd.DataFrame, events: pd.DataFrame | None, target: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    d = raw.loc[raw.trade_date <= target].sort_values("trade_date").copy()
    if d.empty:
        raise ValueError("no dated bars")
    if d.trade_date.duplicated().any() or not np.isfinite(d[PRICE]).all().all():
        raise ValueError("duplicate dates or nonfinite prices")
    if (d[PRICE] <= 0).any().any() or (d.high < d[PRICE].max(axis=1)).any():
        raise ValueError("invalid OHLC")
    if (d.low > d[PRICE].min(axis=1)).any():
        raise ValueError("invalid low")
    if events is None or events.empty:
        return d, 0
    ev = events.loc[(events.date > d.trade_date.iloc[0]) & (events.date <= target)]
    if (~ev.category.isin(range(1, 11))).any():
        raise ValueError("unsupported adjustment category")
    ev = ev.loc[ev.category == 1].sort_values("date")
    if ev.date.duplicated().any():
        raise ValueError("duplicate corporate action date")
    factors = np.ones(len(d), dtype=float)
    dates = d.trade_date.to_numpy()
    last_pos = -1
    ref = 0.0
    for _, e in ev.iterrows():
        pos = int(dates.searchsorted(e.date.to_datetime64()))
        if pos == 0 or pos >= len(d):
            raise ValueError("corporate action without a following bar")
        cash, rights_price, bonus, rights = [float(e[x]) for x in EVENT_FIELDS]
        if not np.isfinite([cash, rights_price, bonus, rights]).all():
            raise ValueError("missing corporate action values")
        if min(cash, rights_price, bonus, rights) < 0:
            raise ValueError("negative corporate action values")
        if pos != last_pos:
            ref = float(d.close.iloc[pos - 1])
        next_ref = (ref * 10 - cash + rights_price * rights) / (10 + bonus + rights)
        if next_ref <= 0 or not np.isfinite(next_ref):
            raise ValueError("invalid adjustment reference")
        factors[:pos] *= next_ref / ref
        ref, last_pos = next_ref, pos
    d[PRICE] = d[PRICE].mul(factors, axis=0)
    if not np.isfinite(d[PRICE]).all().all() or (d[PRICE] <= 0).any().any():
        raise ValueError("invalid adjusted prices")
    return d, len(ev)


def forward_adjust_market(
    df: pd.DataFrame,
    events_by_code: dict[str, pd.DataFrame],
    target: pd.Timestamp | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (前复权长表, 排除清单)。target 缺省为数据最新日。"""
    target = pd.Timestamp(target) if target is not None else df["trade_date"].max()
    parts, excluded, applied = [], [], 0
    for code, g in df.groupby("ts_code", sort=False):
        try:
            d, n = adjust_one(g, events_by_code.get(code), target)
        except ValueError as exc:
            excluded.append({"ts_code": code, "reason": str(exc)})
            continue
        parts.append(d)
        applied += n
    if not parts:
        raise RuntimeError("前复权后没有可用股票")
    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    excluded_df = pd.DataFrame(excluded, columns=["ts_code", "reason"])
    if verbose:
        print(f"[QFQ] 目标日 {target.date()}: 纳入 {out['ts_code'].nunique()} 只, "
              f"应用除权事件 {applied} 次, 排除 {len(excluded_df)} 只")
    return out, excluded_df
