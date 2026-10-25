"""Trend-stock core distilled from the Mistery skills (trend-judgment + warrior-520), tested on local TDX.

Pre-registered 2026-10-24 before any run; parameters are not tuned afterwards.
  Universe   non-ST (today's names: survivorship caveat), >=250 traded bars, 20d avg amount >= 1e8
  Trend      close > MA250 and MA20 > MA60 (A); full MA5>MA10>MA20>MA60 and close > MA250 (B)
             -- MA5>MA10 cannot hold on the day MA5 first crosses MA20, so A uses the medium-term form
  Entry A    MA5 crosses above MA20 and vol >= 1.5x prior 20d avg; skipped on/after an ice day (<=22% up)
  Entry B    low <= MA20*1.02, close >= MA20, MA5 rising, vol < prior 5d avg
  Execution  buy t+1 open (skip if it opens at limit-up); A-share T+1
  Exits      two closes < MA20 | MA5 crosses below MA20 -> next open; intraday -8% from day 2;
             7th session close <= entry -> next open; cap 120 sessions; locked limit-down rolls
  Gate       SSE and an equal-weight (mean, |r|<25%) index both: close > MA60 and MA20 > MA60
  Periods    IS signals 2016-01..2021-12, then one look at 2022-01..as-of (already-seen history)
  Portfolio  trend_start.slot_portfolio.slot_compound, 20 slots, prio = higher 20d amount first
Statistics only; no buy/sell instructions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = next(p for p in Path(__file__).absolute().parents if (p / "rps_analyzer").is_dir())
sys.path[:0] = [str(ROOT), str(ROOT / "skills/a-share-rotation-trader/scripts")]
from run_daily_rps import names_from_tnf, read_day  # noqa: E402
from trend_start.daily_board import build_recent_panel  # noqa: E402
from trend_start.engine import BUY_COST, LIMIT_TOL, sell_cost  # noqa: E402
from trend_start.icepoint import limit_matrix  # noqa: E402
from trend_start.slot_portfolio import slot_compound  # noqa: E402

VOL_SURGE, PULL_TOUCH, STOP, TIME_DAYS, MAX_HOLD = 1.5, 1.02, 0.08, 7, 120
MIN_BARS, MIN_AMOUNT, ICE = 250, 1e8, 0.22
IS_START, OOS_START = pd.Timestamp("2016-01-04"), pd.Timestamp("2022-01-04")


def roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    return pd.DataFrame(a).T.rolling(w, min_periods=w).mean().T.to_numpy(np.float32)


def sell_rolled(k, o, c, lock, lim_close, m, at_close):
    while k < m:
        if not np.isfinite(o[k]):
            k += 1; at_close = False; continue
        if at_close and not lim_close[k]:
            return k, c[k], at_close
        if not at_close and not lock[k]:
            return k, o[k], at_close
        k += 1; at_close = False
    return None, np.nan, at_close


def simulate(i, t, A):
    o, h, lo, c, ma5, ma20 = (A[k][i] for k in ("O", "H", "L", "C", "MA5", "MA20"))
    lock, limc, m = A["lock"][i], A["limc"][i], A["m"]
    e = t + 1
    entry = float(o[e]); stop = entry * (1 - STOP)
    last = min(e + MAX_HOLD - 1, m - 1)
    prev_below = False
    for d in range(e, last + 1):
        if not np.isfinite(c[d]):
            continue
        if d > e and not lock[d]:
            if o[d] <= stop:
                return d, float(o[d]), "stop"
            if lo[d] <= stop:
                return d, stop, "stop"
        below = c[d] < ma20[d]
        dead = ma5[d] < ma20[d] and d > 0 and ma5[d - 1] >= ma20[d - 1]
        timeout = (d - e) == TIME_DAYS - 1 and c[d] <= entry
        reason = "ma20_2d" if (below and prev_below) else "dead_cross" if dead else "time7" if timeout else None
        prev_below = below
        if reason:
            k, px, _ = sell_rolled(d + 1, o, c, lock, limc, m, False)
            return (k, float(px), reason) if k is not None else (None, float(c[d]), "open_end")
    if last == m - 1:
        return None, float(c[last]), "open_end"
    k, px, _ = sell_rolled(last, o, c, lock, limc, m, True)
    return (k, float(px), "max_hold") if k is not None else (None, float(c[last]), "open_end")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default="2026-10-23")
    ap.add_argument("--sessions", type=int, default=2900)
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    args = ap.parse_args()
    panel, audit = build_recent_panel(args.tdx_root, pd.Timestamp(args.as_of), sessions=args.sessions)
    dates = pd.DatetimeIndex(panel.dates); codes = panel.codes.astype(str)
    O, H, L, C, V, AMT = (panel.field(f).astype(np.float32) for f in ("open", "high", "low", "close", "vol", "amount"))
    n, m = C.shape
    print(f"[PANEL] {n} x {m}, {dates[0].date()}..{dates[-1].date()}", flush=True)
    MA = {k: roll_mean(C, k) for k in (5, 10, 20, 60, 250)}
    v20 = np.full_like(V, np.nan); v20[:, 1:] = roll_mean(V, 20)[:, :-1]
    v5 = np.full_like(V, np.nan); v5[:, 1:] = roll_mean(V, 5)[:, :-1]
    amt20 = roll_mean(AMT, 20)
    P = np.full_like(C, np.nan); P[:, 1:] = C[:, :-1]
    lim = (limit_matrix(panel.codes, panel.dates) - LIMIT_TOL).astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        chg = C / P - 1
        limc = chg <= -lim
        lock = limc & (H <= L)
        up_open = (O / P - 1) >= lim
    bars = np.cumsum(np.isfinite(C), axis=1)
    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    st = np.array(["ST" in names.get(c, "").upper() or "退" in names.get(c, "") for c in codes])

    # market gate and ice days
    traded = np.isfinite(chg)
    adv = (np.where(traded, chg > 0, False).sum(0) / np.maximum(traded.sum(0), 1))
    ice = adv <= ICE
    ice_recent = ice | np.r_[False, ice[:-1]]
    r = np.where(np.abs(chg) < 0.25, chg, np.nan)
    ew = pd.Series(np.cumprod(1 + np.nan_to_num(np.nanmean(r, axis=0))), index=dates)
    sse = read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day").set_index("trade_date").close.reindex(dates)
    def ok(s):
        return ((s > s.rolling(60).mean()) & (s.rolling(20).mean() > s.rolling(60).mean())).to_numpy()
    gate = ok(sse) & ok(ew)

    with np.errstate(invalid="ignore"):
        base = (~st[:, None]) & (bars >= MIN_BARS) & (amt20 >= MIN_AMOUNT) & np.isfinite(C)
        mid = (C > MA[250]) & (MA[20] > MA[60])
        full = (MA[5] > MA[10]) & (MA[10] > MA[20]) & (MA[20] > MA[60]) & (C > MA[250])
        cross_up = (MA[5] > MA[20]) & (np.roll(MA[5], 1, 1) <= np.roll(MA[20], 1, 1))
        sigA = base & mid & cross_up & (V >= VOL_SURGE * v20) & ~ice_recent[None, :]
        ma5_up = MA[5] > np.roll(MA[5], 1, 1)
        sigB = base & full & (L <= MA[20] * PULL_TOUCH) & (C >= MA[20]) & ma5_up & (V < v5)
    sigA[:, :1] = sigB[:, :1] = False
    A = {"O": O, "H": H, "L": L, "C": C, "MA5": MA[5], "MA20": MA[20], "lock": lock, "limc": limc, "m": m}
    sc = sell_cost(panel.dates)
    t_is = int(dates.searchsorted(IS_START))

    rows = []
    for entry_kind, sig0 in (("A_golden_cross", sigA), ("B_pullback", sigB)):
        for gated in (False, True):
            sig = sig0 & gate[None, :] if gated else sig0
            variant = f"{entry_kind}{'+gate' if gated else ''}"
            ii, tt = np.nonzero(sig)
            order = np.lexsort((tt, ii))
            busy_until = {}  # one position per stock within each variant's own stream
            for i, t in zip(ii[order], tt[order]):
                if t < t_is or t + 1 >= m or not np.isfinite(O[i, t + 1]) or up_open[i, t + 1]:
                    continue
                if busy_until.get(i, -1) >= t:
                    continue
                k, px, reason = simulate(i, t, A)
                exit_cal = k if k is not None else m - 1
                busy_until[i] = exit_cal
                ret = px * (1 - sc[exit_cal]) / (O[i, t + 1] * (1 + BUY_COST)) - 1
                rows.append({"variant": variant, "code": codes[i], "signal": dates[t], "sig_cal": t + 1,
                             "exit_cal": exit_cal, "hold": max(exit_cal - (t + 1), 1), "ret": float(ret),
                             "reason": reason, "prio": -float(amt20[i, t])})
    tr = pd.DataFrame(rows)
    tr["period"] = np.where(tr.signal >= OOS_START, "OOS_2022+", "IS_2016-2021")
    print(f"[TRADES] {len(tr)}", flush=True)

    def per_trade(g):
        return pd.Series({"trades": len(g), "mean_pct": g.ret.mean() * 100, "median_pct": g.ret.median() * 100,
                          "win_pct": (g.ret > 0).mean() * 100, "avg_hold": g.hold.mean(),
                          "p05": g.ret.quantile(.05) * 100, "p95": g.ret.quantile(.95) * 100,
                          "open_end": int((g.reason == "open_end").sum())})
    variants = {v: tr.variant == v for v in tr.variant.unique()}
    trade_tbl = pd.concat({v: tr.loc[msk].groupby("period").apply(per_trade, include_groups=False)
                           for v, msk in variants.items()})
    reasons = pd.concat({v: tr.loc[msk].groupby("period").reason.value_counts(normalize=True).mul(100).unstack(fill_value=0)
                         for v, msk in variants.items()})
    years = pd.concat({v: tr.loc[msk].groupby(tr.signal.dt.year).ret.mean().mul(100) for v, msk in variants.items()}, axis=1)

    def period_bounds(p):
        lo = t_is if p.startswith("IS") else int(dates.searchsorted(OOS_START))
        hi = int(dates.searchsorted(OOS_START)) - 1 if p.startswith("IS") else m - 1
        return lo, hi
    port = []
    for v, msk in variants.items():
        for p in ("IS_2016-2021", "OOS_2022+"):
            lo, hi = period_bounds(p)
            g = tr.loc[msk & (tr.period == p)]
            res = slot_compound(g[["sig_cal", "code", "ret", "hold", "prio"]], slots=20, cal_end=hi)
            eq = pd.Series(res.equity_by_day).sort_index()
            mdd = float((eq / eq.cummax() - 1).min()) if len(eq) else np.nan
            yrs = (hi - lo + 1) / 252
            port.append({"variant": v, "period": p, "total_pct": res.total_ret * 100,
                         "annual_pct": ((1 + res.total_ret) ** (1 / yrs) - 1) * 100,
                         "mdd_realized_pct": mdd * 100, "filled": res.filled, "skipped": res.skipped_locked})
    for name, s in (("SSE", sse), ("EW_mean", ew)):
        for p in ("IS_2016-2021", "OOS_2022+"):
            lo, hi = period_bounds(p)
            x = s.iloc[lo:hi + 1]; yrs = len(x) / 252
            port.append({"variant": f"bench_{name}", "period": p, "total_pct": (x.iloc[-1] / x.iloc[0] - 1) * 100,
                         "annual_pct": ((x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1) * 100,
                         "mdd_realized_pct": float((x / x.cummax() - 1).min()) * 100})
    port = pd.DataFrame(port)
    gate_share = {p: float(gate[period_bounds(p)[0]:period_bounds(p)[1] + 1].mean() * 100) for p in ("IS_2016-2021", "OOS_2022+")}

    args.output.mkdir(parents=True, exist_ok=True)
    tr.to_csv(args.output / "trades.csv", index=False, encoding="utf-8-sig")
    trade_tbl.to_csv(args.output / "per_trade.csv", encoding="utf-8-sig")
    reasons.to_csv(args.output / "exit_reasons.csv", encoding="utf-8-sig")
    years.to_csv(args.output / "by_year.csv", encoding="utf-8-sig")
    port.to_csv(args.output / "portfolio.csv", index=False, encoding="utf-8-sig")
    (args.output / "audit.json").write_text(json.dumps({**audit, "gate_on_pct": gate_share}, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 220)
    print(trade_tbl.round(2).to_string()); print(reasons.round(1).to_string()); print(years.round(2).to_string())
    print(port.round(2).to_string(index=False)); print("gate on % of sessions:", {k: round(v, 1) for k, v in gate_share.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
