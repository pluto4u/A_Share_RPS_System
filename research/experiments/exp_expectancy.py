"""Expectancy test: does weekly-RPS + pullback have positive expectation?

The honest version of the question, beyond per-trade means:
  1. per-WEEK equal-weight portfolio net returns (pool = U3 + early-stage
     gate, ST excluded), costs deducted (BUY_COST / sell_cost per exit day)
  2. time-series t-stat across signal weeks (8w overlap inflates it;
     reported alongside a non-overlap subsample every 8th week)
  3. top-5-by-score variant (what a 3-5-stock manual trader would hold)
  4. pooled expectancy decomposition: p_win, avg_win, avg_loss, E
  5. worst week / worst trade (tail risk)

Window is contaminated history: this is rule-shaping evidence, and only
the forward walk (weekly_pullback_scan, live since 2026-09-30) can prove
the expectation out of sample. Statistics only, no buy/sell advice.
"""
from __future__ import annotations

import numpy as np
from research.experiments.exp_pullback import T0, T1, build_ctx
from trend_start.engine import BUY_COST, sell_cost

WEIGHTS = {"close_recover": -0.23, "rps120": -0.21, "low_touch": -0.14,
           "vol_dry": -0.11, "ret52": -0.10, "trend_age": 0.09,
           "ma_align": -0.08}


def rank01(x: np.ndarray) -> np.ndarray:
    out = np.full(len(x), 0.5)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n >= 5:
        order = np.argsort(x[ok], kind="stable")
        r = np.empty(n)
        r[order] = np.arange(n)
        out[ok] = r / (n - 1)
    return out


def main() -> None:
    ctx = build_ctx()
    wc, wl, wv, r = ctx["wc"], ctx["wl"], ctx["wv"], ctx["r120w"]
    ma10, ma20, hi52 = ctx["ma10"], ctx["ma20"], ctx["hi52"]
    nw, m, dates = ctx["nw"], ctx["m"], ctx["dates"]
    sc = sell_cost(dates)

    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0
    above = np.isfinite(ma20) & (wc > ma20)
    runlen = np.zeros_like(wc, dtype=float)
    for w in range(1, nw):
        runlen[:, w] = np.where(above[:, w], runlen[:, w - 1] + 1, 0)
    prev = np.hstack([np.full((wv.shape[0], 1), np.nan), wv[:, :-1]])
    c4 = np.nancumsum(np.where(np.isfinite(prev), prev, 0.0), axis=1)
    k4 = np.cumsum(np.isfinite(prev), axis=1)
    prev4 = np.full_like(wv, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev4[:, 4:] = (c4[:, 4:] - c4[:, :-4]) / (k4[:, 4:] - k4[:, :-4])

    base = ((r >= 90) & (wc > ma20) & (ma10 > ma20)
            & (wl <= ma20 * 1.03) & (wc >= ma20 * 0.97)
            & (wc / hi52 >= 0.70) & (ret52 <= 1.5) & ~ctx["excl"][:, None])
    base = np.isfinite(base.astype(float)) & base

    weekly_port, weekly_top5, pooled = [], [], []
    for wi in range(nw - 9):
        wd = ctx["wdates"][wi]
        if wd < T0 or wd > T1:
            continue
        j = ctx["last_day"][wi]
        if j + 1 >= m:
            continue
        x_j = ctx["last_day"][wi + 8]
        e = ctx["open_"][:, j + 1]
        x = ctx["close"][:, x_j]
        with np.errstate(divide="ignore", invalid="ignore"):
            net = x * (1 - sc[x_j]) / (e * (1 + BUY_COST)) - 1
        pool = base[:, wi] & np.isfinite(net)
        idx = np.where(pool)[0]
        if len(idx) == 0:
            continue
        weekly_port.append(float(np.mean(net[idx])))
        pooled.append(net[idx])
        dims = {"rps120": r[idx, wi],
                "close_recover": wc[idx, wi] / ma20[idx, wi] - 1,
                "low_touch": wl[idx, wi] / ma20[idx, wi] - 1,
                "vol_dry": wv[idx, wi] / prev4[idx, wi],
                "ret52": ret52[idx, wi], "trend_age": runlen[idx, wi],
                "ma_align": ma10[idx, wi] / ma20[idx, wi] - 1}
        score = np.zeros(len(idx))
        for d, wgt in WEIGHTS.items():
            score += wgt * rank01(dims[d])
        top = idx[np.argsort(-score)[:5]]
        weekly_top5.append(float(np.mean(net[top])))

    wp = np.array(weekly_port)
    wt = np.array(weekly_top5)
    allr = np.concatenate(pooled)
    wins, losses = allr[allr > 0], allr[allr <= 0]

    print(f"信号周数: {len(wp)}, 池内总交易: {len(allr)}")
    print("\n== 每周等权组合（扣成本, 8周持有） ==")
    print(f"全池: mean {wp.mean() * 100:+.2f}%  med {np.median(wp) * 100:+.2f}%"
          f"  正收益周占比 {(wp > 0).mean() * 100:.0f}%  "
          f"最差周 {wp.min() * 100:+.2f}%")
    t_naive = wp.mean() / (wp.std() / np.sqrt(len(wp)))
    sub = wp[::8]  # 非重叠子样本
    t_sub = (sub.mean() / (sub.std() / np.sqrt(len(sub)))
             if len(sub) > 3 and sub.std() > 0 else float("nan"))
    print(f"t值: 朴素 {t_naive:.2f} (8周重叠, 虚高), "
          f"每8周取1周非重叠 n={len(sub)}: {t_sub:.2f}, "
          f"子样本均值 {sub.mean() * 100:+.2f}%")
    print(f"打分Top5: mean {wt.mean() * 100:+.2f}%  "
          f"med {np.median(wt) * 100:+.2f}%  "
          f"正收益周占比 {(wt > 0).mean() * 100:.0f}%  "
          f"最差周 {wt.min() * 100:+.2f}%")

    print("\n== 单笔期望分解（扣成本） ==")
    print(f"p_win {len(wins) / len(allr) * 100:.1f}%  "
          f"avg_win {wins.mean() * 100:+.2f}%  "
          f"avg_loss {losses.mean() * 100:+.2f}%  "
          f"E {allr.mean() * 100:+.2f}%  med {np.median(allr) * 100:+.2f}%")
    print(f"盈亏比 {wins.mean() / abs(losses.mean()):.2f}  "
          f"最差单笔 {allr.min() * 100:+.1f}%  最好单笔 {allr.max() * 100:+.1f}%")

    ann = (1 + wp.mean()) ** (52 / 8) - 1
    ann5 = (1 + wt.mean()) ** (52 / 8) - 1
    print(f"\n满铺年化期望（每周满仓当周池, 资本滚动复用假设）: "
          f"全池 {ann * 100:+.1f}%  Top5 {ann5 * 100:+.1f}%")


if __name__ == "__main__":
    main()
