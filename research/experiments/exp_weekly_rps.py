"""Weekly RPS-lead breakout selector, abstracted from six winner charts.

Setup (weekly bars, causal at Friday close):
  glue    : weekly MA10/MA20/MA58 max/min <= glue_tol, held >= 6 of last 10w
  rps_lead: RPS120 crossed above rps_cross within last 4 weeks, and was below
            rps_low sometime 8..24 weeks ago (rising from mid-range)
  volume  : mean weekly volume of last 4 weeks >= vol_expand x prior 16 weeks
  price   : close >= max(MA10w,MA20w,MA58w) and close / 52w-high >= near_high
  plus RPS250 >= rps250_min. First occurrence within 8 weeks only
  (TDX BARSSINCEN(sig,8)=0 equivalent).
Entry: next trading day OPEN after signal week (no look-ahead).
Exits compared: fixed 4/8/12 weekly closes, and weekly-MA10 break.
Window: entries 2024-10-01..2026-08-31 (in-sample, user's choice).
Statistics only, no buy/sell advice. Memory-lean: numpy float32.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT

T0, T1 = pd.Timestamp("2024-10-01"), pd.Timestamp("2026-08-31")
GLUE_MIN_WEEKS = 6
HIST_START = "2022-06-01"  # lookback headroom for MA58w / RPS250

GRID = [
    {"glue_tol": 0.20, "rps_cross": 80, "rps_low": 70, "vol_expand": 1.25,
     "near_high": 0.70, "rps250_min": 50},   # 基线(上一轮)
    {"glue_tol": 0.12, "rps_cross": 80, "rps_low": 70, "vol_expand": 1.25,
     "near_high": 0.70, "rps250_min": 50},   # 收紧粘合
    {"glue_tol": 0.12, "rps_cross": 90, "rps_low": 75, "vol_expand": 1.25,
     "near_high": 0.75, "rps250_min": 50},   # 收紧粘合+RPS
    {"glue_tol": 0.12, "rps_cross": 90, "rps_low": 75, "vol_expand": 1.50,
     "near_high": 0.75, "rps250_min": 70},   # 全面收紧
    {"glue_tol": 0.10, "rps_cross": 90, "rps_low": 80, "vol_expand": 1.50,
     "near_high": 0.80, "rps250_min": 80},   # 最严(贴图标准)
]


def pct_rank_col(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n < 100:
        return out
    order = np.argsort(x[ok], kind="stable")
    r = np.empty(n, dtype=np.float64)
    r[order] = np.arange(n)
    out[ok] = r / (n - 1) * 100.0
    return out


def rolling_mean(a: np.ndarray, w: int) -> np.ndarray:
    n, m = a.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    c = np.nancumsum(np.where(np.isfinite(a), a, 0.0), axis=1)
    cnt = np.cumsum(np.isfinite(a), axis=1)
    num = c[:, w - 1:] - np.hstack([np.zeros((n, 1)), c[:, :-w]])
    den = cnt[:, w - 1:] - np.hstack([np.zeros((n, 1)), cnt[:, :-w]])
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, w - 1:] = num / den
    out[:, w - 1:][den < w] = np.nan
    return out


def rolling_extreme(a: np.ndarray, w: int, fn) -> np.ndarray:
    n, m = a.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    for j in range(w - 1, m):
        win = a[:, j - w + 1:j + 1]
        ok = np.isfinite(win).all(axis=1)
        out[ok, j] = fn(win[ok], axis=1)
    return out


def build_weekly():
    z = np.load(ROOT / "output/daily_research_cache_20260930/panel.npz",
                mmap_mode="r")
    dates = pd.DatetimeIndex(z["dates"])
    keep = dates >= pd.Timestamp(HIST_START)
    dates = dates[keep]
    close = np.asarray(z["close"][:, keep], dtype=np.float32)
    open_ = np.asarray(z["open"][:, keep], dtype=np.float32)
    vol = np.asarray(z["vol"][:, keep], dtype=np.float32)
    codes = np.array([str(c) for c in z["codes"]])
    n, m = close.shape
    print(f"[PANEL] {n} stocks x {m} days, "
          f"{dates[0].date()}..{dates[-1].date()}")

    wk = dates.to_period("W-FRI")
    wk_uniq = wk.unique().sort_values()
    pos = {p: i for i, p in enumerate(wk_uniq)}
    wid = np.array([pos[p] for p in wk])
    nw = len(wk_uniq)
    last_day = np.full(nw, -1)
    for j, w in enumerate(wid):
        last_day[w] = j
    wdates = dates[last_day]
    wc = close[:, last_day]
    wv = np.zeros((n, nw), dtype=np.float64)
    np.add.at(wv, (slice(None), wid), np.where(np.isfinite(vol), vol, 0.0))

    ma10 = rolling_mean(wc, 10)
    ma20 = rolling_mean(wc, 20)
    ma58 = rolling_mean(wc, 58)
    ma_max = np.fmax(np.fmax(ma10, ma20), ma58)
    ma_min = np.fmin(np.fmin(ma10, ma20), ma58)
    hi52 = rolling_extreme(wc, 52, np.max)

    r120w = np.full((n, nw), np.nan)
    r250w = np.full((n, nw), np.nan)
    for w, j in enumerate(last_day):
        if j >= 250:
            r120w[:, w] = pct_rank_col(close[:, j] / close[:, j - 120] - 1)
            r250w[:, w] = pct_rank_col(close[:, j] / close[:, j - 250] - 1)
        elif j >= 120:
            r120w[:, w] = pct_rank_col(close[:, j] / close[:, j - 120] - 1)
    return (wc, wv, ma10, ma_max, ma_min, hi52, r120w, r250w, last_day,
            wdates, dates, close, open_, codes, n, nw, m)


def run_one(ctx, p) -> pd.DataFrame:
    (wc, wv, ma10, ma_max, ma_min, hi52, r120w, r250w, last_day,
     wdates, dates, close, open_, codes, n, nw, m) = ctx
    glue = (ma_max / ma_min - 1.0) <= p["glue_tol"]
    gc = np.cumsum(glue, axis=1)
    glue_cnt = gc.copy().astype(float)
    glue_cnt[:, 10:] = gc[:, 10:] - gc[:, :-10]
    vol_fast = rolling_mean(wv, 4)
    wv_shift = np.hstack([np.full((n, 4), np.nan), wv[:, :-4]])
    vol_slow = rolling_mean(wv_shift, 16)
    r120w_max4 = rolling_extreme(r120w, 4, np.max)
    r120_shift = np.hstack([np.full((n, 8), np.nan), r120w[:, :-8]])
    r120w_min_past = rolling_extreme(r120_shift, 17, np.min)  # 8..24 wks ago

    with np.errstate(divide="ignore", invalid="ignore"):
        sig = (
            (glue_cnt >= GLUE_MIN_WEEKS)
            & (r120w_max4 >= p["rps_cross"])
            & (r120w_min_past <= p["rps_low"])
            & (vol_fast / vol_slow >= p["vol_expand"])
            & (wc >= ma_max)
            & (wc / hi52 >= p["near_high"])
            & (r250w >= p["rps250_min"])
        )
    sig = np.isfinite(sig.astype(float)) & sig
    # BARSSINCEN(sig, 8) = 0: first occurrence within 8 weeks
    first8 = sig.copy()
    first8[:, 1:] = sig[:, 1:] & ~sig[:, :-1].copy()
    for back in range(2, 8):
        first8[:, back:] &= ~sig[:, :-back]
    sig = first8

    rows = []
    for wi in range(nw - 13):
        wd = wdates[wi]
        if wd < T0 or wd > T1:
            continue
        hits = np.where(sig[:, wi])[0]
        if len(hits) == 0:
            continue
        j_sig = last_day[wi]
        if j_sig + 1 >= m:
            continue
        e_day = j_sig + 1  # first trading day after signal Friday
        for c in hits:
            e_px = open_[c, e_day]
            if not np.isfinite(e_px) or e_px <= 0:
                continue
            rec = {"code": codes[c], "sig_week": str(wd.date()),
                   "entry_date": str(dates[e_day].date()),
                   "entry_px": float(e_px)}
            for hz in (4, 8, 12):
                if wi + hz < nw:
                    x_px = close[c, last_day[wi + hz]]
                    if np.isfinite(x_px):
                        rec[f"fwd{hz}w"] = float(x_px / e_px - 1)
            for k in range(wi + 1, min(wi + 26, nw)):
                if np.isfinite(ma10[c, k]) and wc[c, k] < ma10[c, k]:
                    x_px = close[c, last_day[k]]
                    if np.isfinite(x_px):
                        rec["ma10_ret"] = float(x_px / e_px - 1)
                        rec["ma10_weeks"] = k - wi
                    break
            rows.append(rec)
    return pd.DataFrame(rows)


def summarize(tag: str, res: pd.DataFrame) -> None:
    if len(res) < 20:
        print(f"{tag}: n={len(res)} (过少, 不统计)")
        return
    parts = [f"{tag}: n={len(res)}, {res.sig_week.nunique()}周"]
    for col in ("fwd4w", "fwd8w", "ma10_ret"):
        g = res[col].dropna()
        parts.append(f"{col} {g.mean() * 100:+.1f}%/"
                     f"win{(g > 0).mean() * 100:.0f}%/"
                     f"med{g.median() * 100:+.1f}%")
    print("  ".join(parts), flush=True)


def main() -> None:
    ctx = build_weekly()
    best = None
    for i, p in enumerate(GRID):
        res = run_one(ctx, p)
        tag = (f"[P{i}] glue={p['glue_tol']} rps>{p['rps_cross']} "
               f"low<{p['rps_low']} vol>={p['vol_expand']} "
               f"hi>={p['near_high']} r250>={p['rps250_min']}")
        summarize(tag, res)
        if i == len(GRID) - 1 and not res.empty:
            best = res
    if best is not None:
        out = ROOT / "output/monitor/weekly_rps_signals.csv"
        best.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[OUT] {out}  (最严参数组)")
        print(best.groupby("sig_week").size().to_string())


if __name__ == "__main__":
    main()
