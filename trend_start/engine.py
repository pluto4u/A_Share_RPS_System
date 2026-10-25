"""Execution model: signal at close s -> buy next session open; exit on close < MA20.

Two exit timings:
  "open":  close<MA20 on bar j -> sell at open of bar j+1.
  "close": close<MA20 on bar j -> sell at close of bar j (T+1: if j is the entry
           bar, sell at next open instead).
  "open2": sensitivity only - two consecutive closes < MA20 -> next open.
Limit handling (conservative): no buy when the entry open is at limit-up; an
open at limit-down sells at that bar's close if it is not a one-price bar, a
one-price limit-down bar cannot be sold and the sale rolls to the next bar; a
close sale at limit-down rolls to the next open. Unfinished trades are marked
at the last close with selling cost deducted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LIMIT_TOL = 0.003
BUY_COST = 0.00025 + 0.001  # commission + slippage
SELL_COST_BASE = 0.00025 + 0.001
STAMP_CUT = np.datetime64("2023-08-28")
GEM_20PCT = np.datetime64("2020-08-24")


def limit_pct(code: str, dates: np.ndarray) -> np.ndarray:
    if code.startswith(("688", "689")):
        return np.full(len(dates), 0.2)
    if code.startswith(("300", "301")):
        return np.where(dates >= GEM_20PCT, 0.2, 0.1)
    return np.full(len(dates), 0.1)


def sell_cost(dates: np.ndarray) -> np.ndarray:
    return SELL_COST_BASE + np.where(dates >= STAMP_CUT, 0.0005, 0.001)


@dataclass
class StockBars:
    code: str
    cal: np.ndarray  # calendar positions of traded bars
    dates: np.ndarray  # datetime64[D]
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    ma20: np.ndarray
    ma50: np.ndarray | None = None
    ma60: np.ndarray | None = None
    ma150: np.ndarray | None = None
    don20: np.ndarray | None = None  # lowest low of the prior 20 bars; NaN until defined


@dataclass
class Outcomes:
    """Outcome if bought at bar e (signal at e-1), for every e; NaN when not buyable."""

    buyable: np.ndarray
    exit_bar: dict[str, np.ndarray]
    exit_px: dict[str, np.ndarray]
    exit_at_close: dict[str, np.ndarray]
    unfinished: dict[str, np.ndarray]
    ret: dict[str, np.ndarray]


def _sell_open(k: np.ndarray, n: int, down_open: np.ndarray, locked: np.ndarray,
               o: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    k = k.copy()
    for _ in range(n):
        inside = k < n
        roll = np.zeros_like(inside)
        roll[inside] = locked[k[inside]]
        if not roll.any():
            break
        k[roll] += 1
    unfinished = k >= n
    kk = np.minimum(k, n - 1)
    at_close = ~unfinished & down_open[kk]
    px = np.where(unfinished | at_close, c[kk], o[kk])
    return kk, px, at_close | unfinished, unfinished


VARIANTS = ("open", "close", "open2")  # open2: two consecutive closes < MA20, sensitivity only
FWD = (20, 60)


def outcomes(b: StockBars) -> Outcomes:
    n = len(b.c)
    prev = np.r_[np.nan, b.c[:-1]]
    lim = limit_pct(b.code, b.dates) - LIMIT_TOL
    with np.errstate(invalid="ignore", divide="ignore"):
        up_open = b.o / prev - 1 >= lim
        down_open = b.o / prev - 1 <= -lim
        down_close = b.c / prev - 1 <= -lim
    locked = down_close & (b.h == b.lo)
    below = b.c < b.ma20  # NaN MA20 -> False
    e = np.arange(n)
    gap_ok = np.r_[False, np.diff(b.cal) == 1]
    buyable = gap_ok & ~up_open & np.isfinite(b.ma20)
    sc = sell_cost(b.dates)
    res_bar, res_px, res_close, res_unf, res_ret = {}, {}, {}, {}, {}
    entry_cost = b.o * (1 + BUY_COST)

    def first_signal(sig: np.ndarray, earliest: np.ndarray) -> np.ndarray:
        nxt = np.minimum.accumulate(np.where(sig, e, n)[::-1])[::-1]
        return np.append(nxt, n)[np.minimum(earliest, n)]

    for v in VARIANTS:
        if v == "open2":
            # both closes must be on/after the entry bar
            sig = below & np.r_[False, below[:-1]]
            j = first_signal(sig, e + 1)
        else:
            j = first_signal(below, e)
        no_sig = j >= n
        jj = np.minimum(j, n - 1)
        k, px, at_close, unf = _sell_open(np.minimum(j + 1, n), n, down_open, locked, b.o, b.c)
        if v == "close":
            use_next = (j == e) | down_close[jj]
            k = np.where(use_next, k, jj)
            px = np.where(use_next, px, b.c[jj])
            at_close = np.where(use_next, at_close, True)
            unf = np.where(use_next, unf, False)
        k = np.where(no_sig, n - 1, k)
        px = np.where(no_sig, b.c[-1], px)
        res_bar[v], res_px[v] = k, px
        res_close[v], res_unf[v] = at_close | no_sig, unf | no_sig
        r = px * (1 - sc[k]) / entry_cost - 1
        res_ret[v] = np.where(buyable, r, np.nan)
    for h in FWD:
        # fixed-horizon hold from entry open to the close h-1 bars later, costs included
        k = e + h - 1
        r = np.full(n, np.nan)
        ok = k < n
        r[ok] = b.c[k[ok]] * (1 - sc[k[ok]]) / entry_cost[ok] - 1
        res_ret[f"fwd{h}"] = np.where(buyable, r, np.nan)
    level_exits = {
        "ma50": b.ma50, "ma60": b.ma60, "ma150": b.ma150, "don20": b.don20,
    }
    flags = (down_open, locked)
    for name, level in level_exits.items():
        if level is None:
            continue
        tripped = np.isfinite(level) & (b.c < level)
        j = first_true_from(tripped, e)
        k, px, at_close, unf, r = sell_next_open(b, j, flags, b.o)
        res_bar[name], res_px[name], res_close[name], res_unf[name] = k, px, at_close, unf
        res_ret[name] = np.where(buyable, r, np.nan)
    return Outcomes(buyable, res_bar, res_px, res_close, res_unf, res_ret)


def first_true_from(sig: np.ndarray, start: np.ndarray) -> np.ndarray:
    """For each start index, the smallest j >= start with sig[j], else n."""
    n = len(sig)
    pos = np.arange(n)
    nxt = np.minimum.accumulate(np.where(sig, pos, n)[::-1])[::-1]
    start = np.asarray(start, dtype=np.int64)
    out = np.full(start.shape, n, dtype=np.int64)
    ok = start < n
    out[ok] = nxt[start[ok]]
    return out


def sell_next_open(b: StockBars, trigger: np.ndarray, flags: tuple[np.ndarray, np.ndarray],
                   entry_open: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Trigger on the close of bar j, sell at the next open, with the limit-down rules."""
    down_open, locked = flags
    n = len(b.c)
    trigger = np.asarray(trigger, dtype=np.int64)
    k, px, at_close, unf = _sell_open(np.minimum(trigger + 1, n), n, down_open, locked, b.o, b.c)
    missing = trigger >= n
    k = np.where(missing, n - 1, k)
    px = np.where(missing, b.c[-1], px)
    unf = unf | missing
    ret = px * (1 - sell_cost(b.dates)[k]) / (np.asarray(entry_open, dtype=float) * (1 + BUY_COST)) - 1
    return k, px, at_close | missing, unf, ret


