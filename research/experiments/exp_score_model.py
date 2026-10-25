"""Multi-dimension scoring model for the leader-pullback strategy.

Instead of hard AND-filters, each dimension is scored and weighted by its
measured contribution (per-week cross-sectional rank IC against fwd8w,
entry at next-day open). Composite score deciles are then tested.

Universe mask (leader zone): RPS120>=90, close>MA20w, MA10w>MA20w,
close/52w-high>=0.70. Dimensions computed within mask each week.

Window 2024-10-01..2026-08-31 is CONTAMINATED history (used for rule
formation); results are rule-shaping evidence, not out-of-sample proof.
Statistics only, no buy/sell advice.
"""
from __future__ import annotations

import numpy as np
from research.experiments.exp_pullback import T0, T1, build_ctx

DIMS = ["rps120", "rps_peg", "low_touch", "close_recover", "ret52",
        "trend_age", "near_high", "vol_dry", "ma_align"]


def rank_pct(x: np.ndarray) -> np.ndarray:
    """Cross-sectional 0..1 rank of a 1-D array, NaN-preserving."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n < 30:
        return out
    order = np.argsort(x[ok], kind="stable")
    r = np.empty(n)
    r[order] = np.arange(n)
    out[ok] = r / (n - 1)
    return out


def main() -> None:
    ctx = build_ctx()
    wc, wl, wv, r = ctx["wc"], ctx["wl"], ctx["wv"], ctx["r120w"]
    ma10, ma20, hi52 = ctx["ma10"], ctx["ma20"], ctx["hi52"]
    nw, m = ctx["nw"], ctx["m"]

    # 52-week return
    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0
    # trend age (weeks above MA20w)
    above = np.isfinite(ma20) & (wc > ma20)
    runlen = np.zeros_like(wc, dtype=float)
    for w in range(1, nw):
        runlen[:, w] = np.where(above[:, w], runlen[:, w - 1] + 1, 0)
    # rps peg count in last 4 weeks
    hi90 = (r >= 90).astype(float)
    cnt = np.cumsum(hi90, axis=1)
    peg = cnt.copy()
    peg[:, 4:] = cnt[:, 4:] - cnt[:, :-4]
    # volume dry ratio
    prev = np.hstack([np.full((wv.shape[0], 1), np.nan), wv[:, :-1]])
    prev4 = np.full_like(wv, np.nan)
    c = np.nancumsum(np.where(np.isfinite(prev), prev, 0.0), axis=1)
    k = np.cumsum(np.isfinite(prev), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev4[:, 4:] = (c[:, 4:] - c[:, :-4]) / (k[:, 4:] - k[:, :-4])
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_dry = wv / prev4
        low_touch = wl / ma20 - 1.0
        close_recover = wc / ma20 - 1.0
        near_high = wc / hi52
        ma_align = ma10 / ma20 - 1.0

    # env_strength is index-level (constant within a week): cross-sectional
    # rank IC is undefined for it; environment is judged at week level
    # separately (see exp_pullback_baseline U1), so it is excluded here.

    dim_mat = {"rps120": r, "rps_peg": peg, "low_touch": low_touch,
               "close_recover": close_recover, "ret52": ret52,
               "trend_age": runlen, "near_high": near_high,
               "vol_dry": vol_dry, "ma_align": ma_align}

    mask = ((r >= 90) & (wc > ma20) & (ma10 > ma20)
            & (near_high >= 0.70) & ~ctx["excl"][:, None])

    weeks = [w for w in range(nw - 9)
             if T0 <= ctx["wdates"][w] <= T1
             and ctx["last_day"][w] + 1 < m]

    # per-week rank ICs
    ics: dict[str, list[float]] = {d: [] for d in DIMS}
    base_ret: list[float] = []
    score_rows = []
    for w in weeks:
        j = ctx["last_day"][w]
        e = ctx["open_"][:, j + 1]
        x = ctx["close"][:, ctx["last_day"][w + 8]]
        with np.errstate(divide="ignore", invalid="ignore"):
            f8 = x / e - 1
        sel = mask[:, w] & np.isfinite(f8)
        if sel.sum() < 30:
            continue
        base_ret.append(float(np.nanmean(f8[sel])))
        fr = rank_pct(f8[sel])
        for d in DIMS:
            v = dim_mat[d][sel, w]
            ok = np.isfinite(v) & np.isfinite(fr)
            if ok.sum() < 30:
                continue
            vr = rank_pct(v[ok])
            ics[d].append(float(np.corrcoef(vr, fr[ok])[0, 1]))
        score_rows.append((w, sel, f8))

    print(f"[WEEKS] {len(score_rows)} 周, 领涨区平均8周收益 "
          f"{np.mean(base_ret) * 100:+.2f}%")
    print(f"\n{'维度':14s} {'IC均值':>8s} {'ICIR':>6s} {'t值':>6s} {'方向'}")
    weights = {}
    for d in DIMS:
        a = np.array(ics[d])
        mu, sd = a.mean(), a.std()
        t = mu / (sd / np.sqrt(len(a))) if sd > 0 else 0.0
        weights[d] = mu
        note = "正贡献" if mu > 0.005 else ("负贡献" if mu < -0.005 else "≈0 剔除")
        print(f"{d:14s} {mu:+8.4f} {mu / sd if sd > 0 else 0:6.2f} "
              f"{t:+6.2f} {note}")

    # composite score: sum of signed-IC-weighted ranks (drop |IC|<=0.005)
    use = {d: w_ for d, w_ in weights.items() if abs(w_) > 0.005}
    norm = sum(abs(v) for v in use.values()) or 1.0
    dec = {q: [] for q in range(10)}
    for w, sel, f8 in score_rows:
        sc = np.zeros(int(sel.sum()))
        for d, wgt in use.items():
            v = dim_mat[d][sel, w]
            ok = np.isfinite(v)
            vr = np.full(len(v), 0.5)
            if ok.sum() >= 30:
                vr[ok] = rank_pct(v[ok])
            sc += (wgt / norm) * vr
        fr = f8[sel]
        ok = np.isfinite(fr) & np.isfinite(sc)
        s, g = sc[ok], fr[ok]
        if len(s) < 30:
            continue
        qs = np.quantile(s, np.linspace(0, 1, 11))
        for q in range(10):
            lo, hi_ = qs[q], qs[q + 1]
            bucket = (s >= lo) & (s <= hi_ if q == 9 else s < hi_)
            dec[q].append(float(np.nanmean(g[bucket])))

    print("\n复合分数十分位（每周横截面内分位, 平均8周收益）:")
    for q in range(10):
        print(f"  Q{q + 1:2d}: {np.mean(dec[q]) * 100:+6.2f}%")
    print("\n权重（已归一化）: "
          + ", ".join(f"{d}={wgt / norm:+.2f}" for d, wgt in use.items()))


if __name__ == "__main__":
    main()
