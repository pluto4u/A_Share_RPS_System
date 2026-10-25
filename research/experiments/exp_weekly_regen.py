"""All-weeks regeneration of weekly candidates in ONE pass (research only).

Same rule and scoring as weekly_pullback_scan.scan() but computes the heavy
weekly arrays once and emits candidates for every week in the study window
(2024-10 -> 2026-08), then attaches next-open entry and 4/8/12w close exits
from the frozen panel, splits by daily-MA60 environment, and tests score
compression (top3/5/8) inside weak weeks. Statistics only; polluted window.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from research.experiments.exp_pullback import st_exclusions
from research.experiments.exp_weekly_rps import pct_rank_col, rolling_mean
from run_pattern_regime_backtest import ROOT
from weekly_pullback_scan import WEIGHTS, rank_pct_small

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"


def main() -> int:
    z = np.load(SNAPSHOT / "panel.npz", mmap_mode="r")
    dates = pd.DatetimeIndex(z["dates"])
    codes = np.array([str(c) for c in z["codes"]])
    n = len(codes)
    close = np.asarray(z["close"], dtype=np.float32)
    low = np.asarray(z["low"], dtype=np.float32)
    vol = np.asarray(z["vol"], dtype=np.float32)
    amount = np.asarray(z["amount"], dtype=np.float32)
    open_ = np.asarray(z["open"], dtype=np.float64)

    excl = st_exclusions(codes, close, high=np.asarray(z["high"],
                       dtype=np.float32), low=low)

    # ---- weekly aggregation (identical to weekly_pullback_scan)
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
    wl = np.full((n, nw), np.inf, dtype=np.float32)
    np.minimum.at(wl, (slice(None), wid),
                  np.where(np.isfinite(low), low, np.inf))
    wl[np.isinf(wl)] = np.nan
    wv = np.zeros((n, nw), dtype=np.float64)
    np.add.at(wv, (slice(None), wid), np.where(np.isfinite(vol), vol, 0.0))

    ma10 = rolling_mean(wc, 10)
    ma20 = rolling_mean(wc, 20)
    hi52 = np.full_like(wc, np.nan)
    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        hi52[ok, w] = win[ok].max(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0
    above = np.isfinite(ma20) & (wc > ma20)
    runlen = np.zeros_like(wc, dtype=float)
    for w in range(1, nw):
        runlen[:, w] = np.where(above[:, w], runlen[:, w - 1] + 1, 0)

    close_x = close.copy()
    close_x[excl] = np.nan
    r120w = np.full((n, nw), np.nan)
    for w, j in enumerate(last_day):
        if j >= 120:
            with np.errstate(divide="ignore", invalid="ignore"):
                r120w[:, w] = pct_rank_col(close_x[:, j]
                                           / close_x[:, j - 120] - 1)
    hi90 = (r120w >= 90).astype(float)
    cnt = np.cumsum(hi90, axis=1)
    peg = cnt.copy()
    peg[:, 4:] = cnt[:, 4:] - cnt[:, :-4]
    prev = np.hstack([np.full((n, 1), np.nan), wv[:, :-1]])
    c4 = np.nancumsum(np.where(np.isfinite(prev), prev, 0.0), axis=1)
    k4 = np.cumsum(np.isfinite(prev), axis=1)
    prev4 = np.full_like(wv, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev4[:, 4:] = (c4[:, 4:] - c4[:, :-4]) / (k4[:, 4:] - k4[:, :-4])

    with np.errstate(divide="ignore", invalid="ignore"):
        sig = ((r120w >= 90) & (peg >= 3)
               & (wc > ma20) & (ma10 > ma20)
               & (wl <= ma20 * 1.03) & (wc >= ma20 * 0.97)
               & (wc / hi52 >= 0.70) & (ret52 <= 1.5))
    sig = np.isfinite(sig.astype(float)) & sig & ~excl[:, None]
    first = sig.copy()
    first[:, 1:] = sig[:, 1:] & ~sig[:, :-1].copy()
    for back in range(2, 4):
        first[:, back:] &= ~sig[:, :-back]
    print("[SCAN] weekly arrays done", flush=True)

    # ---- emit candidates for every week in the study window
    w0 = int(np.searchsorted(wdates, pd.Timestamp("2024-10-01")))
    w1 = int(np.searchsorted(wdates, pd.Timestamp("2026-08-31")))
    amt20 = pd.DataFrame(amount).T.rolling(20).mean().T.to_numpy()

    rows = []
    for wi in range(w0, w1 + 1):
        hits = np.where(first[:, wi])[0]
        if len(hits) == 0:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            dims = {"rps120": r120w[hits, wi],
                    "close_recover": wc[hits, wi] / ma20[hits, wi] - 1,
                    "low_touch": wl[hits, wi] / ma20[hits, wi] - 1,
                    "vol_dry": wv[hits, wi] / prev4[hits, wi],
                    "ret52": ret52[hits, wi],
                    "trend_age": runlen[hits, wi],
                    "ma_align": ma10[hits, wi] / ma20[hits, wi] - 1}
        score = np.zeros(len(hits))
        for d, wgt in WEIGHTS.items():
            r = rank_pct_small(dims[d])
            score += wgt * np.where(np.isfinite(r), r, 0.5)
        d0 = last_day[wi]
        if d0 + 1 >= len(dates):
            continue
        for k, s in enumerate(hits):
            px0 = open_[s, d0 + 1]
            if not np.isfinite(px0) or px0 <= 0:
                continue
            rec = {"code": codes[s], "sig_week": str(wdates[wi].date()),
                   "score": round(float(score[k]), 3),
                   "rps120": round(float(r120w[s, wi]), 1),
                   "amt20_亿": round(float(amt20[s, d0]) / 1e8, 2)}
            for tag, off in (("fwd4w", 21), ("fwd8w", 41), ("fwd12w", 61)):
                j = d0 + off
                px = close[s, j] if j < len(dates) else np.nan
                rec[tag] = (px / px0 - 1) if np.isfinite(px) else np.nan
            rows.append(rec)
    cand = pd.DataFrame(rows)
    out = ROOT / "output/weekly_regen_20261004"
    out.mkdir(parents=True, exist_ok=True)
    cand.to_csv(out / "weekly_candidates.csv", index=False, encoding="utf-8-sig")
    print(f"候选 {len(cand)} 行 / {cand.sig_week.nunique()} 周 -> {out}",
          flush=True)

    # ---- environment split + compression
    idx = pd.Series(np.asarray(z["index_close"], dtype=float),
                    index=dates).ffill()
    weak60 = (idx < idx.rolling(60).mean())
    weak_map = pd.Series(weak60.to_numpy(), index=dates.date.astype(str))
    cand["weak"] = cand.sig_week.map(weak_map)
    cand = cand.dropna(subset=["fwd8w", "weak"])
    cand["weak"] = cand.weak.astype(bool)
    cand["rank"] = cand.groupby("sig_week")["score"].rank(ascending=False)

    print("\n=== 环境分档 × 候选 fwd8w（重建口径）===")
    for name, m in (("弱(指数<日MA60)", cand.weak), ("强", ~cand.weak)):
        g = cand[m]
        print(f"{name:<16} n={len(g):>4} ({g.sig_week.nunique():>2}周)  "
              f"fwd8w {g.fwd8w.mean() * 100:+6.2f}%  "
              f"胜 {(g.fwd8w > 0).mean() * 100:5.1f}%  "
              f"中位 {g.fwd8w.median() * 100:+6.2f}%")

    wk_c = cand[cand.weak]
    print("\n=== 弱周内打分压缩（fwd8w）===")
    for N in (3, 5, 8):
        g = wk_c[wk_c["rank"] <= N]
        print(f"  top{N}: n={len(g):>3}  均值 {g.fwd8w.mean() * 100:+6.2f}%  "
              f"胜率 {(g.fwd8w > 0).mean() * 100:5.1f}%  "
              f"中位 {g.fwd8w.median() * 100:+6.2f}%")
    g = wk_c[wk_c["rank"] > 8]
    if len(g):
        print(f"  其余 : n={len(g):>3}  均值 {g.fwd8w.mean() * 100:+6.2f}%  "
              f"胜率 {(g.fwd8w > 0).mean() * 100:5.1f}%")

    print("\n=== 弱周逐周 top5 fwd8w% ===")
    t = wk_c[wk_c["rank"] <= 5].groupby("sig_week").fwd8w.agg(["count", "mean"])
    print(t.assign(mean=(t["mean"] * 100).round(1)).to_string())

    st = cand[~cand.weak]
    print(f"\n[对照] 强环境全池 fwd8w {st.fwd8w.mean() * 100:+.2f}% / "
          f"{(st.fwd8w > 0).mean() * 100:.1f}% (n={len(st)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
