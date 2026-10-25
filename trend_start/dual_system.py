"""Causal daily research, with truncated inputs and inspectable cash accounting.

No broker interfaces. Adjusted units are NOT actual exchange shares.
Execution prices/limits/costs are delegated to the existing engine unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from trend_start.engine import (
    BUY_COST,
    StockBars,
    block_bootstrap_mean,
    execution_flags,
    first_close_below,
    first_true_from,
    outcomes,
    sell_cost,
    sell_next_open,
)
from trend_start.panel import Panel

Array: TypeAlias = NDArray[Any]
MIN_HISTORY = 251
GRID = list(
    product(
        (20, 40, 60),
        (0.10, 0.15, 0.20),
        (10, 20, 40),
        (0.08, 0.12, 0.15),
        (0.6, 0.8),
        (1.5, 2.0),
        (3, 5),
        (0.03, 0.05),
    )
)
TRADE_COLUMNS = [
    "model",
    "combo",
    "code",
    "code_row",
    "sig_bar",
    "sig_cal",
    "signal_date",
    "entry_cal",
    "entry_date",
    "entry_px",
    "exit_bar",
    "exit_cal",
    "exit_date",
    "exit_px",
    "exit_at_close",
    "unfinished",
    "ret",
    "rps120",
    "signal_close",
    "ma20",
    "pocket_low",
    "volume_bar",
    "d1",
    "d5",
]


class StudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capital: float = Field(default=1_000_000.0, gt=0)
    slots: Literal[20] = 20
    max_drawdown: float = Field(default=0.20, ge=0.20, le=0.20)
    is_start: Literal["2016-09-01"] = "2016-09-01"
    is_end: Literal["2021-12-31"] = "2021-12-31"
    history_start: Literal["2022-01-01"] = "2022-01-01"
    min_is_trades: Literal[300] = 300
    bootstrap_n: Literal[2000] = 2000
    bootstrap_seed: Literal[7] = 7
    b_slope: Literal[5] = 5
    b_pullback: Literal[3] = 3
    b_tolerance: float = Field(default=0.05, ge=0.05, le=0.05)


@dataclass
class Context:
    bars: StockBars
    vol: Array
    amount: Array
    rps: Array
    code_row: int


@dataclass
class Book:
    curve: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    rejected: pd.DataFrame
    contribution: pd.DataFrame


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def load_prefix(cache: Path, cutoff: str) -> tuple[Panel, Array]:
    """Nothing downstream receives prices after cutoff; no panel is rebuilt."""
    with np.load(cache / "panel.npz", allow_pickle=False) as z:
        dates = z["dates"]
        if not len(dates) or np.any(np.diff(dates) <= np.timedelta64(0, "D")):
            raise ValueError("invalid or empty market calendar")
        keep = int(np.searchsorted(dates, np.datetime64(cutoff), side="right"))
        if not keep:
            raise ValueError(f"no data at or before {cutoff}")
        codes = z["codes"]
        if len(np.unique(codes)) != len(codes):
            raise ValueError("duplicate stock codes")
        fields = {
            f: z[f][:, :keep].copy() for f in ("open", "high", "low", "close", "vol", "amount")
        }
        for f, a in fields.items():
            if a.shape != (len(codes), keep):
                raise ValueError(f"invalid field shape: {f}")
            if not np.array_equal(np.isnan(a), np.isnan(fields["close"])):
                raise ValueError(f"inconsistent missing bars: {f}")
        panel = Panel(dates[:keep].copy(), codes.copy(), fields, z["index_close"][:keep].copy())
    with np.load(cache / "rps.npz", allow_pickle=False) as z:
        rps = z["RPS120"][:, :keep].copy()
    if rps.shape != fields["close"].shape:
        raise ValueError("RPS120 and panel shapes differ")
    if np.datetime64(panel.dates[-1]) > np.datetime64(cutoff):
        raise AssertionError("future prices escaped cutoff")
    return panel, rps


def mean(x: Array, n: int) -> Array:
    return cast(Array, pd.Series(x).rolling(n).mean().to_numpy(dtype=float, copy=True))


def context(panel: Panel, rps: Array, row: int) -> Context | None:
    cols = np.flatnonzero(np.isfinite(panel.field("close")[row]))
    if len(cols) < MIN_HISTORY:
        return None  # explicit eligibility exclusion, not a parse failure
    o, h, lo, c, v, amt = (
        panel.field(f)[row, cols].astype(float)
        for f in ("open", "high", "low", "close", "vol", "amount")
    )
    if (
        not all(np.isfinite(a).all() for a in (o, h, lo, c, v, amt))
        or np.any(lo <= 0)
        or np.any(v <= 0)
        or np.any(amt < 0)
        or np.any(h < np.maximum(o, c))
        or np.any(lo > np.minimum(o, c))
    ):
        raise ValueError(f"invalid OHLCV for {panel.codes[row]}")
    bars = StockBars(str(panel.codes[row]), cols, panel.dates[cols], o, h, lo, c, mean(c, 20))
    return Context(bars, v, amt, rps[row, cols].astype(float), row)


def a_layout(ctx: Context, decline: int, base: int) -> dict[str, Array] | None:
    b = ctx.bars
    need = decline + base
    if len(b.c) <= need:
        return None
    starts = np.arange(len(b.c) - need)
    t: Array = starts + need

    def win(x: Array, n: int, shift: int = 0) -> Array:
        return np.lib.stride_tricks.sliding_window_view(x, n)[starts + shift]

    dc, dh, dl = win(b.c, decline), win(b.h, decline), win(b.lo, decline)
    bc, bh, bl = win(b.c, base, decline), win(b.h, base, decline), win(b.lo, base, decline)
    dv, bv = win(ctx.vol, decline).mean(1), win(ctx.vol, base, decline).mean(1)
    return {
        "t": t,
        "decline_order": (dc.argmax(1) < decline // 2) & (dc.argmin(1) >= decline // 2),
        "drop": 1 - dc.min(1) / dc.max(1),
        "base_range": (bh.max(1) - bl.min(1)) / bc[:, -1],
        "decline_range": (dh.max(1) - dl.min(1)) / dc[:, -1],
        "volume_ratio": bv / dv,
        "expansion": ctx.vol[t] / bv,
        "right": (b.c[t] > b.o[t]) & (b.c[t] > b.ma20[t]),
        "mature": t >= MIN_HISTORY - 1,
    }


def a_mask(la: dict[str, Array], params: tuple[Any, ...]) -> Array:
    _, drop, _, width, dry, expansion, _, _ = params
    result = (
        la["mature"]
        & la["decline_order"]
        & (la["drop"] >= drop)
        & (la["base_range"] <= width)
        & (la["base_range"] < la["decline_range"])
        & (la["volume_ratio"] <= dry)
        & (la["expansion"] >= expansion)
        & la["right"]
    )
    return np.asarray(result, dtype=bool)


def retest(ctx: Context, t0: int, k: int, tolerance: float) -> tuple[int, float] | None:
    """Freeze the running low at confirmation, never use a later low."""
    b = ctx.bars
    low, vol = np.inf, 0.0
    for j in range(t0 + 1, min(t0 + k + 1, len(b.c))):
        if not np.isfinite(b.ma20[j]) or b.c[j] < b.ma20[j]:
            break
        low = min(low, b.lo[j])
        vol += ctx.vol[j]
        if (
            b.c[j] <= b.ma20[j] * (1 + tolerance)
            and b.c[j] < b.c[t0]
            and vol / (j - t0) < ctx.vol[t0]
        ):
            return j, float(low)
    return None


def a_specs(
    ctx: Context, combo: int | None = None, volume_baseline: bool = False
) -> list[dict[str, Any]]:
    ids = list(range(len(GRID))) if combo is None else [combo]
    layouts: dict[tuple[int, int], dict[str, Array] | None] = {}
    pb_cache: dict[tuple[int, int, float], tuple[int, float] | None] = {}
    rows: list[dict[str, Any]] = []
    for cid in ids:
        p = GRID[cid]
        pair = (int(p[0]), int(p[2]))
        if pair not in layouts:
            layouts[pair] = a_layout(ctx, *pair)
        la = layouts[pair]
        if la is None:
            continue
        for t0 in la["t"][a_mask(la, p)]:
            t0 = int(t0)
            if volume_baseline:
                rows.append(
                    {
                        "model": "A_volume",
                        "combo": cid,
                        "sig_bar": t0,
                        "pocket_low": np.nan,
                        "volume_bar": t0,
                    }
                )
                continue
            key = (t0, int(p[6]), float(p[7]))
            if key not in pb_cache:
                pb_cache[key] = retest(ctx, *key)
            hit = pb_cache[key]
            if hit is not None:
                j, floor = hit
                rows.append(
                    {
                        "model": "A_retest",
                        "combo": cid,
                        "sig_bar": j,
                        "pocket_low": floor,
                        "volume_bar": t0,
                    }
                )
    return rows


def b_features(ctx: Context, cfg: StudyConfig) -> pd.DataFrame:
    b = ctx.bars
    c, lo, v, ma = (pd.Series(x) for x in (b.c, b.lo, ctx.vol, b.ma20))
    k = cfg.b_pullback
    mature = np.arange(len(c)) >= MIN_HISTORY - 1
    lifted = lo.rolling(20).min() > lo.shift(20).rolling(20).min()
    slope = ma > ma.shift(cfg.b_slope)
    near = (c >= ma) & (c <= ma * (1 + cfg.b_tolerance))
    quiet = v.shift(1).rolling(k).mean() < v.shift(k + 1).rolling(20).mean()
    near_prior = near.shift(1).rolling(k).sum() == k
    actual_drop = (c < c.shift(1)).shift(1).rolling(k).sum() >= 1
    bull = (c > pd.Series(b.o)) & (c > c.shift(1))
    signal = mature & lifted & slope & near & quiet & near_prior & actual_drop & bull
    return pd.DataFrame(
        {
            "lifted": lifted,
            "slope": slope,
            "near": near,
            "quiet": quiet,
            "near_prior": near_prior,
            "actual_drop": actual_drop,
            "bull": bull,
            "signal": signal,
            "floor": lo.shift(1).rolling(k).min(),
        }
    )


def b_specs(ctx: Context, cfg: StudyConfig) -> list[dict[str, Any]]:
    features = b_features(ctx, cfg)
    return [
        {
            "model": "B_trend",
            "combo": -1,
            "sig_bar": int(j),
            "pocket_low": float(features.floor.iloc[j]),
            "volume_bar": -1,
        }
        for j in np.flatnonzero(features.signal.to_numpy())
    ]


def control_specs(ctx: Context) -> list[dict[str, Any]]:
    b = ctx.bars
    ok = (np.arange(len(b.c)) >= MIN_HISTORY - 1) & (b.c > b.ma20) & (mean(ctx.amount, 20) >= 1e8)
    return [
        {"model": "control", "combo": -1, "sig_bar": int(j), "pocket_low": np.nan, "volume_bar": -1}
        for j in np.flatnonzero(ok)
    ]


def calendar_return(b: StockBars, signal_bar: int, horizon: int) -> float:
    target = int(b.cal[signal_bar]) + horizon
    j = int(np.searchsorted(b.cal, target))
    if j >= len(b.cal) or b.cal[j] != target:
        return float("nan")
    return float(b.c[j] / b.c[signal_bar] - 1)


def trade_rows(
    ctx: Context, specs: list[dict[str, Any]], start: str, forward: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Future outcomes go to evaluation rows only; rejection rows retain causal fields."""
    if not specs:
        return pd.DataFrame(columns=TRADE_COLUMNS), pd.DataFrame()
    b = ctx.bars
    sig = np.asarray([r["sig_bar"] for r in specs], dtype=int)
    floors = np.asarray([r["pocket_low"] for r in specs], dtype=float)
    # Distinct volume events may confirm on the same date; keep the first causal event.
    keep = (
        ~pd.DataFrame(
            {"combo": [r["combo"] for r in specs], "sig": sig, "model": [r["model"] for r in specs]}
        )
        .duplicated()
        .to_numpy()
    )
    keep &= b.dates[sig] >= np.datetime64(start)
    sig, floors = sig[keep], floors[keep]
    specs = [r for r, ok in zip(specs, keep, strict=True) if ok]
    if not len(sig):
        return pd.DataFrame(columns=TRADE_COLUMNS), pd.DataFrame()
    e = sig + 1
    out = outcomes(b)
    ok = e < len(b.c)
    ok[ok] &= out.buyable[e[ok]] & np.isfinite(ctx.rps[sig[ok]])
    rejected: list[dict[str, Any]] = []
    for reject_idx in np.flatnonzero(~ok):
        reject_bar = int(sig[reject_idx])
        reason = "pending_next_session"
        if e[reject_idx] < len(b.c):
            reason = (
                "missing_rps"
                if not np.isfinite(ctx.rps[reject_bar])
                else "suspended_next_session"
                if b.cal[e[reject_idx]] != b.cal[reject_bar] + 1
                else "limit_up_or_invalid_entry"
            )
        rejected.append(
            {
                "code": b.code,
                "model": specs[reject_idx]["model"],
                "signal_date": str(b.dates[reject_bar]),
                "sig_cal": int(b.cal[reject_bar]),
                "reason": reason,
                "signal_close": float(b.c[reject_bar]),
                "ma20": float(b.ma20[reject_bar]),
                "pocket_low": float(floors[reject_idx]),
                "rps120": float(ctx.rps[reject_bar]),
            }
        )
    sig, floors, e = sig[ok], floors[ok], e[ok]
    selected = [r for r, valid in zip(specs, ok, strict=True) if valid]
    if not len(e):
        return pd.DataFrame(columns=TRADE_COLUMNS), pd.DataFrame(rejected)
    trigger = np.minimum(first_true_from(b.c < b.ma20, e), first_close_below(b.c, e, floors))
    xb, xp, at_close, unfinished, returns = sell_next_open(b, trigger, execution_flags(b), b.o[e])
    records = []
    for i, r in enumerate(selected):
        j, ent, ex = int(sig[i]), int(e[i]), int(xb[i])
        records.append(
            {
                **r,
                "code": b.code,
                "code_row": ctx.code_row,
                "sig_cal": int(b.cal[j]),
                "signal_date": str(b.dates[j]),
                "entry_cal": int(b.cal[ent]),
                "entry_date": str(b.dates[ent]),
                "entry_px": float(b.o[ent]),
                "exit_bar": ex,
                "exit_cal": int(b.cal[ex]),
                "exit_date": str(b.dates[ex]),
                "exit_px": float(xp[i]),
                "exit_at_close": bool(at_close[i]),
                "unfinished": bool(unfinished[i]),
                "ret": float(returns[i]),
                "rps120": float(ctx.rps[j]),
                "signal_close": float(b.c[j]),
                "ma20": float(b.ma20[j]),
                "d1": calendar_return(b, j, 1) if forward else np.nan,
                "d5": calendar_return(b, j, 5) if forward else np.nan,
            }
        )
    return pd.DataFrame(records, columns=TRADE_COLUMNS), pd.DataFrame(rejected)


