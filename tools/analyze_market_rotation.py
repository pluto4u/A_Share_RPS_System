"""A股盘面、成交资金迁移和板块轮动的本地 TDX 复核。

输出是可审计的 CSV/JSON，供 Data Analytics HTML 报告读取。这里的“资金”只指
成交额迁移、量价方向和成交集中度代理，不把日线数据包装成逐笔主力净流入。
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from macro.sector import load_industry_names, resolve_industry_name
from rps_analyzer.industry_rps import load_industry_map
from rps_analyzer.tdx_reader import load_tdx_market, read_day_file
from run_ma20_screener import _load_tdx_name_master


DEFAULT_TDX = Path(r"C:\new_tdx\vipdoc")
DEFAULT_HQ = Path(r"C:\new_tdx\T0002\hq_cache")
DEFAULT_INDUSTRY = DEFAULT_HQ / "tdxhy.cfg"
DEFAULT_INCON = Path(r"C:\new_tdx\incon.dat")
INDEX_FILES = {
    "上证指数": ("sh", "000001"),
    "沪深300": ("sh", "000300"),
    "中证500": ("sh", "000905"),
    "中证1000": ("sh", "000852"),
    "科创50": ("sh", "000688"),
    "创业板指": ("sz", "399006"),
}


def _risk_name(name: str) -> bool:
    compact = str(name).replace(" ", "").upper()
    return compact.startswith(("ST", "*ST")) or "退" in compact


def _safe_div(a: float, b: float) -> float:
    return float(a / b) if b and math.isfinite(b) else float("nan")


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def _load_universe(tdx_dir: Path, min_days: int) -> pd.DataFrame:
    daily = load_tdx_market(str(tdx_dir), min_days=min_days, verbose=True)
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return daily


def _add_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    work = daily.copy()
    g = work.groupby("ts_code", sort=False)
    close = g["close"]
    amount = g["amount"]
    vol = g["vol"]

    work["ret1"] = close.pct_change(fill_method=None)
    for n in (5, 10, 20, 60, 120):
        work[f"ret{n}"] = close.pct_change(n, fill_method=None)
    for n in (5, 10, 20, 50, 200):
        work[f"ma{n}"] = close.transform(lambda s, n=n: s.rolling(n, min_periods=n).mean())

    work["ma20_prev5"] = g["ma20"].shift(5)
    work["ma20_slope5_pct"] = (work["ma20"] / work["ma20_prev5"] - 1.0) * 100.0
    work["amount20_prior"] = amount.transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    work["vol20_prior"] = vol.transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    work["amount_ratio20"] = work["amount"] / work["amount20_prior"].replace(0, np.nan)
    work["vol_ratio20"] = work["vol"] / work["vol20_prior"].replace(0, np.nan)
    work["high20_prior"] = g["high"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).max())
    work["high60_prior"] = g["high"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).max())
    day_range = (work["high"] - work["low"]).replace(0, np.nan)
    work["clv"] = ((work["close"] - work["low"]) / day_range).clip(0, 1).fillna(0.5)
    work["close_vs_ma20_pct"] = (work["close"] / work["ma20"] - 1.0) * 100.0
    work["history_days"] = g.cumcount() + 1
    return work


def _market_daily(work: pd.DataFrame, codes: set[str], dates: pd.DatetimeIndex) -> pd.DataFrame:
    sub = work[work["ts_code"].isin(codes) & work["trade_date"].isin(dates)].copy()
    rows: list[dict] = []
    for date, day in sub.groupby("trade_date", sort=True):
        ret = day["ret1"].where(day["ret1"].abs() <= 0.25)
        valid = ret.notna()
        active = day.loc[valid].copy()
        r = ret.loc[valid]
        amt = active["amount"].clip(lower=0)
        total_amt = float(amt.sum())
        up_amt = float(amt[r > 0].sum())
        down_amt = float(amt[r < 0].sum())
        top20_share = float(amt.nlargest(min(20, len(amt))).sum() / total_amt) if total_amt else np.nan
        top50_share = float(amt.nlargest(min(50, len(amt))).sum() / total_amt) if total_amt else np.nan
        row = {
            "trade_date": date,
            "stocks": int(len(active)),
            "up_pct": float((r > 0).mean() * 100),
            "down_pct": float((r < 0).mean() * 100),
            "flat_pct": float((r == 0).mean() * 100),
            "median_return_pct": float(r.median() * 100),
            "mean_return_pct": float(r.mean() * 100),
            "above_ma5_pct": float((active["close"] > active["ma5"]).mean() * 100),
            "above_ma10_pct": float((active["close"] > active["ma10"]).mean() * 100),
            "above_ma20_pct": float((active["close"] > active["ma20"]).mean() * 100),
            "above_ma50_pct": float((active["close"] > active["ma50"]).mean() * 100),
            "above_ma200_pct": float((active["close"] > active["ma200"]).mean() * 100),
            "new_high20_pct": float((active["close"] >= active["high20_prior"]).mean() * 100),
            "limit_up_proxy_pct": float((r >= 0.095).mean() * 100),
            "limit_down_proxy_pct": float((r <= -0.095).mean() * 100),
            "total_amount_yi": total_amt / 1e8,
            "up_amount_share_pct": _safe_div(up_amt, total_amt) * 100,
            "down_amount_share_pct": _safe_div(down_amt, total_amt) * 100,
            "signed_amount_ratio_pct": _safe_div(up_amt - down_amt, total_amt) * 100,
            "top20_amount_share_pct": top20_share * 100,
            "top50_amount_share_pct": top50_share * 100,
        }
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    out["amount_vs_prior20"] = out["total_amount_yi"] / out["total_amount_yi"].shift(1).rolling(20, min_periods=10).mean()
    out["amount_vs_prior5"] = out["total_amount_yi"] / out["total_amount_yi"].shift(1).rolling(5, min_periods=3).mean()
    return out


def _sector_snapshot(
    work: pd.DataFrame,
    codes: set[str],
    latest: pd.Timestamp,
    industry_map: pd.DataFrame,
    name_map: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ind = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    recent_dates = sorted(work.loc[work["trade_date"] <= latest, "trade_date"].unique())[-25:]
    sub = work[
        work["ts_code"].isin(codes) & work["trade_date"].isin(recent_dates)
    ].copy()
    sub["industry"] = sub["ts_code"].map(ind)
    sub = sub.dropna(subset=["industry"])

    daily_amount = sub.groupby(["trade_date", "industry"], observed=True)["amount"].sum().rename("amount")
    daily_share = (daily_amount / daily_amount.groupby(level=0).transform("sum")).rename("amount_share")
    prior_dates = [d for d in recent_dates if pd.Timestamp(d) < latest][-20:]
    prior_amount = daily_amount.loc[pd.IndexSlice[prior_dates, :]].groupby("industry").mean()
    prior_share = daily_share.loc[pd.IndexSlice[prior_dates, :]].groupby("industry").mean()

    today = sub[sub["trade_date"].eq(latest)].copy()
    today["ret1_valid"] = today["ret1"].where(today["ret1"].abs() <= 0.25)
    today["signed_amount"] = today["amount"] * np.sign(today["ret1_valid"].fillna(0))

    rows: list[dict] = []
    for industry, group in today.groupby("industry", observed=True):
        amount = float(group["amount"].sum())
        top3 = float(group["amount"].nlargest(min(3, len(group))).sum())
        rows.append(
            {
                "industry": industry,
                "name": resolve_industry_name(industry, name_map),
                "n_stocks": int(group["ts_code"].nunique()),
                "ret1_median_pct": float(group["ret1_valid"].median() * 100),
                "ret1_amount_weighted_pct": _safe_div(float((group["ret1_valid"].fillna(0) * group["amount"]).sum()), amount) * 100,
                "ret5_median_pct": float(group["ret5"].median() * 100),
                "ret20_median_pct": float(group["ret20"].median() * 100),
                "ret60_median_pct": float(group["ret60"].median() * 100),
                "breadth_up_pct": float((group["ret1_valid"] > 0).mean() * 100),
                "breadth_ma5_pct": float((group["close"] > group["ma5"]).mean() * 100),
                "breadth_ma20_pct": float((group["close"] > group["ma20"]).mean() * 100),
                "amount_yi": amount / 1e8,
                "amount_ratio20": _safe_div(amount, float(prior_amount.get(industry, np.nan))),
                "amount_share_pct": float(daily_share.get((latest, industry), np.nan) * 100),
                "amount_share_shift_pp": float((daily_share.get((latest, industry), np.nan) - prior_share.get(industry, np.nan)) * 100),
                "signed_amount_ratio_pct": _safe_div(float(group["signed_amount"].sum()), amount) * 100,
                "top3_amount_share_pct": _safe_div(top3, amount) * 100,
            }
        )
    out = pd.DataFrame(rows)
    out = out[out["n_stocks"] >= 8].copy()

    for col, weight in {
        "ret5_median_pct": 0.20,
        "ret20_median_pct": 0.20,
        "breadth_ma20_pct": 0.15,
        "amount_ratio20": 0.15,
        "amount_share_shift_pp": 0.10,
        "signed_amount_ratio_pct": 0.20,
    }.items():
        out[f"rank_{col}"] = out[col].rank(pct=True) * 100
        out[f"weighted_{col}"] = out[f"rank_{col}"] * weight
    weighted_cols = [c for c in out.columns if c.startswith("weighted_")]
    out["rotation_score"] = out[weighted_cols].sum(axis=1)
    out["fund_proxy_score"] = (
        out["rank_amount_ratio20"] * 0.40
        + out["rank_amount_share_shift_pp"] * 0.25
        + out["rank_signed_amount_ratio_pct"] * 0.35
    )
    med5 = out["ret5_median_pct"].median()
    med20 = out["ret20_median_pct"].median()
    out["quadrant"] = np.select(
        [
            (out["ret20_median_pct"] >= med20) & (out["ret5_median_pct"] >= med5),
            (out["ret20_median_pct"] < med20) & (out["ret5_median_pct"] >= med5) & (out["amount_share_shift_pp"] > 0),
            (out["ret20_median_pct"] >= med20) & (out["ret5_median_pct"] < med5),
        ],
        ["主升", "启动", "退潮"],
        default="落后",
    )
    out["rotation_grade"] = np.select(
        [
            (out["rotation_score"] >= 75)
            & (out["signed_amount_ratio_pct"] > 0)
            & (out["breadth_ma20_pct"] >= 60)
            & (out["breadth_up_pct"] >= 50),
            (out["rotation_score"] >= 65)
            & (out["amount_share_shift_pp"] > 0)
            & (out["signed_amount_ratio_pct"] > -20)
            & (out["breadth_up_pct"] >= 15),
        ],
        ["A_进攻候选", "B_轮动观察"],
        default="C_不优先",
    )
    out = out.sort_values("rotation_score", ascending=False).reset_index(drop=True)

    sector_daily = (
        sub.assign(ret1_valid=sub["ret1"].where(sub["ret1"].abs() <= 0.25))
        .groupby(["trade_date", "industry"], observed=True)["ret1_valid"]
        .median()
        .unstack("industry")
        .sort_index()
    )
    return out, sector_daily


def _momentum_validation(work: pd.DataFrame, stable_codes: set[str], latest: pd.Timestamp) -> pd.DataFrame:
    sub = work[work["ts_code"].isin(stable_codes) & (work["trade_date"] <= latest)].copy()
    sub["realized_ret1"] = sub.groupby("ts_code", sort=False)["close"].pct_change(fill_method=None)
    sub["fwd1"] = sub.groupby("ts_code", sort=False)["realized_ret1"].shift(-1)
    dates = sorted(sub.loc[sub["trade_date"] < latest, "trade_date"].unique())[-45:]
    daily_rows: list[dict] = []
    for date in dates:
        day = sub[sub["trade_date"].eq(date)]
        for n in (5, 20, 60):
            valid = day[[f"ret{n}", "fwd1"]].replace([np.inf, -np.inf], np.nan).dropna()
            valid = valid[valid["fwd1"].abs() <= 0.25]
            if len(valid) < 1000:
                continue
            lo, hi = valid[f"ret{n}"].quantile([0.2, 0.8])
            top = valid.loc[valid[f"ret{n}"] >= hi, "fwd1"].mean()
            bottom = valid.loc[valid[f"ret{n}"] <= lo, "fwd1"].mean()
            daily_rows.append({"trade_date": date, "signal_days": n, "spread_pct": float((top - bottom) * 100)})
    daily = pd.DataFrame(daily_rows)
    rows: list[dict] = []
    for n in (5, 20, 60):
        d = daily[daily["signal_days"].eq(n)].sort_values("trade_date")
        for window in (5, 10, 20, 40):
            sample = d.tail(window)
            if sample.empty:
                continue
            rows.append(
                {
                    "signal_days": n,
                    "window_signals": window,
                    "observations": int(len(sample)),
                    "avg_next_day_spread_pct": float(sample["spread_pct"].mean()),
                    "median_next_day_spread_pct": float(sample["spread_pct"].median()),
                    "positive_spread_pct": float((sample["spread_pct"] > 0).mean() * 100),
                }
            )
    return pd.DataFrame(rows)


def _sector_momentum_validation(sector_daily: pd.DataFrame, latest: pd.Timestamp) -> pd.DataFrame:
    past5 = (1.0 + sector_daily).rolling(5, min_periods=5).apply(np.prod, raw=True) - 1.0
    fwd5 = (1.0 + sector_daily.shift(-1)).rolling(5, min_periods=5).apply(np.prod, raw=True).shift(-4) - 1.0
    signal_dates = [d for d in sector_daily.index if d < latest and d in fwd5.dropna(how="all").index][-30:]
    daily_rows: list[dict] = []
    for date in signal_dates:
        pair = pd.concat([past5.loc[date].rename("past"), fwd5.loc[date].rename("fwd")], axis=1).dropna()
        if len(pair) < 40:
            continue
        lo, hi = pair["past"].quantile([0.2, 0.8])
        spread = pair.loc[pair["past"] >= hi, "fwd"].mean() - pair.loc[pair["past"] <= lo, "fwd"].mean()
        daily_rows.append({"trade_date": date, "spread_pct": float(spread * 100)})
    daily = pd.DataFrame(daily_rows)
    rows: list[dict] = []
    for window in (5, 10, 20):
        sample = daily.tail(window)
        rows.append(
            {
                "window_signals": window,
                "observations": int(len(sample)),
                "avg_next5_spread_pct": float(sample["spread_pct"].mean()) if len(sample) else np.nan,
                "median_next5_spread_pct": float(sample["spread_pct"].median()) if len(sample) else np.nan,
                "positive_spread_pct": float((sample["spread_pct"] > 0).mean() * 100) if len(sample) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _read_indices(tdx_dir: Path, latest: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict] = []
    for name, (market, code) in INDEX_FILES.items():
        path = tdx_dir / market / "lday" / f"{market}{code}.day"
        frame = read_day_file(str(path))
        if frame is None:
            continue
        frame = frame[frame["trade_date"] <= latest].sort_values("trade_date").copy()
        close = frame["close"]
        prior20_amt = frame["amount"].shift(1).tail(20).mean()
        row = {
            "index": name,
            "trade_date": frame.iloc[-1]["trade_date"],
            "close": float(frame.iloc[-1]["close"]),
            "ret1_pct": float(close.pct_change(fill_method=None).iloc[-1] * 100),
            "ret5_pct": float(close.pct_change(5, fill_method=None).iloc[-1] * 100),
            "ret20_pct": float(close.pct_change(20, fill_method=None).iloc[-1] * 100),
            "ret60_pct": float(close.pct_change(60, fill_method=None).iloc[-1] * 100),
            "vs_ma20_pct": float((close.iloc[-1] / close.rolling(20).mean().iloc[-1] - 1) * 100),
            "amount_yi": float(frame.iloc[-1]["amount"] / 1e8),
            "amount_ratio20": _safe_div(float(frame.iloc[-1]["amount"]), float(prior20_amt)),
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values("ret1_pct", ascending=False).reset_index(drop=True)


def _candidate_table(
    latest_rows: pd.DataFrame,
    sectors: pd.DataFrame,
    l2_path: Path,
    super_path: Path,
    names: dict[str, str],
) -> pd.DataFrame:
    if not l2_path.exists():
        return pd.DataFrame()
    l2 = pd.read_csv(l2_path, encoding="utf-8-sig")
    super_codes = set()
    if super_path.exists():
        super_codes = set(pd.read_csv(super_path, encoding="utf-8-sig")["ts_code"].astype(str))
    sec = sectors.set_index("industry")
    rows = latest_rows[latest_rows["ts_code"].isin(set(l2["ts_code"].astype(str)))].copy()
    rows["name"] = rows["ts_code"].map(names).fillna("")
    rows["sector_name"] = rows["industry"].map(sec["name"])
    rows["sector_score"] = rows["industry"].map(sec["rotation_score"])
    rows["sector_fund_score"] = rows["industry"].map(sec["fund_proxy_score"])
    rows["sector_grade"] = rows["industry"].map(sec["rotation_grade"])
    rows["sector_signed_amount_ratio_pct"] = rows["industry"].map(sec["signed_amount_ratio_pct"])
    rows["rps20"] = rows["ret20"].rank(pct=True) * 100
    rows["rps60"] = rows["ret60"].rank(pct=True) * 100
    rows["amount_ratio_rank"] = rows["amount_ratio20"].rank(pct=True) * 100
    rows["vol_ratio_rank"] = rows["vol_ratio20"].rank(pct=True) * 100
    stock_score = (
        rows["rps20"] * 0.30
        + rows["rps60"] * 0.20
        + (rows["close"] > rows["ma20"]).astype(float) * 15
        + (rows["ma5"] > rows["ma10"]).astype(float) * 10
        + (rows["ma20_slope5_pct"] > 0).astype(float) * 10
        + rows["clv"].clip(0, 1) * 15
    )
    fund_score = rows["amount_ratio_rank"] * 0.50 + rows["vol_ratio_rank"] * 0.25 + rows["clv"] * 25
    rows["is_super"] = rows["ts_code"].isin(super_codes)
    rows["is_first_l2"] = rows["ts_code"].map(l2.set_index("ts_code").get("L2_FIRST_SIGNAL", pd.Series(dtype=bool))).fillna(False).astype(bool)
    penalty = (
        np.maximum(rows["close_vs_ma20_pct"] - 18, 0) * 0.8
        + (rows["ret1"] < -0.07).astype(float) * 8
        + (rows["clv"] < 0.15).astype(float) * 6
    )
    rows["overextended"] = (
        (rows["ret1"] > 0.07)
        | (rows["ret5"] > 0.18)
        | (rows["close_vs_ma20_pct"] > 15)
    )
    rows["candidate_score"] = (
        stock_score * 0.45
        + rows["sector_score"].fillna(40) * 0.35
        + fund_score * 0.20
        + rows["is_super"].astype(float) * 3
        + rows["is_first_l2"].astype(float) * 3
        - penalty
    ).clip(0, 100)
    rows["setup"] = np.select(
        [
            rows["overextended"],
            (rows["sector_grade"] == "A_进攻候选")
            & (rows["candidate_score"] >= 70)
            & (rows["close"] > rows["ma20"])
            & (rows["clv"] >= 0.35),
            rows["sector_grade"].isin(["A_进攻候选", "B_轮动观察"])
            & (rows["candidate_score"] >= 60)
            & (rows["close"] > rows["ma20"])
            & (rows["clv"] >= 0.25),
        ],
        ["过热，仅等整理", "优先观察回踩确认", "次级观察等待补量"],
        default="暂不博弈",
    )
    keep = [
        "ts_code", "name", "sector_name", "setup", "candidate_score", "sector_score",
        "sector_fund_score", "close", "ret1", "ret5", "ret20", "ret60", "rps20", "rps60",
        "amount_ratio20", "vol_ratio20", "clv", "close_vs_ma20_pct", "ma20_slope5_pct",
        "sector_signed_amount_ratio_pct", "is_super", "is_first_l2", "overextended",
    ]
    out = rows[keep].copy()
    for col in ("ret1", "ret5", "ret20", "ret60"):
        out[f"{col}_pct"] = out[col] * 100
    out = out.drop(columns=["ret1", "ret5", "ret20", "ret60"])
    return out.sort_values("candidate_score", ascending=False).reset_index(drop=True)


def _file_tail_qa(tdx_dir: Path) -> dict:
    rec = struct.Struct("<IIIIIfII")
    valid_prefix = {"sh": ("600", "601", "603", "605", "688", "689"), "sz": ("000", "001", "002", "003", "300", "301")}
    counts: dict[int, int] = {}
    bad_sizes = 0
    invalid_tails = 0
    total = 0
    for market in ("sh", "sz"):
        for path in (tdx_dir / market / "lday").glob(f"{market}*.day"):
            code = path.stem[2:8]
            if not code.startswith(valid_prefix[market]):
                continue
            total += 1
            size = path.stat().st_size
            if size % rec.size or size < rec.size:
                bad_sizes += 1
                continue
            with path.open("rb") as handle:
                handle.seek(-rec.size, 2)
                date, opn, high, low, close, amount, vol, _ = rec.unpack(handle.read(rec.size))
            counts[date] = counts.get(date, 0) + 1
            if not (20000101 <= date <= 20261231 and 0 < low <= min(opn, close) <= high and max(opn, close) <= high and amount >= 0):
                invalid_tails += 1
    latest_counts = sorted(counts.items(), reverse=True)[:8]
    return {"a_share_files": total, "latest_date_counts": latest_counts, "bad_file_sizes": bad_sizes, "invalid_tail_records": invalid_tails}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-dir", default=str(DEFAULT_TDX))
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ))
    parser.add_argument("--industry-cfg", default=str(DEFAULT_INDUSTRY))
    parser.add_argument("--incon", default=str(DEFAULT_INCON))
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--min-days", type=int, default=60)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tdx_dir = Path(args.tdx_dir)
    daily = _load_universe(tdx_dir, args.min_days)
    work = _add_indicators(daily)
    latest_available = work["trade_date"].max()
    latest = pd.Timestamp(args.target_date) if args.target_date else latest_available
    if latest > latest_available:
        raise ValueError(f"目标日 {latest.date()} 晚于本地最新日期 {latest_available.date()}")
    if latest not in set(work["trade_date"]):
        raise ValueError(f"目标日 {latest.date()} 不是本地交易日")
    stamp = latest.strftime("%Y%m%d")

    names = _load_tdx_name_master(Path(args.hq_cache))
    risk_codes = {code for code, name in names.items() if _risk_name(name)}
    history_counts = work.groupby("ts_code")["trade_date"].size()
    latest_codes = set(work.loc[work["trade_date"].eq(latest), "ts_code"])
    full_codes = latest_codes - risk_codes
    stable_codes = {code for code in full_codes if history_counts.get(code, 0) >= 250}

    all_dates = pd.DatetimeIndex(sorted(work.loc[work["trade_date"] <= latest, "trade_date"].unique()))
    lookback_dates = all_dates[-35:]
    market_full = _market_daily(work, full_codes, lookback_dates)
    market_stable = _market_daily(work, stable_codes, lookback_dates)
    market = market_full.merge(
        market_stable[["trade_date", "up_pct", "median_return_pct", "above_ma20_pct", "above_ma200_pct"]],
        on="trade_date",
        how="left",
        suffixes=("", "_stable"),
    )

    industry_map = load_industry_map(Path(args.industry_cfg))
    industry_names = load_industry_names(Path(args.incon))
    sectors_full, sector_daily = _sector_snapshot(work, full_codes, latest, industry_map, industry_names)
    sectors_stable, _ = _sector_snapshot(work, stable_codes, latest, industry_map, industry_names)
    stable_compare = sectors_full[["industry", "rotation_score"]].merge(
        sectors_stable[["industry", "rotation_score"]], on="industry", suffixes=("_full", "_stable")
    )
    stable_rank_corr = float(stable_compare["rotation_score_full"].corr(stable_compare["rotation_score_stable"], method="spearman"))
    stable_top10_overlap = len(set(sectors_full.head(10)["industry"]) & set(sectors_stable.head(10)["industry"]))

    macro_sector_path = out_dir / f"sector_weekly_{stamp}.csv"
    macro_rank_corr = np.nan
    macro_top10_overlap = 0
    if macro_sector_path.exists():
        macro_sector = pd.read_csv(macro_sector_path, encoding="utf-8-sig")
        macro_compare = sectors_full[["industry", "rotation_score"]].merge(
            macro_sector[["industry", "score"]], on="industry", how="inner"
        )
        macro_rank_corr = float(macro_compare["rotation_score"].corr(macro_compare["score"], method="spearman"))
        macro_top10_overlap = len(set(sectors_full.head(10)["industry"]) & set(macro_sector.nlargest(10, "score")["industry"]))
        sectors_full = sectors_full.merge(
            macro_sector[["industry", "score", "quadrant_cn"]].rename(columns={"score": "macro_sector_score", "quadrant_cn": "macro_quadrant"}),
            on="industry", how="left"
        )

    ind_series = industry_map.drop_duplicates("ts_code").set_index("ts_code")["industry"]
    latest_rows = work[work["trade_date"].eq(latest) & work["ts_code"].isin(full_codes)].copy()
    latest_rows["industry"] = latest_rows["ts_code"].map(ind_series)
    candidates = _candidate_table(
        latest_rows,
        sectors_full,
        out_dir / f"tdx_screener_{stamp}_L2.csv",
        out_dir / f"tdx_screener_{stamp}_super.csv",
        names,
    )
    momentum = _momentum_validation(work, stable_codes, latest)
    sector_momentum = _sector_momentum_validation(sector_daily, latest)
    indices = _read_indices(tdx_dir, latest)

    macro_path = out_dir / f"macro_regime_{stamp}.csv"
    macro_latest: dict = {}
    if macro_path.exists():
        macro_df = pd.read_csv(macro_path, encoding="utf-8-sig")
        macro_latest = _records(macro_df.tail(1))[0]

    duplicates = int(daily.duplicated(["trade_date", "ts_code"]).sum())
    invalid_ohlc = int((
        (daily["low"] <= 0)
        | (daily["low"] > daily[["open", "close"]].min(axis=1))
        | (daily["high"] < daily[["open", "close"]].max(axis=1))
        | (daily["high"] < daily["low"])
    ).sum())
    invalid_amount = int(((daily["amount"] < 0) | (daily["vol"] < 0)).sum())
    latest_market = market.iloc[-1]
    latest_stable = market_stable.iloc[-1]
    industry_coverage = float(latest_rows["industry"].notna().mean() * 100)
    name_coverage = float(latest_rows["ts_code"].map(names).notna().mean() * 100)
    tail_qa = _file_tail_qa(tdx_dir)

    l2_path = out_dir / f"tdx_screener_{stamp}_L2.csv"
    super_path = out_dir / f"tdx_screener_{stamp}_super.csv"
    qa = {
        "target_date": latest.strftime("%Y-%m-%d"),
        "latest_available": latest_available.strftime("%Y-%m-%d"),
        "timezone": "Asia/Shanghai",
        "rows_loaded": int(len(daily)),
        "codes_loaded_min60": int(daily["ts_code"].nunique()),
        "active_codes_latest": int(len(latest_codes)),
        "active_non_risk_codes": int(len(full_codes)),
        "stable_non_risk_codes_250d": int(len(stable_codes)),
        "risk_name_codes_excluded": int(len(latest_codes & risk_codes)),
        "duplicate_date_code_rows": duplicates,
        "invalid_ohlc_rows": invalid_ohlc,
        "invalid_amount_or_volume_rows": invalid_amount,
        "name_coverage_pct": name_coverage,
        "industry_coverage_pct": industry_coverage,
        "latest_full_vs_stable": {
            "up_pct_diff_pp": float(latest_market["up_pct"] - latest_stable["up_pct"]),
            "median_return_diff_pp": float(latest_market["median_return_pct"] - latest_stable["median_return_pct"]),
            "above_ma20_diff_pp": float(latest_market["above_ma20_pct"] - latest_stable["above_ma20_pct"]),
        },
        "sector_full_vs_stable_spearman": stable_rank_corr,
        "sector_full_vs_stable_top10_overlap": int(stable_top10_overlap),
        "sector_rotation_vs_macro_spearman": None if pd.isna(macro_rank_corr) else macro_rank_corr,
        "sector_rotation_vs_macro_top10_overlap": int(macro_top10_overlap),
        "l2_count": int(len(pd.read_csv(l2_path, encoding="utf-8-sig"))) if l2_path.exists() else 0,
        "super_count": int(len(pd.read_csv(super_path, encoding="utf-8-sig"))) if super_path.exists() else 0,
        "macro_latest": macro_latest,
        "file_tail_qa": tail_qa,
        "fund_flow_definition": "成交额相对20日基线、行业成交占比变化、上涨/下跌方向成交额差；不是逐笔主力净流入",
        "validation_status": "Share with caveats",
        "validation_caveats": [
            "通达信 .day 为本地日线，不能识别机构、北向、ETF申赎或大单买方身份。",
            "价格为通达信日线口径，未额外接入统一前复权因子；异常单日涨跌超过25%的记录不进入宽度方向统计。",
            "8月14日凌晨运行，最后完整收盘日是8月13日。",
        ],
    }

    paths = {
        "market": out_dir / f"rotation_market_daily_{stamp}.csv",
        "indices": out_dir / f"rotation_indices_{stamp}.csv",
        "sectors": out_dir / f"rotation_sectors_{stamp}.csv",
        "candidates": out_dir / f"rotation_candidates_{stamp}.csv",
        "momentum": out_dir / f"rotation_momentum_validation_{stamp}.csv",
        "sector_momentum": out_dir / f"rotation_sector_momentum_validation_{stamp}.csv",
        "qa": out_dir / f"rotation_analysis_{stamp}_qa.json",
    }
    market.to_csv(paths["market"], index=False, encoding="utf-8-sig", float_format="%.6f")
    indices.to_csv(paths["indices"], index=False, encoding="utf-8-sig", float_format="%.6f")
    sectors_full.to_csv(paths["sectors"], index=False, encoding="utf-8-sig", float_format="%.6f")
    candidates.to_csv(paths["candidates"], index=False, encoding="utf-8-sig", float_format="%.6f")
    momentum.to_csv(paths["momentum"], index=False, encoding="utf-8-sig", float_format="%.6f")
    sector_momentum.to_csv(paths["sector_momentum"], index=False, encoding="utf-8-sig", float_format="%.6f")
    paths["qa"].write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n盘面最新截面")
    print(market.tail(5).to_string(index=False))
    print("\n指数")
    print(indices.to_string(index=False))
    print("\n板块 Top 15")
    print(sectors_full.head(15)[[
        "name", "rotation_grade", "quadrant", "rotation_score", "fund_proxy_score", "ret1_median_pct",
        "ret5_median_pct", "ret20_median_pct", "breadth_ma20_pct", "amount_ratio20",
        "amount_share_shift_pp", "signed_amount_ratio_pct",
    ]].to_string(index=False))
    print("\nL2 候选 Top 15")
    print(candidates.head(15).to_string(index=False) if len(candidates) else "无")
    print("\n交叉验证")
    print(json.dumps({
        "full_vs_stable_spearman": stable_rank_corr,
        "full_vs_stable_top10_overlap": stable_top10_overlap,
        "rotation_vs_macro_spearman": macro_rank_corr,
        "rotation_vs_macro_top10_overlap": macro_top10_overlap,
    }, ensure_ascii=False, indent=2))
    print("\n输出")
    for path in paths.values():
        print(path.resolve())


if __name__ == "__main__":
    main()
