"""Diagnose breakout-sleeve slot-fill divergence (research only).

Per-trade edge is positive (+1.1%~+2.1%/trade) but the 10-slot combo is
negative. Hypothesis: signal clustering + code-ascending fill order picks a
bad subset. Test: (a) 20 random fill seeds; (b) liquidity-ranked fill
(20d amount, the user's real compression rule). Statistics only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.slot_portfolio import slot_compound

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"
SEEDS = 20


def curve_ret(trades: pd.DataFrame, slots: int, w0: int, n: int,
              dates: pd.DatetimeIndex) -> tuple[float, float]:
    s = trades[(trades.entry_cal >= w0) & (trades.entry_cal <= n - 1)]
    t = s.rename(columns={"entry_cal": "sig_cal"})
    cols = ["sig_cal", "code", "ret", "hold"] + (["prio"] if "prio" in t else [])
    r = slot_compound(t[cols], slots=slots, cal_end=n - 1)
    eq = pd.Series(r.equity_by_day).sort_index()
    if not len(eq):
        return 0.0, 0.0
    total = r.total_ret * 100
    mdd = float((eq / eq.cummax() - 1).min()) * 100
    return total, mdd


def main() -> int:
    z = np.load(SNAPSHOT / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    n = len(dates)
    w0 = int(dates.searchsorted(pd.Timestamp("2024-09-01")))

    sim = pd.read_csv(ROOT / "output/breakout_sleeve_20261004/breakout_trades.csv",
                      encoding="utf-8-sig")

    # signals per day clustering
    spd = sim[sim.variant == "fixed10"].groupby("entry_cal").size()
    print(f"信号日分布: 中位 {spd.median():.0f} 只/天, p90 {spd.quantile(0.9):.0f}, "
          f"max {spd.max()}, >10只的天数占比 {(spd > 10).mean() * 100:.0f}%")

    # 20d amount for liquidity ranking
    amt = pd.DataFrame(z["amount"].astype(float), index=[str(c) for c in z["codes"]])
    amt20 = amt.T.rolling(20).mean().T
    amt20.columns = np.arange(n)
    long_amt = amt20.stack().rename("amt20")
    long_amt.index.names = ["code", "entry_cal"]

    rng = np.random.default_rng(7)
    for v in ("fixed5", "fixed10", "ma20_break", "trail_s04_t08"):
        g = sim[sim.variant == v].copy()
        # (a) code-asc baseline
        base, base_dd = curve_ret(g, 10, w0, n, dates)
        # (b) random seeds
        rs = []
        for k in range(SEEDS):
            gg = g.copy()
            gg["prio"] = rng.permutation(len(gg))
            rs.append(curve_ret(gg, 10, w0, n, dates)[0])
        rs = np.array(rs)
        # (c) liquidity-ranked (higher 20d amount fills first)
        gl = g.join(long_amt, on=["code", "entry_cal"])
        gl = gl.dropna(subset=["amt20"])
        gl["prio"] = gl.groupby("entry_cal")["amt20"].rank(ascending=False)
        liq, liq_dd = curve_ret(gl, 10, w0, n, dates)
        print(f"{v:<14} code序 {base:+7.1f}%/dd{base_dd:6.1f}%  "
              f"随机20种子 中位{np.median(rs):+7.1f}% [{rs.min():+6.1f},{rs.max():+6.1f}]  "
              f"流动性优先 {liq:+7.1f}%/dd{liq_dd:6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