def nonoverlap(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    kept: list[int] = []
    ordered = frame.sort_values(["model", "code", "combo", "sig_bar", "volume_bar"])
    groups = pd.factorize(pd.MultiIndex.from_frame(ordered[["model", "code", "combo"]]))[0]
    previous_group, last = -1, -1
    for index, group, signal, exit_bar, unfinished in zip(
        ordered.index.to_numpy(),
        groups,
        ordered.sig_bar.to_numpy(),
        ordered.exit_bar.to_numpy(),
        ordered.unfinished.to_numpy(),
        strict=True,
    ):
        if group != previous_group:
            last, previous_group = -1, int(group)
        if signal >= last:
            kept.append(int(index))
            last = 10**9 if unfinished else int(exit_bar)
    return frame.loc[kept].sort_values(["sig_cal", "code"]).reset_index(drop=True)


def book(events: pd.DataFrame, panel: Panel, cfg: StudyConfig, start: str) -> Book:
    """Offered signals do not occupy slots unless actually admitted by this book."""
    cal = panel.dates
    first = int(np.searchsorted(cal, np.datetime64(start)))
    end = len(cal) - 1
    close = pd.DataFrame(panel.field("close")).ffill(axis=1).to_numpy()
    sc = sell_cost(cal)
    ev = events.copy()
    if not ev.empty:
        ev = ev.loc[ev.sig_cal >= first].sort_values(
            ["entry_cal", "rps120", "code", "model"], ascending=[True, False, True, True]
        )
    by_day = {int(d): g for d, g in ev.groupby("entry_cal")} if not ev.empty else {}
    held: dict[str, dict[str, Any]] = {}
    last_exit: dict[str, int] = {}
    cash, previous = cfg.capital, cfg.capital
    curves: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

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

    for day in range(first, end + 1):
        for code in list(held):
            p = held[code]
            if p["event"]["exit_cal"] == day and not p["event"]["exit_at_close"]:
                exit_position(code, day, p["event"]["exit_px"], False)
        target = previous / cfg.slots
        if day in by_day:
            for r in by_day[day].to_dict("records"):
                code = str(r["code"])
                reason = ""
                if code in held or int(r["sig_cal"]) < last_exit.get(code, -1):
                    reason = "already_held_or_signal_before_exit"
                elif len(held) >= cfg.slots:
                    reason = "slots_full"
                elif cash <= target * 0.25:
                    reason = "insufficient_cash"
                if reason:
                    rejected.append({**r, "reason": reason})
                    continue
                alloc = min(target, cash)
                cash -= alloc
                held[code] = {
                    "units": alloc / (float(r["entry_px"]) * (1 + BUY_COST)),
                    "cost": alloc,
                    "event": r,
                }
        for code in list(held):
            r = held[code]["event"]
            if r["exit_cal"] == day and r["exit_at_close"] and not r["unfinished"]:
                exit_position(code, day, r["exit_px"], False)
        mv = 0.0
        for code, p in held.items():
            px = float(close[int(p["event"]["code_row"]), day])
            if not np.isfinite(px):
                raise ValueError(f"missing valuation for {code} at {cal[day]}")
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
                    "stale_mark": bool(
                        np.isnan(panel.field("close")[int(p["event"]["code_row"]), day])
                    ),
                }
            )
        gross = cash + mv
        boundary_fee = mv * sc[day] if day == end else 0.0
        equity = gross - boundary_fee
        count = len(held)
        if cash < -1e-6 or count > cfg.slots:
            raise AssertionError("cash/slot invariant failed")
        for r in holdings[-count:] if count else []:
            r["weight"] = r["market_value"] / gross
        curves.append(
            {
                "date": str(cal[day]),
                "equity": equity,
                "gross_equity": gross,
                "cash_before_boundary_mark": cash,
                "position_market_value": mv,
                "boundary_valuation_cost": boundary_fee,
                "holding_count": count,
                "cash_ratio": cash / gross,
            }
        )
        previous = gross
    for code in list(held):
        p = held[code]
        exit_position(code, end, float(close[int(p["event"]["code_row"]), end]), True)
    t = pd.DataFrame(
        trades,
        columns=TRADE_COLUMNS
        + [
            "book_exit_date",
            "book_exit_cal",
            "book_exit_px",
            "units",
            "cost",
            "net_proceeds",
            "pnl",
            "book_ret",
            "boundary_mark",
        ],
    )
    if t.empty:
        contribution = pd.DataFrame(columns=["code", "trades", "pnl", "capital_return_pct"])
    else:
        contribution = t.groupby("code").agg(trades=("pnl", "size"), pnl=("pnl", "sum"))
        contribution["capital_return_pct"] = contribution.pnl / cfg.capital * 100
        contribution = contribution.reset_index().sort_values("pnl", ascending=False)
        residual = float(t.pnl.sum()) - (float(curves[-1]["equity"]) - cfg.capital)
        if abs(residual) > 1e-5:
            raise AssertionError(f"contribution does not reconcile: {residual}")
    return Book(
        pd.DataFrame(curves),
        pd.DataFrame(
            holdings,
            columns=[
                "date",
                "code",
                "model",
                "units",
                "cost",
                "mark_price",
                "market_value",
                "weight",
                "stale_mark",
            ],
        ),
        t,
        pd.DataFrame(rejected, columns=TRADE_COLUMNS + ["reason"]),
        contribution,
    )


