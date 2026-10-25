"""Memory-bounded completion after the registered replay's MemoryError.

No search or parameter changes. Frozen functions produce events; a disk spool
and streamed rejection log replace only the original unbounded Python lists.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import csv
import gc
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import run_dual_research as registered
from trend_start.dual_system import (
    TRADE_COLUMNS,
    Book,
    StudyConfig,
    context,
    control_specs,
    load_prefix,
    metrics,
    nonoverlap,
    sha256,
    trade_rows,
)
from trend_start.engine import BUY_COST, sell_cost
from trend_start.panel import Panel

OUT = registered.OUT
INTS = {
    "combo",
    "code_row",
    "sig_bar",
    "sig_cal",
    "entry_cal",
    "exit_bar",
    "exit_cal",
    "volume_bar",
}
BOOLS = {"exit_at_close", "unfinished"}
TEXTS = {"model", "code", "signal_date", "entry_date", "exit_date"}
HOLD_COLUMNS = [
    "date",
    "code",
    "model",
    "units",
    "cost",
    "mark_price",
    "market_value",
    "weight",
    "stale_mark",
]
BOOK_EXTRA = [
    "book_exit_date",
    "book_exit_cal",
    "book_exit_px",
    "units",
    "cost",
    "net_proceeds",
    "pnl",
    "book_ret",
    "boundary_mark",
]


def stream_book(
    records: Iterable[dict[str, Any]],
    panel: Panel,
    cfg: StudyConfig,
    start: str,
    rejected_path: Path,
) -> Book:
    """Same decisions as frozen book(); no all-events or all-rejections list."""
    cal, close = panel.dates, panel.field("close")
    first, end = int(np.searchsorted(cal, np.datetime64(start))), len(cal) - 1
    sc = sell_cost(cal)
    held: dict[str, dict[str, Any]] = {}
    last_exit: dict[str, int] = {}
    cash, previous = cfg.capital, cfg.capital
    curves: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    iterator = iter(records)
    pending = next(iterator, None)

    def exit_position(code: str, day: int, px: float, boundary: bool) -> None:
        nonlocal cash
        p = held.pop(code)
        value = p["units"] * px * (1 - sc[day])
        cash += value
        last_exit[code] = day
        trades.append(
            {
                **p["event"],
                "book_exit_date": str(cal[day]),
                "book_exit_cal": day,
                "book_exit_px": px,
                "units": p["units"],
                "cost": p["cost"],
                "net_proceeds": value,
                "pnl": value - p["cost"],
                "book_ret": value / p["cost"] - 1,
                "boundary_mark": boundary,
            }
        )

    with rejected_path.open("x", encoding="utf-8-sig", newline="") as log:
        writer = csv.DictWriter(log, fieldnames=TRADE_COLUMNS + ["reason"])
        writer.writeheader()
        for day in range(first, end + 1):
            for code in list(held):
                r = held[code]["event"]
                if r["exit_cal"] == day and not r["exit_at_close"]:
                    exit_position(code, day, r["exit_px"], False)
            target = previous / cfg.slots
            while pending is not None and int(pending["entry_cal"]) <= day:
                r, pending = pending, next(iterator, None)
                if int(r["sig_cal"]) < first:
                    continue
                if int(r["entry_cal"]) != day:
                    raise ValueError("spooled events must be ordered and inside the period")
                code, reason = str(r["code"]), ""
                if code in held or int(r["sig_cal"]) < last_exit.get(code, -1):
                    reason = "already_held_or_signal_before_exit"
                elif len(held) >= cfg.slots:
                    reason = "slots_full"
                elif cash <= target * 0.25:
                    reason = "insufficient_cash"
                if reason:
                    writer.writerow({**r, "reason": reason})
                    continue
                alloc = min(target, cash)
                cash -= alloc
                held[code] = {
                    "units": alloc / (float(r["entry_px"]) * (1 + BUY_COST)),
                    "cost": alloc,
                    "event": r,
                    "mark": np.nan,
                }
            for code in list(held):
                r = held[code]["event"]
                if r["exit_cal"] == day and r["exit_at_close"] and not r["unfinished"]:
                    exit_position(code, day, r["exit_px"], False)
            mv = 0.0
            for code, p in held.items():
                raw = float(close[int(p["event"]["code_row"]), day])
                if np.isfinite(raw):
                    p["mark"] = raw
                px = float(p["mark"])
                if not np.isfinite(px):
                    raise ValueError(f"missing valuation: {code} {cal[day]}")
                value = p["units"] * px
                mv += value
                holdings.append(
                    {
                        "date": str(cal[day]),
                        "code": code,
                        "model": p["event"]["model"],
                        "units": p["units"],
                        "cost": p["cost"],
                        "mark_price": px,
                        "market_value": value,
                        "weight": 0.0,
                        "stale_mark": bool(np.isnan(raw)),
                    }
                )
            gross = cash + mv
            fee = mv * sc[day] if day == end else 0.0
            count = len(held)
            if cash < -1e-6 or count > cfg.slots:
                raise AssertionError("cash/slot invariant failed")
            for h in holdings[-count:] if count else []:
                h["weight"] = h["market_value"] / gross
            curves.append(
                {
                    "date": str(cal[day]),
                    "equity": gross - fee,
                    "gross_equity": gross,
                    "cash_before_boundary_mark": cash,
                    "position_market_value": mv,
                    "boundary_valuation_cost": fee,
                    "holding_count": count,
                    "cash_ratio": cash / gross,
                }
            )
            previous = gross
    if pending is not None:
        raise ValueError("event outside panel")
    for code in list(held):
        exit_position(code, end, float(held[code]["mark"]), True)
    trades_frame = pd.DataFrame(trades, columns=TRADE_COLUMNS + BOOK_EXTRA)
    if trades_frame.empty:
        contribution = pd.DataFrame(columns=["code", "trades", "pnl", "capital_return_pct"])
    else:
        contribution = trades_frame.groupby("code").agg(trades=("pnl", "size"), pnl=("pnl", "sum"))
        contribution["capital_return_pct"] = contribution.pnl / cfg.capital * 100
        contribution = contribution.reset_index().sort_values("pnl", ascending=False)
        if abs(trades_frame.pnl.sum() - (curves[-1]["equity"] - cfg.capital)) > 1e-5:
            raise AssertionError("PnL reconciliation failed")
    return Book(
        pd.DataFrame(curves),
        pd.DataFrame(holdings, columns=HOLD_COLUMNS),
        trades_frame,
        pd.DataFrame(columns=TRADE_COLUMNS + ["reason"]),
        contribution,
    )


def sql_records(connection: sqlite3.Connection, model: str) -> Iterator[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    condition = "model IN ('A_retest','B_trend')" if model == "A_plus_B" else "model = ?"
    args = () if model == "A_plus_B" else (model,)
    cursor = connection.execute(
        f"SELECT * FROM events WHERE {condition} ORDER BY entry_cal, rps120 DESC, code, model", args
    )
    for row in cursor:
        r = dict(row)
        for col in BOOLS:
            r[col] = bool(r[col])
        for col in TRADE_COLUMNS:
            if r[col] is None:
                r[col] = np.nan
        yield r


def spool(connection: sqlite3.Connection, panel: Panel, rps: Any, cfg: StudyConfig) -> None:
    """Reconstruct control; reuse recorded causal A/B signals, never select again."""
    history = OUT / "history"
    causal = pd.read_csv(history / "signal_evidence.csv", float_precision="round_trip")
    causal = causal.loc[causal.model.isin(["A_retest", "B_trend"])]
    groups = {code: group for code, group in causal.groupby("code")}
    schema = ",".join(
        f"{c} {'TEXT' if c in TEXTS else 'INTEGER' if c in INTS | BOOLS else 'REAL'}"
        for c in TRADE_COLUMNS
    )
    connection.execute(f"CREATE TABLE events ({schema})")
    insert = f"INSERT INTO events VALUES ({','.join('?' for _ in TRADE_COLUMNS)})"
    chain_path = OUT / "recovery_chain.csv"
    sums = np.zeros((4, len(panel.dates)))
    counts = np.zeros_like(sums)
    with chain_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(TRADE_COLUMNS)
        for i, code in enumerate(panel.codes):
            if i % 500 == 0:
                connection.commit()
                print(f"[RESUME spool] {i}/{len(panel.codes)}", flush=True)
            ctx = context(panel, rps, i)
            if ctx is None:
                continue
            specs = control_specs(ctx)
            group = groups.get(str(code))
            if group is not None:
                for r in group.itertuples(index=False):
                    j = int(np.searchsorted(ctx.bars.cal, r.sig_cal))
                    if ctx.bars.cal[j] != r.sig_cal:
                        raise AssertionError("causal signal calendar mismatch")
                    specs.append(
                        {
                            "model": r.model,
                            "combo": int(r.combo),
                            "sig_bar": j,
                            "pocket_low": float(r.pocket_low),
                            "volume_bar": int(r.volume_bar),
                        }
                    )
            frame, _ = trade_rows(ctx, specs, cfg.history_start)
            if frame.empty:
                continue
            records = frame.itertuples(index=False, name=None)
            connection.executemany(insert, records)
            for row in nonoverlap(frame).itertuples(index=False, name=None):
                writer.writerow(row)
            control = frame.loc[frame.model == "control"]
            cal = control.sig_cal.to_numpy(dtype=int)
            for k, field in enumerate(("ret", "d1", "d5")):
                values = control[field].to_numpy(dtype=float)
                ok = np.isfinite(values)
                np.add.at(sums[k], cal[ok], values[ok])
                np.add.at(counts[k], cal[ok], 1)
            np.add.at(counts[3], cal, 1)
    connection.commit()
    means = np.divide(
        sums[:3], counts[:3], out=np.full_like(sums[:3], np.nan), where=counts[:3] > 0
    )
    existing = pd.read_csv(
        history / "control_by_date.csv", index_col="sig_cal", float_precision="round_trip"
    )
    ix = existing.index.to_numpy(dtype=int)
    if not np.array_equal(counts[3, ix], existing.stocks):
        raise AssertionError("control cohort counts changed")
    for k, field in enumerate(("mean_ret", "d1", "d5")):
        if not np.allclose(means[k, ix], existing[field], atol=1e-12, rtol=1e-12, equal_nan=True):
            raise AssertionError(f"reconstructed control {field} changed")
    connection.execute("CREATE INDEX event_order ON events(entry_cal, rps120 DESC, code, model)")
    connection.execute("CREATE TABLE complete (cohort_parity INTEGER)")
    connection.execute("INSERT INTO complete VALUES (1)")
    connection.commit()


def read_book(folder: Path) -> Book:
    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(folder / name, float_precision="round_trip")

    return Book(
        read("equity_cash.csv"),
        read("holdings.csv"),
        read("portfolio_trades.csv"),
        pd.DataFrame(),
        read("stock_contribution.csv"),
    )


def save_model(
    folder: Path, chain: pd.DataFrame, result: Book, panel: Panel, cfg: StudyConfig
) -> None:
    chain.to_csv(folder / "trade_outcomes.csv", index=False, encoding="utf-8-sig")
    for name, frame in (
        ("equity_cash", result.curve),
        ("holdings", result.holdings),
        ("portfolio_trades", result.trades),
        ("stock_contribution", result.contribution),
    ):
        frame.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8-sig")
    registered.daily_forward_table(chain, panel).to_csv(
        folder / "daily_forward.csv", index=False, encoding="utf-8-sig"
    )
    prior, annual = cfg.capital, []
    for year, group in result.curve.groupby(pd.to_datetime(result.curve.date).dt.year):
        annual.append(
            {
                "year": int(year),
                "return_pct": (group.equity.iloc[-1] / prior - 1) * 100,
                "end_equity": group.equity.iloc[-1],
            }
        )
        prior = float(group.equity.iloc[-1])
    pd.DataFrame(annual).to_csv(folder / "annual.csv", index=False, encoding="utf-8-sig")


def main() -> None:
    frozen = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
    if registered.hashes() != frozen["hashes"]:
        raise RuntimeError("frozen source changed; recovery forbidden")
    if not (OUT / "history_access.json").is_file() or (OUT / "history/metrics.csv").exists():
        raise RuntimeError("not an unfinished registered replay")
    before = {str(p.relative_to(OUT)): sha256(p) for p in (OUT / "history").rglob("*.csv")}
    manifest = OUT / "recovery_manifest.json"
    recovery_hashes = {
        name: sha256(registered.ROOT / name)
        for name in ("resume_dual_research.py", "tests/test_dual_recovery.py")
    }
    if not manifest.exists():
        registered.write_json(
            manifest,
            {
                "started_at": registered.timestamp(),
                "reason": "MemoryError in original control rejection list",
                "policy": "resume missing stages; no re-selection or parameter change",
                "source_hashes": recovery_hashes,
                "existing_outputs": before,
            },
        )
    else:
        old = json.loads(manifest.read_text(encoding="utf-8"))
        if old["source_hashes"] != recovery_hashes:
            raise RuntimeError("recovery source changed")
    cfg = StudyConfig.model_validate(frozen["cfg"])
    with np.load(registered.CACHE / "panel.npz", allow_pickle=False) as z:
        last = str(z["dates"][-1])
    panel, rps = load_prefix(registered.CACHE, last)
    connection = sqlite3.connect(OUT / "recovery_events.sqlite")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-4096")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "complete" not in tables:
        if tables:
            raise RuntimeError(
                "incomplete disk spool; retain evidence and report instead of silently resetting"
            )
        spool(connection, panel, rps, cfg)
    del rps
    gc.collect()
    chain_all = pd.read_csv(OUT / "recovery_chain.csv", float_precision="round_trip")
    chain_all = chain_all.sort_values(["sig_cal", "code"]).reset_index(drop=True)
    cohort = pd.read_csv(OUT / "history/control_by_date.csv", index_col="sig_cal").mean_ret
    rows = []
    parity = []
    for model in ("A_retest", "A_volume", "B_trend", "control", "A_plus_B"):
        dest = OUT / "history" / model
        if dest.exists():
            result = read_book(dest)
            chain = pd.read_csv(dest / "trade_outcomes.csv", float_precision="round_trip")
            if model in ("A_retest", "B_trend"):
                test_path = OUT / f"recovery_parity_{model}_rejected.csv"
                streamed = stream_book(
                    sql_records(connection, model), panel, cfg, cfg.history_start, test_path
                )
                pd.testing.assert_frame_equal(
                    streamed.curve, result.curve, check_dtype=False, rtol=1e-12, atol=1e-8
                )
                pd.testing.assert_frame_equal(
                    streamed.trades, result.trades, check_dtype=False, rtol=1e-12, atol=1e-8
                )
                parity.append({"model": model, "curve_and_trades_unchanged": True})
                del streamed
        else:
            dest.mkdir()
            result = stream_book(
                sql_records(connection, model),
                panel,
                cfg,
                cfg.history_start,
                dest / "portfolio_rejected.csv",
            )
            chain = (
                chain_all.loc[chain_all.model.isin(["A_retest", "B_trend"])]
                if model == "A_plus_B"
                else chain_all.loc[chain_all.model == model]
            )
            save_model(dest, chain, result, panel, cfg)
        m = metrics(chain, cohort, result, cfg)
        m.update(model=model, start=cfg.history_start, end=last)
        rows.append(m)
        print(f"[RESUME ledger] {model} completed", flush=True)
        del result, chain
        gc.collect()
    connection.close()
    if registered.hashes() != frozen["hashes"] or recovery_hashes != {
        name: sha256(registered.ROOT / name) for name in recovery_hashes
    }:
        raise RuntimeError("source changed during recovery")
    if any(sha256(OUT / name) != value for name, value in before.items()):
        raise RuntimeError("pre-existing partial results changed")
    stats = pd.DataFrame(rows)
    stats.to_csv(OUT / "history/metrics.csv", index=False, encoding="utf-8-sig")
    reject = pd.read_csv(OUT / "history/rejected_execution.csv")
    watch = reject.loc[
        (reject.reason == "pending_next_session")
        & (reject.sig_cal == len(panel.dates) - 1)
        & reject.model.isin(["A_retest", "B_trend"])
    ].copy()
    watch = watch.sort_values(["rps120", "code", "model"], ascending=[False, True, True])
    watch["state"] = "candidate_for_human_review"
    watch.to_csv(OUT / "history/latest_candidates.csv", index=False, encoding="utf-8-sig")
    registered.write_json(
        OUT / "verification.json",
        {
            "checked_at": registered.timestamp(),
            "hashes_unchanged": True,
            "independent_validation": False,
            "no_parameter_change_after_history": True,
            "evidence_status": "insufficient_independent_validation",
            "recovery_manifest": "recovery_manifest.json",
            "streamed_parity": parity,
            "existing_outputs_unchanged": True,
            "control_cohort_unchanged": True,
        },
    )
    registered.review(pd.read_csv(OUT / "is/metrics.csv"), stats)
    print(stats.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
