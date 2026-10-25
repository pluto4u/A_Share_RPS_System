"""Refine the 2026-08-12 AI-hardware watchlist with market-breadth and momentum tests.

Inputs are local TongdaXin daily bars plus the prior 44-name analysis output.
The script deliberately treats index membership as a flow exposure, not as proof
that ETF money has entered on the signal day.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rps_analyzer.tdx_reader import DEFAULT_TDX_DIR, load_tdx_market


TARGET = pd.Timestamp("2026-08-12")
START_BREADTH = pd.Timestamp("2026-07-01")

# Constituents explicitly shown as additions in CSI's 2026-05-29 adjustment
# notice. The list does not claim exhaustive membership in every index.
INDEX_ADDITIONS = {
    "000988.SZ": "沪深300调入",
    "301308.SZ": "沪深300调入；中证A500调入",
    "688072.SH": "沪深300调入",
    "688183.SH": "沪深300调入",
    "001309.SZ": "中证500调入",
    "300475.SZ": "中证500调入；中证A500调入",
    "300548.SZ": "中证500调入",
    "300620.SZ": "中证500调入",
    "688313.SH": "中证500调入",
    "688498.SH": "中证500调入；中证A500调入",
    "688525.SH": "中证A500调入",
}


def add_features(daily: pd.DataFrame) -> pd.DataFrame:
    work = daily[daily["trade_date"].le(TARGET)].copy()
    work = work.sort_values(["ts_code", "trade_date"])
    group = work.groupby("ts_code", sort=False)
    close = group["close"]
    work["ma5"] = close.transform(lambda x: x.rolling(5, min_periods=5).mean())
    work["ma10"] = close.transform(lambda x: x.rolling(10, min_periods=10).mean())
    work["ma20"] = close.transform(lambda x: x.rolling(20, min_periods=20).mean())
    work["ret1_fwd"] = group["close"].shift(-1) / work["close"] - 1
    work["next_record_date"] = group["trade_date"].shift(-1)
    work["mom5"] = close.pct_change(5, fill_method=None)
    work["mom10"] = close.pct_change(10, fill_method=None)
    work["mom20"] = close.pct_change(20, fill_method=None)
    work["vol20_prior"] = group["vol"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=20).mean()
    )
    work["vr20_prior"] = work["vol"] / work["vol20_prior"].replace(0, np.nan)
    return work


def market_breadth(work: pd.DataFrame) -> pd.DataFrame:
    sample = work[
        work["trade_date"].between(pd.Timestamp("2026-08-03"), TARGET)
        & work[["ma5", "ma10", "ma20"]].notna().all(axis=1)
    ].copy()
    out = (
        sample.groupby("trade_date")
        .apply(
            lambda x: pd.Series(
                {
                    "stocks": x["ts_code"].nunique(),
                    "above_ma5_pct": (x["close"] > x["ma5"]).mean() * 100,
                    "above_ma10_pct": (x["close"] > x["ma10"]).mean() * 100,
                    "above_ma20_pct": (x["close"] > x["ma20"]).mean() * 100,
                    "above_ma5_and_ma10_pct": (
                        (x["close"] > x["ma5"]) & (x["close"] > x["ma10"])
                    ).mean()
                    * 100,
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    return out


def momentum_spread(work: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    sample = work[
        work["trade_date"].between(START_BREADTH, TARGET - pd.Timedelta(days=1))
    ].copy()
    trade_dates = sorted(pd.Timestamp(x) for x in work.loc[work["trade_date"].le(TARGET), "trade_date"].unique())
    next_date = {trade_dates[i]: trade_dates[i + 1] for i in range(len(trade_dates) - 1)}
    for date, raw_day in sample.groupby("trade_date"):
        for signal in ["mom5", "mom10", "mom20"]:
            expected_next = next_date.get(pd.Timestamp(date), pd.NaT)
            day = raw_day.dropna(subset=[signal, "ret1_fwd", "next_record_date"])
            day = day[day["next_record_date"].eq(expected_next)]
            day = day[(day["close"] > 0) & np.isfinite(day["ret1_fwd"])]
            if len(day) < 1000:
                continue
            p20, p80 = day[signal].quantile([0.2, 0.8])
            low_group = day.loc[day[signal] <= p20, "ret1_fwd"]
            high_group = day.loc[day[signal] >= p80, "ret1_fwd"]
            rows.append(
                {
                    "signal_date": date,
                    "next_trade_date": expected_next,
                    "signal": signal,
                    "stocks": len(day),
                    "top20_next_ret_pct": high_group.mean() * 100,
                    "bottom20_next_ret_pct": low_group.mean() * 100,
                    "momentum_spread_pct": (high_group.mean() - low_group.mean()) * 100,
                    "median_spread_pct": (high_group.median() - low_group.median()) * 100,
                }
            )
    daily_spread = pd.DataFrame(rows)
    daily_spread["period"] = np.where(
        daily_spread["signal_date"] < pd.Timestamp("2026-08-03"),
        "2026-07",
        "2026-08-03_to_08-11",
    )
    summary = (
        daily_spread.groupby(["period", "signal"])
        .agg(
            observations=("momentum_spread_pct", "size"),
            avg_daily_spread_pct=("momentum_spread_pct", "mean"),
            median_daily_spread_pct=("median_spread_pct", "mean"),
            positive_days=("momentum_spread_pct", lambda x: int((x > 0).sum())),
            compounded_top20_pct=("top20_next_ret_pct", lambda x: (np.prod(1 + x / 100) - 1) * 100),
            compounded_bottom20_pct=("bottom20_next_ret_pct", lambda x: (np.prod(1 + x / 100) - 1) * 100),
        )
        .reset_index()
    )
    summary["compounded_spread_pct"] = (
        summary["compounded_top20_pct"] - summary["compounded_bottom20_pct"]
    )
    return daily_spread, summary


def refine_candidates(work: pd.DataFrame) -> pd.DataFrame:
    prior_path = ROOT / "output" / "ai_hardware_rotation_20260812.csv"
    prior = pd.read_csv(prior_path)
    pool = set(prior["ts_code"])
    hist = work[
        work["ts_code"].isin(pool)
        & work["trade_date"].between(pd.Timestamp("2026-07-01"), TARGET)
    ].copy()
    july = hist[hist["trade_date"].dt.month.eq(7)].groupby("ts_code").agg(
        july_high=("high", "max"), july_low=("low", "min")
    )
    current = hist[hist["trade_date"].eq(TARGET)].set_index("ts_code")
    extra = current[["close", "ma5", "ma10", "ma20"]].join(july, how="left")
    extra["july_recovery_pct"] = (
        (extra["close"] - extra["july_low"])
        / (extra["july_high"] - extra["july_low"]).replace(0, np.nan)
        * 100
    )
    extra["gap_to_july_high_pct"] = (extra["close"] / extra["july_high"] - 1) * 100
    extra["above_ma5_ma10"] = (
        (extra["close"] > extra["ma5"]) & (extra["close"] > extra["ma10"])
    )
    extra = extra.reset_index()[
        ["ts_code", "july_recovery_pct", "gap_to_july_high_pct", "above_ma5_ma10"]
    ]
    out = prior.merge(extra, on="ts_code", how="left", validate="one_to_one")
    out["index_flow_exposure"] = out["ts_code"].map(INDEX_ADDITIONS).fillna("未由本次公告确认调入")

    # Image-view implementation: recent momentum + broad participation + flow
    # eligibility, while penalising exhaustion, poor volume, and crowded recovery.
    out["momentum_points"] = (
        np.where(out["rps5"] >= 80, 14, np.where(out["rps5"] >= 65, 8, 0))
        + np.where(out["rps10"] >= 80, 14, np.where(out["rps10"] >= 65, 8, 0))
        + np.where(out["ret5_pct"].between(3, 15), 8, 0)
        + np.where(out["above_ma5_ma10"], 8, 0)
    )
    out["flow_points"] = (
        np.where(out["index_flow_exposure"].ne("未由本次公告确认调入"), 8, 0)
        + np.where(out["amount_yi"] >= 50, 6, np.where(out["amount_yi"] >= 20, 3, 0))
    )
    out["entry_points"] = (
        np.where(out["vr20_prior"].between(0.8, 1.4), 8, 0)
        + np.where(out["clv"] >= 0.65, 5, 0)
        + np.where(out["close_vs_ma20_pct"].between(0, 10), 8, 0)
        + np.where(out["trigger_risk_pct"] <= 7, 5, 0)
    )
    out["risk_penalty"] = (
        np.where(out["ret5_pct"] > 15, 12, 0)
        + np.where(out["close_vs_ma20_pct"] > 15, 12, 0)
        + np.where(out["july_recovery_pct"] >= 90, 8, 0)
        + np.where(out["vr20_prior"] < 0.65, 5, 0)
    )
    out["refined_score"] = (
        out["momentum_points"] + out["flow_points"] + out["entry_points"] - out["risk_penalty"]
    ).clip(0, 100)
    out["refined_status"] = np.select(
        [
            (out["refined_score"] >= 50) & ~out["hard_overextended"],
            (out["refined_score"] >= 40) & ~out["hard_overextended"],
            out["hard_overextended"],
        ],
        ["一级条件观察", "二级条件观察", "强动量但过热",],
        default="暂不参与",
    )
    # The market-level test rejected a clean momentum-takeover regime for
    # 2026-08-03..11. Use a hybrid ranking that prefers controlled recovery
    # instead of mechanically rewarding the hottest names.
    out["hybrid_score"] = (
        out["trade_score"]
        + np.where(out["above_ma5_ma10"], 5, 0)
        + np.where(out["index_flow_exposure"].ne("未由本次公告确认调入"), 5, 0)
        + np.where(out["ret5_pct"].between(3, 10), 5, 0)
        + np.where(out["vr20_prior"].between(0.8, 1.4), 5, 0)
        + np.where(out["close_vs_ma20_pct"].between(0, 8), 5, 0)
        - np.where(out["ret5_pct"] > 15, 15, 0)
        - np.where(out["close_vs_ma20_pct"] > 15, 15, 0)
        - np.where(out["july_recovery_pct"] >= 90, 8, 0)
        - np.where(out["trigger_risk_pct"] > 8, 5, 0)
        - np.where(out["ret20_pct"] < -20, 8, 0)
        - np.where(out["ma20_slope5_pct"] < -5, 5, 0)
        - np.where(out["rps20"] < 5, 5, 0)
    ).clip(0, 100)
    out["hybrid_status"] = np.select(
        [
            (out["hybrid_score"] >= 75) & ~out["hard_overextended"],
            (out["hybrid_score"] >= 65) & ~out["hard_overextended"],
            out["hard_overextended"],
        ],
        ["一级条件观察", "二级条件观察", "过热等待整理"],
        default="暂不参与",
    )
    return out.sort_values(
        ["hybrid_score", "trade_score"], ascending=[False, False]
    ).reset_index(drop=True)


def main() -> None:
    daily = load_tdx_market(DEFAULT_TDX_DIR, min_days=130, verbose=False)
    work = add_features(daily)
    breadth = market_breadth(work)
    spread_daily, spread_summary = momentum_spread(work)
    refined = refine_candidates(work)

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    refined.to_csv(output / "ai_hardware_momentum_refined_20260812.csv", index=False, encoding="utf-8-sig")
    breadth.to_csv(output / "market_breadth_20260812.csv", index=False, encoding="utf-8-sig")
    spread_daily.to_csv(output / "momentum_spread_daily_20260812.csv", index=False, encoding="utf-8-sig")
    spread_summary.to_csv(output / "momentum_spread_summary_20260812.csv", index=False, encoding="utf-8-sig")

    audit = {
        "as_of": str(TARGET.date()),
        "source": str(DEFAULT_TDX_DIR),
        "stocks_loaded": int(daily["ts_code"].nunique()),
        "rows_loaded": int(len(daily)),
        "latest_date": str(daily["trade_date"].max().date()),
        "candidate_rows": int(len(refined)),
        "candidate_unique": int(refined["ts_code"].nunique()),
        "breadth_dates": int(len(breadth)),
        "momentum_test_rows": int(len(spread_daily)),
        "momentum_test_signal_dates": int(spread_daily["signal_date"].nunique()),
        "duplicate_candidates": int(refined["ts_code"].duplicated().sum()),
        "missing_refinement_fields": {
            c: int(refined[c].isna().sum())
            for c in ["july_recovery_pct", "gap_to_july_high_pct", "refined_score"]
        },
        "index_notice_scope": "only explicit additions in CSI 2026-05-29 adjustment notice; not exhaustive current membership",
    }
    (output / "ai_hardware_momentum_refined_20260812_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print("\nBreadth:\n", breadth.to_string(index=False))
    print("\nMomentum spread:\n", spread_summary.to_string(index=False))
    cols = [
        "name", "sector", "hybrid_status", "hybrid_score", "refined_score", "ret5_pct", "ret10_pct",
        "vr20_prior", "close_vs_ma20_pct", "july_recovery_pct", "gap_to_july_high_pct",
        "index_flow_exposure", "trigger_price", "invalidation_price",
    ]
    print("\nTop refined:\n", refined[cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
