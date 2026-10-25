"""Read-only IS diagnostics of frozen events, not a new strategy search.

All price consumers receive a prefix ending 2021-12-31. No historical-replay
result tables, network quotes, broker interfaces, or parameter selection.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from trend_start.dual_system import (
    Context,
    StudyConfig,
    context,
    load_prefix,
    mean,
    sha256,
    trade_rows,
)
from trend_start.engine import (
    block_bootstrap_mean,
    execution_flags,
    first_close_below,
    first_true_from,
    sell_next_open,
)

ROOT = PROJECT_ROOT
BASE = ROOT / "output/dual_research_20261002_v1"
CACHE = ROOT / "output/trend_start_cache_20260930"
OUT = ROOT / "output/dual_research_is_diagnostics_20261002_v2"
SPEC = ROOT / "docs/IS_DIAGNOSTICS_20261002.md"
MODELS = ("A_retest", "A_volume", "B_trend")
KEYS = ["model", "combo", "code", "sig_cal"]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cfg: StudyConfig
    # Measurement horizons only; never trading knobs or searched choices.
    horizons: tuple[int, ...] = (1, 5, 20)


def stamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def read_is(name: str) -> pd.DataFrame:
    path = (BASE / "is" / name).resolve()
    if not path.is_relative_to((BASE / "is").resolve()):
        raise ValueError("only registered IS result paths are allowed")
    return pd.read_csv(path, float_precision="round_trip")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def rps_band(values: pd.Series) -> pd.Series:
    if ((values < 0) | (values > 100) | values.isna()).any():
        raise ValueError("invalid cached RPS120")
    return pd.cut(
        values,
        (0, 20, 40, 60, 80, 100),
        include_lowest=True,
        labels=["0-20", "20-40", "40-60", "60-80", "80-100"],
    )


def exact_close_return(ctx: Context, signal_cal: int, horizon: int, price: float) -> float:
    target = signal_cal + horizon
    j = int(np.searchsorted(ctx.bars.cal, target))
    if j >= len(ctx.bars.cal) or ctx.bars.cal[j] != target:
        return float("nan")
    return float((ctx.bars.c[j] / price - 1) * 100)


def classify_structure(ctx: Context) -> np.ndarray:
    b = ctx.bars
    ma60, ma120 = mean(b.c, 60), mean(b.c, 120)
    ma60old = np.r_[np.full(5, np.nan), ma60[:-5]]
    ma20old = np.r_[np.full(5, np.nan), b.ma20[:-5]]
    strong = (b.c > b.ma20) & (b.ma20 > ma60) & (ma60 > ma120) & (ma60 > ma60old)
    up20 = (b.c > b.ma20) & (b.ma20 > ma20old)
    return np.where(strong, "ordered_20_60_120_up60", np.where(up20, "up20_only", "other"))


def path_details(ctx: Context, frame: pd.DataFrame, last_cal: int) -> pd.DataFrame:
    b = ctx.bars
    entries = np.asarray(np.searchsorted(b.cal, frame.entry_cal.to_numpy()), dtype=int)
    ma_hit = first_true_from(b.c < b.ma20, entries)
    low_hit = first_close_below(b.c, entries, frame.pocket_low.to_numpy(dtype=float))
    hit = np.minimum(ma_hit, low_hit)
    ex, price, at_close, unfinished, returns = sell_next_open(
        b, hit, execution_flags(b), b.o[entries]
    )
    if not (
        np.array_equal(b.cal[ex], frame.exit_cal)
        and np.array_equal(at_close, frame.exit_at_close)
        and np.array_equal(unfinished, frame.unfinished)
        and np.allclose(price, frame.exit_px, atol=1e-12, rtol=1e-12)
        and np.allclose(returns, frame.ret, atol=1e-12, rtol=1e-12)
    ):
        raise AssertionError("IS exits changed from frozen outcomes")
    structures = classify_structure(ctx)
    records = []
    for k, r in enumerate(frame.itertuples(index=False)):
        sig, entry, exit_bar = int(r.sig_bar), int(entries[k]), int(ex[k])
        h = int(hit[k])
        reason = (
            "is_boundary"
            if h >= len(b.c)
            else (
                "ma20_and_floor"
                if ma_hit[k] == low_hit[k]
                else "ma20_only"
                if ma_hit[k] < low_hit[k]
                else "floor_only"
            )
        )
        record: dict[str, Any] = {
            "model": r.model,
            "combo": r.combo,
            "code": r.code,
            "sig_cal": r.sig_cal,
            "structure": structures[sig],
            "gap_pct": (r.entry_px / r.signal_close - 1) * 100,
            "bias20_pct": (r.signal_close / r.ma20 - 1) * 100,
            "trigger_reason": reason,
            "trigger_date": str(b.dates[h]) if h < len(b.c) else None,
            "hold_market_sessions": int(r.exit_cal - r.entry_cal),
            "hold_stock_bars": exit_bar - entry,
            "in_trade_max_favorable_close_pct": (b.c[entry : exit_bar + 1].max() / r.entry_px - 1)
            * 100,
            "in_trade_max_adverse_close_pct": (b.c[entry : exit_bar + 1].min() / r.entry_px - 1)
            * 100,
            "exit_gross_return_pct": (r.exit_px / r.entry_px - 1) * 100,
            "post_exit_20_full_price_window": False,
            "post_exit_20_reclaims_entry": np.nan,
        }
        for horizon in (1, 5, 20):
            record[f"signal_d{horizon}_close_return_pct"] = exact_close_return(
                ctx, int(r.sig_cal), horizon, float(r.signal_close)
            )
            record[f"post_exit_d{horizon}_close_return_pct"] = (
                exact_close_return(ctx, int(r.exit_cal), horizon, float(r.exit_px))
                if not r.unfinished
                else np.nan
            )
        left = int(np.searchsorted(b.cal, r.exit_cal + 1))
        right = int(np.searchsorted(b.cal, r.exit_cal + 21))
        full = (
            not r.unfinished
            and r.exit_cal + 20 <= last_cal
            and np.array_equal(b.cal[left:right], np.arange(r.exit_cal + 1, r.exit_cal + 21))
        )
        if full:
            record["post_exit_20_full_price_window"] = True
            record["post_exit_20_reclaims_entry"] = bool(b.c[left:right].max() > r.entry_px)
        records.append(record)
    return pd.DataFrame(records)


def describe(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "events": 0,
            "mean_net_return_pct": None,
            "win_pct": None,
            "mean_hold_sessions": None,
            "median_hold_sessions": None,
            "mean_gross_return_pct": None,
            "profit_factor": None,
        }
    ret = frame.ret.to_numpy(dtype=float)
    gross = frame.exit_px / frame.entry_px - 1
    losses = -ret[ret < 0].sum()
    return {
        "events": len(frame),
        "mean_net_return_pct": float(ret.mean() * 100),
        "win_pct": float((ret > 0).mean() * 100),
        "mean_hold_sessions": float((frame.exit_cal - frame.entry_cal).mean()),
        "median_hold_sessions": float((frame.exit_cal - frame.entry_cal).median()),
        "mean_gross_return_pct": float(gross.mean() * 100),
        "profit_factor": float(ret[ret > 0].sum() / losses) if losses else None,
    }


def grouped(frame: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(fields, observed=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(fields, key, strict=True)), **describe(group)})
    return pd.DataFrame(rows)


def paired_admission(frame: pd.DataFrame, cfg: StudyConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    compare = frame.loc[
        frame.disposition.isin(["admitted", "slots_full", "insufficient_cash"])
    ].copy()
    compare["population"] = np.where(
        compare.disposition == "admitted", "admitted", "capacity_blocked"
    )
    daily = compare.groupby(["model", "sig_cal", "signal_date", "population"]).ret.agg(
        ["mean", "size"]
    )
    means = daily["mean"].unstack("population")
    counts = daily["size"].unstack("population")
    pairs = means.reindex(columns=["admitted", "capacity_blocked"]).dropna().reset_index()
    if pairs.empty:
        return pairs, pd.DataFrame(
            columns=["model", "paired_dates", "mean_delta_pct", "ci_lo_pct", "ci_hi_pct"]
        )
    pairs["delta"] = pairs.admitted - pairs.capacity_blocked
    pairs["admitted_events"] = counts.loc[means.dropna().index, "admitted"].to_numpy()
    pairs["blocked_events"] = counts.loc[means.dropna().index, "capacity_blocked"].to_numpy()
    rows = []
    for model, group in pairs.groupby("model"):
        month = pd.to_datetime(group.signal_date).dt.to_period("M").astype(str)
        lo, hi = block_bootstrap_mean(group.delta, month, cfg.bootstrap_n, cfg.bootstrap_seed)
        rows.append(
            {
                "model": model,
                "paired_dates": len(group),
                "mean_delta_pct": float(group.delta.mean() * 100),
                "ci_lo_pct": lo * 100,
                "ci_hi_pct": hi * 100,
                "interpretation": "same-date association, not isolated causal RPS effect",
            }
        )
    return pairs, pd.DataFrame(rows)


def collect(scope: Scope) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = scope.cfg
    panel, rps = load_prefix(CACHE, cfg.is_end)
    evidence = read_is("signal_evidence.csv")
    if not evidence.signal_date.between(cfg.is_start, cfg.is_end).all():
        raise ValueError("causal evidence contains dates outside IS")
    admitted, rejected, chain = [], [], []
    for model in MODELS:
        accepted = read_is(f"{model}/portfolio_trades.csv")
        if not accepted.book_exit_date.le(cfg.is_end).all():
            raise ValueError("IS book contains post-boundary settlement")
        admitted.append(accepted)
        rejected.append(read_is(f"{model}/portfolio_rejected.csv"))
        chain.append(read_is(f"{model}/trade_outcomes.csv"))
    accept = pd.concat(admitted, ignore_index=True)
    reject = pd.concat(rejected, ignore_index=True)
    if accept.duplicated(KEYS).any() or reject.duplicated(KEYS).any():
        raise ValueError("duplicate portfolio event key")
    statuses = pd.concat(
        [
            accept[KEYS].assign(disposition="admitted"),
            reject[KEYS + ["reason"]].rename(columns={"reason": "disposition"}),
        ],
        ignore_index=True,
    )
    if statuses.duplicated(KEYS).any():
        raise ValueError("event was both admitted and rejected")
    by_code = evidence.groupby("code", sort=False)
    frames, details = [], []
    lookup = {str(code): i for i, code in enumerate(panel.codes)}
    for k, (code, group) in enumerate(by_code):
        if k % 600 == 0:
            print(f"[IS diagnostic] {k}/{len(by_code)}", flush=True)
        ctx = context(panel, rps, lookup[str(code)])
        if ctx is None:
            raise ValueError(f"causal evidence without sufficient IS prices: {code}")
        specs = []
        for r in group.itertuples(index=False):
            j = int(np.searchsorted(ctx.bars.cal, r.sig_cal))
            if j >= len(ctx.bars.cal) or ctx.bars.cal[j] != r.sig_cal:
                raise ValueError(f"signal bar absent: {code} {r.signal_date}")
            specs.append(
                {
                    "model": r.model,
                    "combo": int(r.combo),
                    "sig_bar": j,
                    "pocket_low": float(r.pocket_low),
                    "volume_bar": int(r.volume_bar),
                }
            )
        frame, failed = trade_rows(ctx, specs, cfg.is_start)
        if not failed.empty or len(frame) != len(group):
            raise AssertionError("frozen executable offers changed")
        frames.append(frame)
        details.append(path_details(ctx, frame, len(panel.dates) - 1))
    offered = pd.concat(frames, ignore_index=True).merge(
        statuses, on=KEYS, validate="one_to_one", how="left"
    )
    offered = offered.merge(pd.concat(details, ignore_index=True), on=KEYS, validate="one_to_one")
    if offered.disposition.isna().any() or len(offered) != len(statuses):
        raise AssertionError("offered/admitted/rejected reconciliation failed")
    matched = accept.merge(
        offered[KEYS + ["ret", "entry_px", "exit_px"]],
        on=KEYS,
        suffixes=("_book", "_offer"),
        validate="one_to_one",
    )
    for field in ("ret", "entry_px", "exit_px"):
        if not np.allclose(
            matched[f"{field}_book"], matched[f"{field}_offer"], rtol=1e-12, atol=1e-12
        ):
            raise AssertionError(f"frozen admitted {field} changed")
    offered["rps_band"] = rps_band(offered.rps120)
    offered["signal_year"] = pd.to_datetime(offered.signal_date).dt.year
    return offered, accept, pd.concat(chain, ignore_index=True)


def analyze(scope: Scope) -> None:
    cfg = scope.cfg
    offered, accepted, chain = collect(scope)
    offered.to_csv(OUT / "is_event_diagnostics.csv", index=False, encoding="utf-8-sig")
    summary = []
    costs = []
    exits = []
    for model in MODELS:
        f = offered.loc[offered.model == model]
        a = accepted.loc[accepted.model == model]
        for label, population in (
            ("all_executable_offers", f),
            ("nonoverlap_stock_chain", chain.loc[chain.model == model]),
            ("portfolio_admitted", a),
            ("capacity_blocked", f.loc[f.disposition.isin(["slots_full", "insufficient_cash"])]),
        ):
            summary.append({"model": model, "population": label, **describe(population)})
        curve = read_is(f"{model}/equity_cash.csv")
        gross = float((a.units * (a.book_exit_px - a.entry_px)).sum())
        cost = float(
            (a.cost - a.units * a.entry_px + a.units * a.book_exit_px - a.net_proceeds).sum()
        )
        profit = float(a.pnl.sum())
        last = float(curve.equity.iloc[-1]) - cfg.capital
        if abs(gross - cost - profit) > 1e-5 or abs(last - profit) > 1e-5:
            raise AssertionError("IS price/cost/cash identity failed")
        costs.append(
            {
                "model": model,
                "gross_price_pnl": gross,
                "modeled_cost": cost,
                "net_pnl": profit,
                "mean_cash_pct": float(curve.cash_ratio.mean() * 100),
                "mean_holdings": float(curve.holding_count.mean()),
                "entry_cash_sum": float(a.cost.sum()),
                "modeled_cost_over_entry_cash_pct": float(cost / a.cost.sum() * 100),
                "gross_pnl_over_entry_cash_pct": float(gross / a.cost.sum() * 100),
                "cost_share_of_positive_gross_pct": cost / gross * 100 if gross > 0 else None,
            }
        )
        actual = f.loc[f.disposition == "admitted"]
        for reason, group in actual.groupby("trigger_reason"):
            losers = group.loc[(group.ret < 0) & (~group.unfinished)]
            mature = losers.loc[losers.post_exit_20_full_price_window]
            exits.append(
                {
                    "model": model,
                    "trigger_reason": reason,
                    **describe(group),
                    "losing_events": len(losers),
                    "losing_full_post20_events": len(mature),
                    "losing_post20_reclaims_entry_count": int(
                        mature.post_exit_20_reclaims_entry.sum()
                    ),
                    "losing_post20_reclaims_entry_pct": mature.post_exit_20_reclaims_entry.mean()
                    * 100
                    if len(mature)
                    else None,
                    "mean_in_trade_favorable_close_pct": float(
                        group.in_trade_max_favorable_close_pct.mean()
                    ),
                    "mean_post_exit_d5_close_return_pct": (
                        group.post_exit_d5_close_return_pct.mean()
                    ),
                    "mean_post_exit_d20_close_return_pct": (
                        group.post_exit_d20_close_return_pct.mean()
                    ),
                    "short_5_sessions_count": int((group.hold_market_sessions <= 5).sum()),
                }
            )
    tables = {
        "population_summary": pd.DataFrame(summary),
        "cost_cash_diagnostics": pd.DataFrame(costs),
        "exit_path_diagnostics": pd.DataFrame(exits),
        "structure_groups": grouped(offered, ["model", "disposition", "structure"]),
        "rps_groups": grouped(offered, ["model", "disposition", "rps_band"]),
        "yearly_event_groups": grouped(offered, ["model", "disposition", "signal_year"]),
    }
    pairs, pair_summary = paired_admission(offered, cfg)
    tables.update(admission_paired_dates=pairs, admission_paired_summary=pair_summary)
    ranking = pd.read_csv(BASE / "grid_is_summary.csv")
    eligible = ranking.loc[ranking.eligible]
    tables["grid_context"] = pd.DataFrame(
        [
            {
                "allowed_combos": len(ranking),
                "eligible_combos": len(eligible),
                "eligible_positive_absolute_mean": int((eligible.mean_ret > 0).sum()),
                "eligible_positive_relative_mean": int((eligible.mean_excess > 0).sum()),
                "caveat": "existing IS grid statistics only; no new selection or validation",
            }
        ]
    )
    for name, frame in tables.items():
        frame.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(costs).to_string(index=False), flush=True)
    print(pd.DataFrame(summary).to_string(index=False), flush=True)
    print(pair_summary.to_string(index=False), flush=True)


def main() -> None:
    frozen = json.loads((BASE / "freeze.json").read_text(encoding="utf-8"))
    for name, recorded in frozen["hashes"].items():
        if sha256(ROOT / name) != recorded:
            raise RuntimeError(f"frozen input/source changed: {name}")
    scope = Scope(cfg=StudyConfig.model_validate(frozen["cfg"]))
    sources = [
        Path(__file__),
        SPEC,
        ROOT / "tests/test_dual_is_diagnostics.py",
        BASE / "freeze.json",
        BASE / "grid_is_summary.csv",
        CACHE / "panel.npz",
        CACHE / "rps.npz",
    ]
    sources += sorted((BASE / "is").rglob("*.csv"))
    hashes = {str(p.relative_to(ROOT)): sha256(p) for p in sources}
    OUT.mkdir(exist_ok=False)
    write_json(
        OUT / "registered.json",
        {
            "created_at": stamp(),
            "scope": scope.model_dump(),
            "hashes": hashes,
            "parameter_change": False,
            "no_new_selection": True,
            "price_input_end": scope.cfg.is_end,
            "evidence_status": "exploratory_in_sample_only",
        },
    )
    analyze(scope)
    if hashes != {str(p.relative_to(ROOT)): sha256(p) for p in sources}:
        raise RuntimeError("sources changed during diagnostics")
    write_json(
        OUT / "verification.json",
        {
            "checked_at": stamp(),
            "hashes_unchanged": True,
            "offered_admitted_rejected_reconciled": True,
            "exits_match_frozen_engine": True,
            "gross_cost_net_equity_reconciled": True,
            "read_history_result_tables": False,
            "independent_validation": False,
        },
    )


if __name__ == "__main__":
    main()