def execution_flags(b: StockBars) -> tuple[np.ndarray, np.ndarray]:
    prev = np.r_[np.nan, b.c[:-1]]
    lim = limit_pct(b.code, b.dates) - LIMIT_TOL
    with np.errstate(invalid="ignore", divide="ignore"):
        down_open = b.o / prev - 1 <= -lim
        down_close = b.c / prev - 1 <= -lim
    return down_open, down_close & (b.h == b.lo)


def first_close_below(close: np.ndarray, entry: np.ndarray, level: np.ndarray) -> np.ndarray:
    """First bar on/after entry whose close is below a constant level. n if none."""
    n = len(close)
    entry = np.asarray(entry, dtype=np.int64)
    level = np.asarray(level, dtype=float)
    out = np.full(len(entry), n, dtype=np.int64)
    idx = np.arange(n)
    for a0 in range(0, len(entry), 128):
        e = entry[a0 : a0 + 128]
        lv = level[a0 : a0 + 128]
        hit = (idx >= e[:, None]) & (close[None, :] < lv[:, None]) & np.isfinite(lv)[:, None]
        found = hit.any(1)
        out[a0 : a0 + len(e)] = np.where(found, hit.argmax(1), n)
    return out


def chandelier_trigger(high: np.ndarray, close: np.ndarray, atr: np.ndarray,
                       entry: np.ndarray, mult: float = 3.0) -> np.ndarray:
    """Close below the highest high since entry minus mult * ATR. ATR must already lag."""
    n = len(close)
    out = np.full(len(entry), n, dtype=np.int64)
    for i, e in enumerate(np.asarray(entry, dtype=np.int64)):
        if e >= n or not np.isfinite(atr[e:]).any():
            continue
        stop = np.maximum.accumulate(high[e:]) - mult * atr[e:]
        hit = np.flatnonzero(np.isfinite(stop) & (close[e:] < stop))
        if len(hit):
            out[i] = e + int(hit[0])
    return out


