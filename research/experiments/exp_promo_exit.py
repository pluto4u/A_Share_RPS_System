"""Promotion exit: ice-day entries hold past day 5 while trend persists (research only).

Structure: buy at ice-day tail as before. At fixed5 expiry, if the stock has
NOT closed below MA20 since entry (proxied: the parallel ma20_break sim exited
after day 5), the position is PROMOTED to trend-hold (trail_s04_t08 or
ma20_break exit realized instead). Variant P2 additionally requires the index
to be in trend regime on day 5. Slot frees at day 5 regardless (promotion
moves the position to a separate ledger conceptually; here we test both
shared-slot and separate accounting).

Reuses frozen pool_trades.csv parallel sims (same entry, 4 exits, costs and
T+1 already inside ret). Statistics only; no buy/sell instructions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.slot_portfolio import slot_compound

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"


def metrics(eq: pd.Series) -> tuple[float, float, float]:
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    mdd = float((eq / eq.cummax() - 1).min())
    return total * 100, ((1 + total) ** (1 / years) - 1) * 100, mdd * 100


def combo_curve(trades: pd.DataFrame, slots: int, a: int, b: int,
                dates: pd.DatetimeIndex) -> pd.Series:
    s = trades[(trades.sig_cal >= a) & (trades.sig_cal <= b)]
    r = slot_compound(s[["sig_cal", "code", "ret", "hold", "prio"]],
                      slots=slots, cal_end=b)
    eq = pd.Series(r.equity_by_day).sort_index()
    full = pd.Series(1.0, index=dates[a:b + 1])
    if len(eq):
        eq.index = dates[eq.index.to_numpy()]
        full.update(eq)
    return full.ffill()


def main() -> int:
    z = np.load(SNAPSHOT / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    idx = pd.Series(z["index_close"].astype(float), index=dates).ffill()
    n_days = len(dates)
    w0 = int(dates.searchsorted(pd.Timestamp("2024-09-01")))
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))

    ma20i = idx.rolling(20).mean()
    ma60i = idx.rolling(60).mean()
    trend = ((idx > ma20i) & (ma20i >= ma20i.shift(5)) & (idx > ma60i))
    trend_by_cal = pd.Series(trend.to_numpy(), index=np.arange(n_days))

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    ice = pool[pool.channel == "ice"]
    piv: dict[str, pd.DataFrame] = {}
    for v in ("fixed5", "ma20_break", "trail_s04_t08"):
        piv[v] = (ice[ice.variant == v]
                  .set_index(["code", "entry_cal"])[["ret", "hold", "exit_cal"]]
                  .rename(columns={"ret": f"ret_{v}", "hold": f"hold_{v}",
                                   "exit_cal": f"exit_{v}"}))
    m = piv["fixed5"].join([piv["ma20_break"], piv["trail_s04_t08"]]).dropna()
    m = m.reset_index()
    print(f"配对样本（圈池冰点入场，四退出平行模拟齐全）: {len(m)} 笔")

    # promotable: no close below MA20 from entry+1 through day 5
    m["promotable"] = m.exit_ma20_break > m.exit_fixed5
    m["regime_d5"] = (m.entry_cal + 5).map(trend_by_cal).fillna(False)
    for col in ("ret", "hold"):
        m[f"{col}_P1"] = np.where(m.promotable, m[f"{col}_trail_s04_t08"],
                                  m[f"{col}_fixed5"])
        m[f"{col}_P2"] = np.where(m.promotable & m.regime_d5,
                                  m[f"{col}_trail_s04_t08"], m[f"{col}_fixed5"])
        m[f"{col}_P3"] = np.where(m.promotable & m.regime_d5,
                                  m[f"{col}_ma20_break"], m[f"{col}_fixed5"])

    print("\n=== 每笔口径（圈池冰点通道）===")
    print(f"{'exit':<22}{'期':>6}{'n':>7}{'mean%':>8}{'win%':>7}{'med_hold':>9}{'晋升率':>7}")
    for pname, (a, b) in {"全期": (0, 10**9), "OOS22+": (oos0, 10**9),
                          "窗口24-09+": (w0, 10**9)}.items():
        g = m[(m.entry_cal >= a) & (m.entry_cal <= b)]
        pr = g.promotable.mean() * 100
        for v in ("fixed5", "P1", "P2", "P3"):
            print(f"{v:<22}{pname:>6}{len(g):>7}{g[f'ret_{v}'].mean() * 100:>8.2f}"
                  f"{(g[f'ret_{v}'] > 0).mean() * 100:>7.1f}"
                  f"{g[f'hold_{v}'].median():>9.0f}{pr:>6.1f}%")
        pr2 = (g.promotable & g.regime_d5).mean() * 100
        print(f"  (P2 实际晋升率 {pr2:.1f}%)")

    # ---- combo: swap pool-ice fixed5 for promo variants, keep pattern + chase
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")

    def prio(df: pd.DataFrame, p: int) -> pd.DataFrame:
        d = df.copy()
        d["prio"] = p
        return d

    rest = pd.concat([
        prio(dual[(dual.engine == "ice") & (dual.variant == "fixed5")], 1)
        .rename(columns={"entry_cal": "sig_cal"}),
        prio(dual[(dual.engine == "chase") & (dual.variant == "fixed3")], 2)
        .rename(columns={"entry_cal": "sig_cal"}),
    ], ignore_index=True)[["sig_cal", "code", "ret", "hold", "prio"]]

    print("\n=== 组合口径（20槽共享，圈池通道换成晋升退出）===")
    print(f"{'组合':<30}{'期':>10}{'累计%':>9}{'年化%':>8}{'回撤%':>8}")
    results = {}
    for v in ("fixed5", "P1", "P2", "P3"):
        lead = prio(m.rename(columns={"entry_cal": "sig_cal"}), 0)[
            ["sig_cal", "code", f"ret_{v}", f"hold_{v}", "prio"]]
        lead.columns = ["sig_cal", "code", "ret", "hold", "prio"]
        allt = pd.concat([lead, rest], ignore_index=True)
        for pname, (a, b) in {"全期10.7年": (0, 10**9),
                              "窗口24-09+": (w0, 10**9)}.items():
            eq = combo_curve(allt, 20, a, b if b < 10**9 else n_days - 1, dates)
            t, c, d = metrics(eq)
            results[(v, pname)] = eq
            print(f"{'F(圈池' + v + ')':<30}{pname:>10}{t:>9.1f}{c:>8.1f}{d:>8.1f}")

    # yearly for P2 full period
    eq = results[("P2", "全期10.7年")]
    yr = eq.groupby(eq.index.year).last()
    prev = pd.Series([eq.iloc[0], *yr.iloc[:-1]], index=yr.index)
    print("\n[P2 全期] 逐年 %:")
    print(((yr / prev - 1) * 100).round(1).to_string())

    # 2026 top autopsy on window curve
    top = idx.iloc[w0:].idxmax()
    for v in ("fixed5", "P2"):
        eqw = results[(v, "窗口24-09+")]
        after = eqw[eqw.index >= top]
        dd = float((after / after.cummax() - 1).min()) * 100
        print(f"[{v} 窗口] 顶({top.date()})后最大回撤 {dd:.1f}%")

    out = ROOT / "output/promo_exit_20261004"
    out.mkdir(parents=True, exist_ok=True)
    m.to_csv(out / "paired_trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({f"{v}|{p}": eq for (v, p), eq in results.items()}
                 ).to_csv(out / "curves.csv", encoding="utf-8-sig")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
