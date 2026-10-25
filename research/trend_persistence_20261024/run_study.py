"""Which trend definitions persist in A-shares? Weekly cross-sectional excess-return study.

Pre-registered 2026-10-24 before any run (all data below has been seen before: descriptive only).
  Periods   design 2018-01..2021-12; stress 2022-01..2024-01; recent 2024-02..as-of
  Universe  non-ST (today's names: survivorship caveat), >=260 traded bars, 20d avg amount >= 5e7,
            traded on the evaluation day and the next session
  Return    buy next-session open, hold h in {20, 60, 120} sessions to the close (last close if suspended);
            excess = stock return - equal-weight mean of the eligible universe, same window
  Test      weekly series of the group-mean excess; Newey-West t with lag = h/5 weeks
  Variant   ex_hot: drop stocks whose 20-day return is in the universe's top 10% (short-term reversal)
  Selection the definition with the largest design-period NW t at h=60 is the pre-declared pick;
            every definition is still reported for every period.
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

PERIODS = {"design_2018-2021": ("2018-01-01", "2021-12-31"), "stress_2022-2024.01": ("2022-01-01", "2024-01-31"),
           "recent_2024.02-2026.10": ("2024-02-01", "2026-12-31")}
HORIZONS = (20, 60, 120)
MIN_BARS, MIN_AMOUNT = 260, 5e7

LABELS = {
    "D01_ma_slope": "长期均线：收盘>MA120且MA120较20日前上升，收盘>MA250",
    "D02_weekly": "周线：周收盘>20周线且20周线较4周前上升，周收盘>40周线",
    "D03_mom12_1": "动量12-1：t-250到t-20的涨幅>0",
    "D04_mom6_1": "动量6-1：t-125到t-20的涨幅>0",
    "D05_rps250": "相对强度：250日涨幅全市场前10%",
    "D06_rps120_mid": "腰部强度：120日涨幅分位在80-95",
    "D07_structure": "结构：近20日高点和低点都高于前20日",
    "D08_near_52wh": "接近52周高点：收盘≥52周最高价×0.9",
    "D09_donchian55": "唐奇安：近10日内收盘创55日新高",
    "D10_quality": "趋势质量：120日对数价回归斜率>0且R²≥0.7",
    "D11_adx": "ADX14>25且+DI>-DI",
    "C1_minervini": "Minervini趋势模板（8条，RS分位≥70）",
    "C2_dual_momentum": "双动量：12-1涨幅>0且250日相对强度≥70",
    "C3_smooth_momentum": "平滑动量：12-1涨幅>0且信息离散度在同类较低一半",
    "C4_leader_zone": "项目领涨区：RPS120≥90、周收盘>20周线、10周线>20周线、距52周高≥0.7",
    "C5_ma_structure": "D01长期均线 + D07结构抬高",
    "C6_quality_rs": "D10趋势质量 + 250日相对强度≥70",
    "C7_weekly_mid": "D02周线 + D06腰部强度",
}


def roll(a: np.ndarray, w: int, fn: str) -> np.ndarray:
    return getattr(pd.DataFrame(a).T.rolling(w, min_periods=w), fn)().T.to_numpy(np.float32)


def pct_rank(x: np.ndarray, ok: np.ndarray) -> np.ndarray:
    out = np.full(len(x), np.nan)
    m = ok & np.isfinite(x)
    if m.sum() > 1:
        out[m] = pd.Series(x[m]).rank(pct=True).to_numpy() * 100
    return out


def nw_t(x: np.ndarray, lag: int) -> float:
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return np.nan
    e = x - x.mean()
    v = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(v / n)) if v > 0 else np.nan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default="2026-10-23")
    ap.add_argument("--sessions", type=int, default=2400)
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    ap.add_argument("--benchmark", choices=("ew", "liquidity"), default="ew",
                    help="ew: universe equal-weight mean; liquidity: mean of the same prior-20d-amount quintile")
    args = ap.parse_args()
    panel, audit = build_recent_panel(args.tdx_root, pd.Timestamp(args.as_of), sessions=args.sessions)
    dates = pd.DatetimeIndex(panel.dates); codes = panel.codes.astype(str)
    O, H, L, C, AMT = (panel.field(f).astype(np.float32) for f in ("open", "high", "low", "close", "amount"))
    del panel
    n, m = C.shape
    print(f"[PANEL] {n} x {m}, {dates[0].date()}..{dates[-1].date()}", flush=True)
    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    st = np.array(["ST" in names.get(c, "").upper() or "退" in names.get(c, "") for c in codes])
    Cf = pd.DataFrame(C).T.ffill(limit=10).T.to_numpy(np.float32)  # last close through short suspensions
    bars = np.cumsum(np.isfinite(C), axis=1)
    MA = {w: roll(C, w, "mean") for w in (50, 120, 150, 200, 250)}
    amt20 = roll(AMT, 20, "mean")
    hi250, lo250 = roll(H, 250, "max"), roll(L, 250, "min")
    hi55_prev = np.full_like(H, np.nan); hi55_prev[:, 1:] = roll(H, 55, "max")[:, :-1]
    with np.errstate(invalid="ignore"):
        brk55 = (C >= hi55_prev).astype(float)
    brk55_10 = roll(brk55, 10, "max")
    del brk55, hi55_prev
    hi20, lo20 = roll(H, 20, "max"), roll(L, 20, "min")
    r1 = np.full_like(C, np.nan); r1[:, 1:] = C[:, 1:] / C[:, :-1] - 1
    pos = roll((r1 > 0).astype(np.float32), 230, "sum"); neg = roll((r1 < 0).astype(np.float32), 230, "sum")
    del r1
    # ADX(14), Wilder smoothing
    P = np.full_like(C, np.nan); P[:, 1:] = C[:, :-1]
    up = np.full_like(H, np.nan); up[:, 1:] = H[:, 1:] - H[:, :-1]
    dn = np.full_like(L, np.nan); dn[:, 1:] = L[:, :-1] - L[:, 1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.fmax(H - L, np.fmax(np.abs(H - P), np.abs(L - P)))
    wild = lambda a: pd.DataFrame(a).T.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().T.to_numpy()  # noqa: E731
    atr = wild(tr)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, mdi = 100 * wild(pdm) / atr, 100 * wild(mdm) / atr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = wild(dx)
    del P, up, dn, pdm, mdm, tr, atr, dx
    # weekly closes for the weekly templates
    wk = dates.to_period("W-FRI"); uniq = wk.unique().sort_values(); wid = np.searchsorted(uniq, wk)
    last_day = np.array([np.flatnonzero(wid == w)[-1] for w in range(len(uniq))])
    wc = Cf[:, last_day]
    ma10w, ma20w, ma40w = roll(wc, 10, "mean"), roll(wc, 20, "mean"), roll(wc, 40, "mean")
    hi52w_close = roll(wc, 52, "max")

    t_first = int(dates.searchsorted(pd.Timestamp(PERIODS["design_2018-2021"][0])))
    weeks = [w for w in range(len(uniq)) if last_day[w] >= t_first and last_day[w] + 1 < m]
    log_c = np.log(Cf).astype(np.float64)
    xs = np.arange(120, dtype=float); xs_c = xs - xs.mean(); sxx = (xs_c ** 2).sum()
    rows, members = [], []
    for w in weeks:
        t = last_day[w]
        ok = (~st) & (bars[:, t] >= MIN_BARS) & (amt20[:, t] >= MIN_AMOUNT) & np.isfinite(C[:, t]) & np.isfinite(O[:, t + 1])
        r250 = Cf[:, t] / Cf[:, t - 250] - 1; r120 = Cf[:, t] / Cf[:, t - 120] - 1; r20 = Cf[:, t] / Cf[:, t - 20] - 1
        rps250, rps120, rps20 = pct_rank(r250, ok), pct_rank(r120, ok), pct_rank(r20, ok)
        mom12 = Cf[:, t - 20] / Cf[:, t - 250] - 1; mom6 = Cf[:, t - 20] / Cf[:, t - 125] - 1
        seg = log_c[:, t - 119:t + 1]
        fin = np.isfinite(seg).all(axis=1)
        ycen = seg - seg.mean(axis=1, keepdims=True)
        slope = (ycen * xs_c).sum(axis=1) / sxx
        r2 = np.where(fin, (slope ** 2 * sxx) / np.maximum((ycen ** 2).sum(axis=1), 1e-12), np.nan)
        idisc = np.sign(mom12) * ((neg[:, t - 20] - pos[:, t - 20]) / 230)
        with np.errstate(invalid="ignore"):
            d = {
                "D01_ma_slope": (C[:, t] > MA[120][:, t]) & (MA[120][:, t] > MA[120][:, t - 20]) & (C[:, t] > MA[250][:, t]),
                "D02_weekly": (wc[:, w] > ma20w[:, w]) & (ma20w[:, w] > ma20w[:, w - 4]) & (wc[:, w] > ma40w[:, w]),
                "D03_mom12_1": mom12 > 0,
                "D04_mom6_1": mom6 > 0,
                "D05_rps250": rps250 >= 90,
                "D06_rps120_mid": (rps120 >= 80) & (rps120 <= 95),
                "D07_structure": (hi20[:, t] > hi20[:, t - 20]) & (lo20[:, t] > lo20[:, t - 20]),
                "D08_near_52wh": C[:, t] >= 0.9 * hi250[:, t],
                "D09_donchian55": brk55_10[:, t] > 0,
                "D10_quality": (slope > 0) & (r2 >= 0.7),
                "D11_adx": (adx[:, t] > 25) & (pdi[:, t] > mdi[:, t]),
            }
            mv = (C[:, t] > MA[150][:, t]) & (C[:, t] > MA[200][:, t]) & (MA[150][:, t] > MA[200][:, t]) \
                & (MA[200][:, t] > MA[200][:, t - 22]) & (MA[50][:, t] > MA[150][:, t]) & (C[:, t] > MA[50][:, t]) \
                & (C[:, t] >= 1.3 * lo250[:, t]) & (C[:, t] >= 0.75 * hi250[:, t]) & (rps250 >= 70)
            d["C1_minervini"] = mv
            d["C2_dual_momentum"] = (mom12 > 0) & (rps250 >= 70)
            base = ok & (mom12 > 0) & np.isfinite(idisc)
            med = np.nanmedian(idisc[base]) if base.any() else np.nan
            d["C3_smooth_momentum"] = base & (idisc <= med)
            d["C4_leader_zone"] = (rps120 >= 90) & (wc[:, w] > ma20w[:, w]) & (ma10w[:, w] > ma20w[:, w]) & (wc[:, w] >= 0.7 * hi52w_close[:, w])
            d["C5_ma_structure"] = d["D01_ma_slope"] & d["D07_structure"]
            d["C6_quality_rs"] = d["D10_quality"] & (rps250 >= 70)
            d["C7_weekly_mid"] = d["D02_weekly"] & d["D06_rps120_mid"]
        hot = rps20 >= 90
        entry = O[:, t + 1]
        liq_rank = pct_rank(amt20[:, t].astype(np.float64), ok)  # prior 20d amount, known at t
        quint = np.where(np.isfinite(liq_rank), np.minimum((liq_rank - 1e-9) // 20, 4), -1).astype(int)
        for h in HORIZONS:
            if t + h >= m:
                continue
            ret = Cf[:, t + h] / entry - 1
            valid = ok & np.isfinite(ret) & (quint >= 0)
            if valid.sum() < 300:
                continue
            if args.benchmark == "liquidity":
                bench = np.full(n, np.nan)
                for q in range(5):
                    g = valid & (quint == q)
                    bench[g] = ret[g].mean()
                ex = ret - bench
            else:
                ex = ret - ret[valid].mean()
            for k, flag in d.items():
                for variant, sel in (("all", valid & flag), ("ex_hot", valid & flag & ~hot)):
                    if sel.sum() < 20:
                        continue
                    rows.append({"date": dates[t], "def": k, "variant": variant, "h": h, "n": int(sel.sum()),
                                 "coverage_pct": sel.sum() / valid.sum() * 100, "excess_pct": float(ex[sel].mean() * 100),
                                 "hit_pct": float((ex[sel] > 0).mean() * 100),
                                 "avg_liq_quintile": float(quint[sel].mean() + 1)})
    res = pd.DataFrame(rows)
    def period_of(dt):
        for p, (a, b) in PERIODS.items():
            if pd.Timestamp(a) <= dt <= pd.Timestamp(b):
                return p
        return None
    res["period"] = res.date.map(period_of)
    summ = []
    for (k, v, h, p), g in res.groupby(["def", "variant", "h", "period"]):
        x = g.sort_values("date").excess_pct.to_numpy()
        summ.append({"def": k, "variant": v, "h": h, "period": p, "weeks": len(g), "avg_n": g.n.mean(),
                     "coverage_pct": g.coverage_pct.mean(), "excess_pct": x.mean(), "nw_t": nw_t(x, max(1, h // 5)),
                     "weeks_positive_pct": (x > 0).mean() * 100, "stock_hit_pct": g.hit_pct.mean(),
                     "avg_liq_quintile": g.avg_liq_quintile.mean()})
    summ = pd.DataFrame(summ)
    design = summ.loc[(summ.period == "design_2018-2021") & (summ.h == 60) & (summ.variant == "all")]
    pick = design.sort_values("nw_t", ascending=False).iloc[0]["def"]
    yearly = res.loc[(res.h == 60) & (res.variant == "all")].assign(year=lambda x: x.date.dt.year) \
        .pivot_table(index="def", columns="year", values="excess_pct", aggfunc="mean")
    args.output.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.output / "weekly.csv", index=False, encoding="utf-8-sig")
    summ.to_csv(args.output / "summary.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(args.output / "yearly_h60.csv", encoding="utf-8-sig")
    (args.output / "audit.json").write_text(json.dumps({**audit, "pre_declared_pick": pick, "labels": LABELS},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 250)
    for v in ("all", "ex_hot"):
        tab = summ.loc[(summ.h == 60) & (summ.variant == v)].pivot(index="def", columns="period", values=["excess_pct", "nw_t"])
        print(f"\n== h=60, variant={v}: excess % / NW t"); print(tab.round(2).to_string())
    for h in (20, 120):
        tab = summ.loc[(summ.h == h) & (summ.variant == "all")].pivot(index="def", columns="period", values=["excess_pct", "nw_t"])
        print(f"\n== h={h}, all"); print(tab.round(2).to_string())
    cov = summ.loc[(summ.h == 60) & (summ.variant == "all")].pivot(index="def", columns="period", values="coverage_pct")
    print("\n== coverage % (h=60)"); print(cov.round(1).to_string())
    liq = summ.loc[(summ.h == 60) & (summ.variant == "all")].pivot(index="def", columns="period", values="avg_liq_quintile")
    print("\n== avg liquidity quintile of flagged stocks (1=least liquid, 5=most; universe avg 3.0)"); print(liq.round(2).to_string())
    print("\n== yearly excess % (h=60, all)"); print(yearly.round(2).to_string())
    print("\nPRE-DECLARED PICK (largest design NW t at h=60):", pick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