def event_rows(b: StockBars, out: Outcomes, sig: np.ndarray, strategy: str,
               extra: dict[str, np.ndarray]) -> list[dict]:
    """One row per signal bar s whose next bar is buyable."""
    rows = []
    for s in np.flatnonzero(sig):
        e = s + 1
        if e >= len(b.c) or not out.buyable[e]:
            continue
        row = {
            "strategy": strategy,
            "code": b.code,
            "sig_cal": int(b.cal[s]),
            "sig_bar": int(s),
            "entry_bar": int(e),
            "entry_cal": int(b.cal[e]),
            "entry_px": float(b.o[e]),
        }
        for h in FWD:
            row[f"ret_fwd{h}"] = float(out.ret[f"fwd{h}"][e])
        for v in VARIANTS:
            k = int(out.exit_bar[v][e])
            row[f"exit_bar_{v}"] = k
            row[f"exit_cal_{v}"] = int(b.cal[k])
            row[f"exit_px_{v}"] = float(out.exit_px[v][e])
            row[f"exit_at_close_{v}"] = bool(out.exit_at_close[v][e])
            row[f"unfinished_{v}"] = bool(out.unfinished[v][e])
            row[f"ret_{v}"] = float(out.ret[v][e])
            row[f"hold_{v}"] = k - e
        k = row["exit_bar_open"]
        row["mfe_open"] = float(b.c[e : k + 1].max() / b.o[e] - 1)
        for name, arr in extra.items():
            row[name] = float(arr[s])
        rows.append(row)
    return rows


def chain(events: pd.DataFrame, variant: str, reentry: pd.DataFrame | None = None,
          reentry_window: int = 30) -> pd.DataFrame:
    """Non-overlapping trades per stock: a new signal needs sig_bar >= previous exit bar.

    Re-entry signals are admitted only within `reentry_window` bars after an exit
    of a trade in the same chain.
    """
    frames = [events.assign(kind="primary")]
    if reentry is not None and not reentry.empty:
        frames.append(reentry.assign(kind="reentry"))
    allev = pd.concat(frames, ignore_index=True).sort_values(["code", "sig_bar", "kind"])
    keep = []
    for _, g in allev.groupby("code", sort=False):
        last_exit = -1
        had_trade = False
        for idx, bar, kind, xb in zip(g.index, g.sig_bar, g.kind, g[f"exit_bar_{variant}"]):
            if bar < last_exit:
                continue
            if kind == "reentry" and not (had_trade and bar - last_exit <= reentry_window):
                continue
            keep.append(idx)
            last_exit, had_trade = xb, True
    return allev.loc[keep].sort_values(["sig_cal", "code"])


