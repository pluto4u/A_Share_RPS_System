"""Exploratory study: 3-day hold with a -3% stop on common short-term entries.

Data 2019-01 .. as-of are ALREADY SEEN history: results describe, they do not validate.
Entries (signal at day t close, buy at t+1 open):
  first_board  sealed limit-up today, not yesterday
  second_board second consecutive sealed limit-up
  top5d        5-day return in the top 1% of traded stocks (any close)
Unfillable: t+1 opens at the limit-up price (cannot be bought at the open) -> skipped.
Exits (A-share T+1: nothing can be sold on the entry day t+1):
  hold3        sell at the close of t+3 (3 sessions in position)
  stop3_intra  from t+2: open <= entry*0.97 -> sell at that open (gap through);
               else low <= entry*0.97 -> sell at entry*0.97; otherwise hold3
  stop3_close  any close (t+1..t+2) <= entry*0.97 -> sell next open; otherwise hold3
A one-price limit-down day cannot be sold; a close at limit-down cannot be sold at the close;
both roll to the next sellable open. Costs: trend_start.engine BUY_COST / sell_cost.
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
from run_daily_rps import names_from_tnf  # noqa: E402
from trend_start.daily_board import build_recent_panel  # noqa: E402
from trend_start.engine import BUY_COST, LIMIT_TOL, sell_cost  # noqa: E402
from trend_start.icepoint import limit_matrix  # noqa: E402

STOP = 0.03
HOLD = 3
MIN_HISTORY = 60
TOP_PCT = 99.0


def sell_px_rolled(k, o, h, lo, c, down_lock, lim_dn_close, n_days, at_close):
    """First sellable price at bar k (close if at_close, else open), rolling forward over locks."""
    while k < n_days:
        if not np.isfinite(o[k]):
            k += 1; at_close = False; continue
        if at_close:
            if not lim_dn_close[k]:
                return k, c[k]
        elif not down_lock[k]:
            return k, o[k]
        k += 1; at_close = False
    return None, np.nan


def simulate(i, t, O, H, L, C, down_lock, lim_dn_close, m):
    o, h, lo, c = O[i], H[i], L[i], C[i]
    e = t + 1
    entry = o[e]
    stop = entry * (1 - STOP)
    last = t + HOLD  # close of the third session in position
    out = {}
    # hold3
    k, px = sell_px_rolled(last, o, h, lo, c, down_lock[i], lim_dn_close[i], m, True)
    out["hold3"] = (k, px, False)
    # intraday stop from t+2
    res = None
    for d in range(e + 1, last + 1):
        if not np.isfinite(o[d]):
            continue
        if o[d] <= stop:
            if down_lock[i][d]:
                continue  # locked limit-down: cannot sell today
            res = (d, o[d], True); break
        if lo[d] <= stop:
            res = (d, stop, True); break
    out["stop3_intra"] = res or out["hold3"]
    # close-based stop: close <= stop on t+1 or t+2 -> next open
    res = None
    for d in range(e, last):
        if np.isfinite(c[d]) and c[d] <= stop:
            k, px = sell_px_rolled(d + 1, o, h, lo, c, down_lock[i], lim_dn_close[i], m, False)
            res = (k, px, True); break
    out["stop3_close"] = res or out["hold3"]
    return entry, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default="2026-10-23")
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    args = ap.parse_args()
    as_of = pd.Timestamp(args.as_of)
    cal_len = 2050  # ~8.4 years of sessions: warm-up before --start included
    panel, audit = build_recent_panel(args.tdx_root, as_of, sessions=cal_len)
    dates = pd.DatetimeIndex(panel.dates)
    codes = panel.codes.astype(str)
    O, H, L, C = (panel.field(f).astype(np.float64) for f in ("open", "high", "low", "close"))
    n, m = C.shape
    P = np.full_like(C, np.nan); P[:, 1:] = C[:, :-1]
    lim = limit_matrix(panel.codes, panel.dates) - LIMIT_TOL
    with np.errstate(invalid="ignore", divide="ignore"):
        chg = C / P - 1
        sealed = chg >= lim
        lim_dn_close = chg <= -lim
        down_lock = lim_dn_close & (H <= L)          # one-price limit-down
        up_open_lock = (O / P - 1) >= lim             # opens at limit-up: not buyable at the open
    streak = np.zeros_like(C)
    for t in range(1, m):
        streak[:, t] = np.where(sealed[:, t], streak[:, t - 1] + 1, 0)
    hist = np.cumsum(np.isfinite(C), axis=1)
    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    st = np.array(["ST" in names.get(c, "").upper() or "退" in names.get(c, "") for c in codes])
    with np.errstate(invalid="ignore", divide="ignore"):
        ret5 = C / np.roll(C, 5, axis=1) - 1
    ret5[:, :5] = np.nan
    r5pct = pd.DataFrame(ret5).rank(axis=0, pct=True).to_numpy() * 100

    t0 = int(dates.searchsorted(pd.Timestamp(args.start)))
    base_ok = (~st[:, None]) & (hist >= MIN_HISTORY) & np.isfinite(C)
    kinds = {"first_board": sealed & (streak == 1), "second_board": sealed & (streak == 2),
             "top5d": r5pct >= TOP_PCT}
    rows = []
    sc = sell_cost(panel.dates)
    for kind, sig in kinds.items():
        ii, tt = np.nonzero(sig & base_ok)
        keep = (tt >= t0) & (tt + HOLD + 1 < m)
        for i, t in zip(ii[keep], tt[keep]):
            e = t + 1
            if not np.isfinite(O[i, e]) or up_open_lock[i, e]:
                continue
            entry, outs = simulate(i, t, O, H, L, C, down_lock, lim_dn_close, m)
            for variant, (k, px, stopped) in outs.items():
                if k is None:
                    continue
                ret = px * (1 - sc[k]) / (entry * (1 + BUY_COST)) - 1
                rows.append({"kind": kind, "variant": variant, "code": codes[i], "signal": dates[t],
                             "year": dates[t].year, "ret": ret, "stopped": stopped, "exit_day": int(k - e + 1)})
    trades = pd.DataFrame(rows)
    # benchmark: every eligible stock bought at t+1 open, sold at t+3 close (no locks/stops)
    with np.errstate(invalid="ignore", divide="ignore"):
        bench = np.full(m, np.nan)
        for t in range(t0, m - HOLD - 1):
            ok = base_ok[:, t] & np.isfinite(O[:, t + 1]) & np.isfinite(C[:, t + HOLD])
            r = C[ok, t + HOLD] * (1 - sc[t + HOLD]) / (O[ok, t + 1] * (1 + BUY_COST)) - 1
            bench[t] = np.nanmean(r)
    bench_s = pd.Series(bench, index=dates).dropna()

    def summary(g: pd.DataFrame) -> pd.Series:
        day = g.groupby("signal").ret.mean()
        return pd.Series({"trades": len(g), "signal_days": g.signal.nunique(),
                          "mean_pct": g.ret.mean() * 100, "median_pct": g.ret.median() * 100,
                          "win_pct": (g.ret > 0).mean() * 100,
                          "day_weighted_mean_pct": day.mean() * 100,
                          "bench_same_days_pct": bench_s.reindex(day.index).mean() * 100,
                          "stopped_pct": g.stopped.mean() * 100,
                          "stopped_mean_pct": g.loc[g.stopped, "ret"].mean() * 100 if g.stopped.any() else np.nan,
                          "worse_than_-5_pct": (g.ret < -0.05).mean() * 100,
                          "p05_pct": g.ret.quantile(.05) * 100, "p95_pct": g.ret.quantile(.95) * 100})

    trades["period"] = np.where(trades.signal < pd.Timestamp("2022-01-01"), "2019-2021", "2022-2026")
    overall = trades.groupby(["kind", "variant"]).apply(summary, include_groups=False)
    by_period = trades.groupby(["kind", "variant", "period"]).apply(summary, include_groups=False)
    by_year = trades.pivot_table(index=["kind", "variant"], columns="year", values="ret", aggfunc="mean") * 100
    args.output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.output / "trades.csv", index=False, encoding="utf-8-sig")
    overall.to_csv(args.output / "overall.csv", encoding="utf-8-sig")
    by_period.to_csv(args.output / "by_period.csv", encoding="utf-8-sig")
    by_year.to_csv(args.output / "by_year.csv", encoding="utf-8-sig")
    (args.output / "audit.json").write_text(json.dumps({**audit, "start": args.start, "bench_mean_pct": float(bench_s.mean() * 100)},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 220)
    print(overall.round(2).to_string())
    print(by_period[["trades", "mean_pct", "day_weighted_mean_pct", "bench_same_days_pct", "win_pct", "stopped_pct"]].round(2).to_string())
    print(by_year.round(2).to_string())
    print("benchmark all-stock 3d mean %:", round(bench_s.mean() * 100, 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