def metrics(
    frame: pd.DataFrame, control: pd.Series, result: Book, cfg: StudyConfig
) -> dict[str, Any]:
    curve = result.curve
    equity = curve.equity.to_numpy(dtype=float)
    peak = np.maximum.accumulate(np.r_[cfg.capital, equity])[1:]
    dd = equity / peak - 1
    years = len(equity) / 244
    out: dict[str, Any] = {
        "signal_chain_trades": len(frame),
        "portfolio_trades": len(result.trades),
        "net_profit": float(equity[-1] - cfg.capital),
        "cagr_pct": float(((equity[-1] / cfg.capital) ** (1 / years) - 1) * 100),
        "max_drawdown_pct": float(dd.min() * 100),
        "max_holdings": int(curve.holding_count.max()),
        "mean_holdings": float(curve.holding_count.mean()),
        "mean_cash_pct": float(curve.cash_ratio.mean() * 100),
        "unique_portfolio_stocks": int(result.trades.code.nunique())
        if not result.trades.empty
        else 0,
        "excess_mean_pct": None,
        "ci_lo_pct": None,
        "ci_hi_pct": None,
        "mean_trade_ret_pct": None,
        "top5_positive_pnl_share_pct": None,
        "risk_limit_pass": bool(dd.min() >= -cfg.max_drawdown),
    }
    if not frame.empty:
        x = frame.ret.to_numpy() - control.reindex(frame.sig_cal.to_numpy()).to_numpy()
        month = pd.Series(pd.to_datetime(frame.signal_date).dt.to_period("M").astype(str))
        low, high = block_bootstrap_mean(pd.Series(x), month, cfg.bootstrap_n, cfg.bootstrap_seed)
        out.update(
            excess_mean_pct=float(np.nanmean(x) * 100),
            ci_lo_pct=low * 100,
            ci_hi_pct=high * 100,
            mean_trade_ret_pct=float(frame.ret.mean() * 100),
        )
    pos = result.contribution.loc[result.contribution.pnl > 0, "pnl"]
    if not pos.empty:
        out["top5_positive_pnl_share_pct"] = float(pos.head(5).sum() / pos.sum() * 100)
    out["evidence_status"] = "insufficient_independent_validation"
    return out