def portfolio(events: pd.DataFrame, variant: str, close_ff: np.ndarray, code_row: dict[str, int],
              dates: np.ndarray, start: int, end: int, slots: int = 20,
              reentry: pd.DataFrame | None = None, reentry_window: int = 30,
              priority: str = "rps120") -> tuple[pd.Series, pd.DataFrame]:
    """Equal-weight slot portfolio over calendar [start, end]; marked at `end` close."""
    ev = events.loc[(events.sig_cal >= start) & (events.entry_cal <= end)].copy()
    ev["kind"] = "primary"
    if reentry is not None and not reentry.empty:
        re = reentry.loc[(reentry.sig_cal >= start) & (reentry.entry_cal <= end)].copy()
        re["kind"] = "reentry"
        ev = pd.concat([ev, re], ignore_index=True)
    ev = ev.sort_values(["entry_cal", "kind", priority], ascending=[True, True, False])
    by_day = {d: g for d, g in ev.groupby("entry_cal")}
    sc = sell_cost(dates)
    cash, equity_prev = 1.0, 1.0
    held: dict[str, dict] = {}
    last_exit: dict[str, int] = {}
    curve = np.full(end - start + 1, np.nan)
    trades = []

    def close_pos(code: str, d: int, px: float, unfinished: bool) -> None:
        nonlocal cash
        p = held.pop(code)
        proceeds = p["shares"] * px * (1 - sc[d])
        cash += proceeds
        last_exit[code] = d
        trades.append({**p["row"], "pf_exit_cal": d, "pf_ret": proceeds / p["cost"] - 1,
                       "pf_unfinished": unfinished})

    for d in range(start, end + 1):
        for code in [k for k, p in held.items()
                     if p["exit_cal"] == d and not p["at_close"]]:
            close_pos(code, d, held[code]["exit_px"], False)
        if d in by_day and len(held) < slots:
            target = equity_prev / slots
            for r in by_day[d].itertuples(index=False):
                if len(held) >= slots or cash <= target * 0.25:
                    break
                if r.code in held:
                    continue
                if r.kind == "reentry":
                    le = last_exit.get(r.code)
                    if le is None or r.sig_cal - le > reentry_window:
                        continue
                alloc = min(target, cash)
                cash -= alloc
                held[r.code] = {
                    "shares": alloc / (r.entry_px * (1 + BUY_COST)),
                    "cost": alloc,
                    "exit_cal": getattr(r, f"exit_cal_{variant}"),
                    "exit_px": getattr(r, f"exit_px_{variant}"),
                    "at_close": getattr(r, f"exit_at_close_{variant}"),
                    "unfinished": getattr(r, f"unfinished_{variant}"),
                    "row": r._asdict(),
                }
        for code in [k for k, p in held.items()
                     if p["exit_cal"] == d and p["at_close"] and not p["unfinished"]]:
            close_pos(code, d, held[code]["exit_px"], False)
        mtm = sum(p["shares"] * close_ff[code_row[c], d] for c, p in held.items())
        equity_prev = cash + mtm
        curve[d - start] = equity_prev
    for code in list(held):
        close_pos(code, end, float(close_ff[code_row[code], end]), True)
    return pd.Series(curve, index=pd.DatetimeIndex(dates[start : end + 1])), pd.DataFrame(trades)


def curve_stats(curve: pd.Series) -> dict[str, float]:
    r = curve.pct_change().dropna()
    years = len(curve) / 244
    total = curve.iloc[-1] / curve.iloc[0]
    dd = curve / curve.cummax() - 1
    return {
        "total_return_pct": (total - 1) * 100,
        "cagr_pct": (total ** (1 / years) - 1) * 100 if years > 0 else np.nan,
        "max_drawdown_pct": dd.min() * 100,
        "ann_vol_pct": r.std() * np.sqrt(244) * 100,
        "sharpe": r.mean() / r.std() * np.sqrt(244) if r.std() > 0 else np.nan,
    }


def trade_stats(t: pd.DataFrame, variant: str) -> dict[str, float]:
    r = t[f"ret_{variant}"].dropna()
    if r.empty:
        return {"trades": 0}
    wins, losses = r[r > 0], r[r <= 0]
    return {
        "trades": int(len(r)),
        "win_rate_pct": len(wins) / len(r) * 100,
        "mean_ret_pct": r.mean() * 100,
        "median_ret_pct": r.median() * 100,
        "avg_win_pct": wins.mean() * 100 if len(wins) else np.nan,
        "avg_loss_pct": losses.mean() * 100 if len(losses) else np.nan,
        "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else np.nan,
        "p90_ret_pct": r.quantile(0.9) * 100,
        "p10_ret_pct": r.quantile(0.1) * 100,
        "median_hold": float(t[f"hold_{variant}"].median()),
        "median_lateness60_pct": float(t["lateness60"].median() * 100),
        "median_bias20_pct": float(t["bias20"].median() * 100),
    }


def block_bootstrap_mean(x: pd.Series, month: pd.Series, n: int = 2000, seed: int = 7) -> tuple[float, float]:
    """95% CI of the mean, resampling calendar months (signals cluster in time)."""
    g = pd.DataFrame({"x": x.to_numpy(), "m": month.to_numpy()}).dropna()
    if g.empty:
        return np.nan, np.nan
    sums = g.groupby("m").x.agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(n, len(sums)))
    s, c = sums["sum"].to_numpy()[idx].sum(1), sums["count"].to_numpy()[idx].sum(1)
    means = s / c
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
