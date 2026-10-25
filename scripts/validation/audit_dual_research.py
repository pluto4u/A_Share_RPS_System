"""Independent CSV reconciliation of the registered dual research results."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.dual_system import StudyConfig, load_prefix, sha256
from trend_start.engine import portfolio as legacy_portfolio

ROOT = PROJECT_ROOT
OUT = ROOT / "output/dual_research_20261002_v1"


def main() -> None:
    frozen = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    cfg = StudyConfig.model_validate(frozen["cfg"])
    checks: list[dict[str, Any]] = []
    decomposition: list[dict[str, Any]] = []
    concentration: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    for relative, recorded in frozen["hashes"].items():
        check(f"hash:{relative}", sha256(ROOT / relative) == recorded, "compare frozen hash")
    ranking = pd.read_csv(OUT / "grid_is_summary.csv")
    eligible = ranking.loc[(ranking.trades >= cfg.min_is_trades) & ranking.mean_excess.notna()]
    chosen = int(eligible.iloc[0].combo) if not eligible.empty else None
    check("selected_allowed_combo", chosen == frozen["a_combo"], "IS ranking and eligibility")
    check("grid_size", len(ranking) == 1296, "only registered grid")
    for part in frozen["parts"]:
        check(
            f"part_cutoff:{part['range']}",
            part["cutoff"] <= cfg.is_end,
            "actual worker input calendar",
        )

    for label in ("is", "history"):
        folder = OUT / label
        stats = pd.read_csv(folder / "metrics.csv")
        for r in stats.itertuples(index=False):
            dest = folder / r.model
            curve = pd.read_csv(dest / "equity_cash.csv")
            trades = pd.read_csv(dest / "portfolio_trades.csv")
            held = pd.read_csv(dest / "holdings.csv")
            contribution = pd.read_csv(dest / "stock_contribution.csv")
            equity = curve.equity.to_numpy(dtype=float)
            profit = equity[-1] - cfg.capital
            peak = np.maximum.accumulate(np.r_[cfg.capital, equity])[1:]
            dd = (equity / peak - 1).min() * 100
            cagr = ((equity[-1] / cfg.capital) ** (244 / len(equity)) - 1) * 100
            check(
                f"{label}:{r.model}:profit",
                abs(profit - r.net_profit) < 1e-5,
                "recompute last equity minus initial capital",
            )
            check(
                f"{label}:{r.model}:drawdown",
                abs(dd - r.max_drawdown_pct) < 1e-8,
                "recompute daily peak to trough including initial capital",
            )
            check(
                f"{label}:{r.model}:annualized",
                abs(cagr - r.cagr_pct) < 1e-8,
                "recompute using declared 244-session annualization",
            )
            check(
                f"{label}:{r.model}:cash_identity",
                np.allclose(
                    curve.equity,
                    curve.cash_before_boundary_mark
                    + curve.position_market_value
                    - curve.boundary_valuation_cost,
                    atol=1e-5,
                ),
                "cash + market value - boundary cost",
            )
            check(
                f"{label}:{r.model}:cash_nonnegative",
                bool((curve.cash_before_boundary_mark >= -1e-6).all()),
                "every market session",
            )
            check(
                f"{label}:{r.model}:slot_cap",
                bool((curve.holding_count <= cfg.slots).all()),
                "every market session",
            )
            check(
                f"{label}:{r.model}:contribution",
                abs(contribution.pnl.sum() - profit) < 1e-5,
                "sum stock PnL equals total equity change",
            )
            check(
                f"{label}:{r.model}:trade_pnl",
                abs(trades.pnl.sum() - profit) < 1e-5,
                "actual portfolio events, not offered signal counts",
            )
            price_pnl = float((trades.units * (trades.book_exit_px - trades.entry_px)).sum())
            costs = float(
                (
                    trades.cost
                    - trades.units * trades.entry_px
                    + trades.units * trades.book_exit_px
                    - trades.net_proceeds
                ).sum()
            )
            check(
                f"{label}:{r.model}:cost_decomposition",
                abs(price_pnl - costs - profit) < 1e-5,
                "gross price PnL minus entry/exit modeled costs equals net profit",
            )
            decomposition.append(
                {
                    "period": label,
                    "model": r.model,
                    "portfolio_trades": len(trades),
                    "gross_price_pnl": price_pnl,
                    "modeled_total_cost": costs,
                    "net_profit": profit,
                }
            )
            if not trades.empty:
                check(
                    f"{label}:{r.model}:next_session_entry",
                    bool((trades.entry_cal == trades.sig_cal + 1).all()),
                    "market calendar, not stock bar",
                )
            if not held.empty:
                counts = held.groupby("date").code.nunique().reindex(curve.date, fill_value=0)
                check(
                    f"{label}:{r.model}:holding_counts",
                    np.array_equal(counts.to_numpy(), curve.holding_count.to_numpy()),
                    "ledger rows versus curve",
                )
                days = held.groupby("code").size().sort_values(ascending=False)
                concentration.append(
                    {
                        "period": label,
                        "model": r.model,
                        "unique_held_stocks": int(len(days)),
                        "total_stock_days": int(days.sum()),
                        "top5_stock_days_share_pct": float(days.head(5).sum() / days.sum() * 100),
                        "mean_holdings": float(curve.holding_count.mean()),
                        "max_holdings": int(curve.holding_count.max()),
                        "longest_stock_days": int(days.iloc[0]),
                        "longest_stock_code": str(days.index[0]),
                        "definition": "stock-days across all spells, not uninterrupted holding",
                    }
                )
            if label == "is":
                check(
                    f"is:{r.model}:dates",
                    bool((curve.date <= cfg.is_end).all())
                    and (trades.empty or bool((trades.book_exit_date <= cfg.is_end).all())),
                    "all input and settlement bounded",
                )

    # A bounded independent spot-check of cached RPS denominator, without rebuilding a cache.
    panel, rps = load_prefix(ROOT / "output/trend_start_cache_20260930", cfg.is_end)
    close = panel.field("close")
    # book() converts each valuation to Python float. The legacy normalized
    # portfolio otherwise multiplies by float32 marks, which NumPy 2 keeps in
    # float32. Promote identical prices here to isolate execution-rule parity
    # from that different numeric accumulation policy; do not relax tolerance.
    close_ff = pd.DataFrame(close).ffill(axis=1).to_numpy(dtype=np.float64)
    code_rows = {str(code): row for row, code in enumerate(panel.codes)}
    first = int(np.searchsorted(panel.dates, np.datetime64(cfg.is_start)))
    for model in ("A_retest", "B_trend"):
        accepted = pd.read_csv(OUT / f"is/{model}/portfolio_trades.csv")
        accepted = accepted.rename(
            columns={
                "exit_cal": "exit_cal_open",
                "exit_px": "exit_px_open",
                "exit_at_close": "exit_at_close_open",
                "unfinished": "unfinished_open",
            }
        )
        # The frozen research book has a code/model tie break; the legacy
        # portfolio sorts only by the priority column. Preserve the registered
        # ordering using an ordinal, rather than letting CSV exit order break ties.
        accepted = accepted.sort_values(
            ["entry_cal", "rps120", "code", "model"], ascending=[True, False, True, True]
        )
        accepted["reference_order"] = -np.arange(len(accepted), dtype=float)
        reference, _ = legacy_portfolio(
            accepted,
            "open",
            close_ff,
            code_rows,
            panel.dates,
            first,
            len(panel.dates) - 1,
            cfg.slots,
            priority="reference_order",
        )
        curve = pd.read_csv(OUT / f"is/{model}/equity_cash.csv")
        error = float(np.max(np.abs(reference.to_numpy() * cfg.capital - curve.gross_equity)))
        check(
            f"is:{model}:legacy_gross_curve",
            error < 1e-5,
            f"legacy engine, float64 marks and registered tie order; gross max error={error}",
        )

    cases = []
    for model in ("A_retest", "A_volume", "B_trend", "A_plus_B"):
        chain = pd.read_csv(OUT / f"history/{model}/trade_outcomes.csv")
        accepted = pd.read_csv(OUT / f"history/{model}/portfolio_trades.csv")
        signals = chain.loc[(chain.code == "300308.SZ") & chain.signal_date.str.startswith("2025-")]
        actual = accepted.loc[
            (accepted.code == "300308.SZ") & accepted.signal_date.str.startswith("2025-")
        ]
        cases.append(
            {
                "code": "300308.SZ",
                "model": model,
                "signals_in_2025": len(signals),
                "portfolio_entries": len(actual),
                "first_signal": signals.signal_date.min() if not signals.empty else None,
                "first_entry": actual.entry_date.min() if not actual.empty else None,
                "net_pnl_for_these_trades": actual.pnl.sum(),
                "caveat": "known case, not independent validation; signal-year cohort",
            }
        )
    pd.DataFrame(cases).to_csv(OUT / "zjx_2025_case.csv", index=False, encoding="utf-8-sig")
    rps_checks = []
    for target in (800, len(panel.dates) - 1):
        raw = np.full(len(panel.codes), np.nan)
        for row in range(len(panel.codes)):
            bars = np.flatnonzero(np.isfinite(close[row, : target + 1]))
            if len(bars) >= 251 and bars[-1] == target:
                raw[row] = float(close[row, target]) / float(close[row, bars[-121]]) - 1
        exact64 = pd.Series(raw).rank(method="average", pct=True).to_numpy() * 100
        # signals.cross_section_rps stores trailing returns in float32 BEFORE
        # percentile ranking. Reproduce that documented precision for cache
        # compatibility while also exposing its deviation from exact64 ranks.
        expected = (
            pd.Series(raw.astype(np.float32)).rank(method="average", pct=True).to_numpy() * 100
        )
        valid = np.isfinite(expected)
        error = float(np.max(np.abs(expected[valid] - rps[valid, target])))
        missing_equal = np.array_equal(valid, np.isfinite(rps[:, target]))
        rps_checks.append(
            {
                "date": str(panel.dates[target]),
                "effective_stocks": int(valid.sum()),
                "max_abs_rps_error": error,
                "max_abs_error_vs_float64_rank": float(
                    np.max(np.abs(exact64[valid] - rps[valid, target]))
                ),
                "missingness_matches": missing_equal,
                "precision_policy": "float32 trailing returns before average percentile rank",
            }
        )
        check(
            f"RPS120_denominator:{panel.dates[target]}",
            error < 1e-4 and missing_equal,
            "all effective stocks, float32 cache return policy; two-session spot check only",
        )
    pd.DataFrame(rps_checks).to_csv(
        OUT / "rps_cache_spot_check.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(decomposition).to_csv(
        OUT / "cost_decomposition.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(concentration).to_csv(
        OUT / "position_concentration.csv", index=False, encoding="utf-8-sig"
    )
    del panel, rps, close, close_ff
    # Old file is never overwritten. Its prior per-stock-bar horizons are not
    # silently reinterpreted as market sessions during a suspension.
    old = pd.read_csv(ROOT / "output/daily_system/历史对照.csv")
    with np.load(ROOT / "output/trend_start_cache_20260930/panel.npz", allow_pickle=False) as z:
        dates, codes, close_all = z["dates"], z["codes"], z["close"]
    lookup = {str(code): i for i, code in enumerate(codes)}
    rows = []
    for r in old.to_dict("records"):
        day = int(np.searchsorted(dates, np.datetime64(r["信号日"])))
        if day == len(dates) or str(dates[day]) != r["信号日"]:
            raise ValueError(f"old signal date absent from panel: {r['信号日']}")
        row = lookup[r["代码"]]
        px = float(close_all[row, day])
        item = {"signal_date": r["信号日"], "code": r["代码"], "signal_close": px}
        for h, label_h in ((1, "隔日收益%"), (5, "隔周收益%")):
            target = day + h
            present = target < len(dates)
            future = float(close_all[row, target]) if present else np.nan
            value = (future / px - 1) * 100
            recorded = r[label_h]
            item.update(
                {
                    f"d{h}_date": str(dates[target]) if present else None,
                    f"d{h}_close": future,
                    f"d{h}_return_pct": value,
                    f"d{h}_old_return_pct": recorded,
                    f"d{h}_matches_market_horizon": bool(
                        np.isclose(value, recorded, atol=1e-5, rtol=1e-5, equal_nan=True)
                    ),
                }
            )
        rows.append(item)
    horizon = pd.DataFrame(rows)
    horizon.to_csv(OUT / "old_daily_horizon_audit.csv", index=False, encoding="utf-8-sig")
    direction = []
    for label_d, d1, d5, ex1, ex5 in (
        ("old_recorded", old["隔日收益%"], old["隔周收益%"], old["隔日超额%"], old["隔周超额%"]),
        ("recomputed_market_sessions", horizon.d1_return_pct, horizon.d5_return_pct, None, None),
    ):
        valid = d1.notna() & d5.notna()
        direction.append(
            {
                "source": label_d,
                "rows": len(d1),
                "both_present": int(valid.sum()),
                "same_sign_including_both_zero": int((valid & (np.sign(d1) == np.sign(d5))).sum()),
                "d1_mean_pct": float(d1.mean()),
                "d5_mean_pct": float(d5.mean()),
                "mean_return_same_direction": bool(np.sign(d1.mean()) == np.sign(d5.mean())),
                "d1_excess_mean_pct": float(ex1.mean()) if ex1 is not None else None,
                "d5_excess_mean_pct": float(ex5.mean()) if ex5 is not None else None,
                "excess_same_direction": bool(np.sign(ex1.mean()) == np.sign(ex5.mean()))
                if ex1 is not None and ex5 is not None
                else None,
                "caveat": "recomputed control excess missing; old control means are not repaired",
            }
        )
    pd.DataFrame(direction).to_csv(
        OUT / "old_daily_direction_summary.csv", index=False, encoding="utf-8-sig"
    )
    result = pd.DataFrame(checks)
    result.to_csv(OUT / "independent_checks.csv", index=False, encoding="utf-8-sig")
    failures = result.loc[~result.passed]
    print(f"CSV checks: {len(result)}; failed: {len(failures)}")
    if not failures.empty:
        print(failures.to_string(index=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
