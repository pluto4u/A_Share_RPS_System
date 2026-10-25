"""Small-account operability check (research only).

The F combo fires 100+ signals on clustered ice days; a human can hold 3-10
positions, not 20-40. Honest model of hand-picking = random draw from the
day's signal set. Measure outcome dispersion:
  slots {3,5,10,20} x fill rules {code_asc, liq_first, mom_first,
  20 random seeds} on two windows: 2026-08..09 (the replay window) and
  2024-09..2026-09 (the focus window).
If random-seed spread swamps the mean, the two-month number is not operable
without a selection layer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

BIG = 10_000_000


def build_trades(panel) -> pd.DataFrame:
    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/"
                       "dual_trades.csv", encoding="utf-8-sig")

    def pk(df: pd.DataFrame, m: pd.Series, prio: int) -> pd.DataFrame:
        d = df[m].copy()
        d["eprio"] = prio
        return d[["entry_cal", "code", "ret", "hold", "eprio"]]

    df = pd.concat([
        pk(pool, (pool.channel == "ice") & (pool.variant == "fixed5"), 0),
        pk(dual, (dual.engine == "ice") & (dual.variant == "fixed5"), 1),
        pk(dual, (dual.engine == "chase") & (dual.variant == "fixed3"), 2),
    ], ignore_index=True)
    close = panel.field("close").astype(float)
    amount = panel.field("amount").astype(float)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    df["i"] = df.code.map(code_row)
    amt20 = pd.DataFrame(amount).T.rolling(20).mean().T.to_numpy()
    mom20 = (close / pd.DataFrame(close).T.rolling(20).mean().T
             - 1).to_numpy()
    df["liq"] = amt20[df.i.to_numpy(), df.entry_cal.to_numpy()]
    df["mom"] = mom20[df.i.to_numpy(), df.entry_cal.to_numpy()]
    return df


def run(df: pd.DataFrame, rule: str, seed: int, slots: int) -> float:
    d = df.copy()
    g = d.groupby(["entry_cal", "eprio"])
    if rule == "code_asc":
        rank = d.code.str.slice(0, 6).astype(int)
    elif rule == "liq_first":
        rank = g["liq"].rank(ascending=False)
    elif rule == "mom_first":
        rank = g["mom"].rank(ascending=False)
    else:
        rng = np.random.default_rng(seed)
        rank = pd.Series(rng.permutation(len(d)), index=d.index)
    d["prio"] = d.eprio * BIG + rank
    r = slot_compound(d.rename(columns={"entry_cal": "sig_cal"})[
        ["sig_cal", "code", "ret", "hold", "prio"]], slots=slots)
    return r.total_ret * 100


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    df = build_trades(panel)
    for label, start in (("2026-08~09", "2026-08-01"),
                         ("2024-09~2026-09", "2024-09-01")):
        t0 = int(dates.searchsorted(pd.Timestamp(start)))
        w = df[df.entry_cal >= t0]
        print(f"\n=== {label}（{len(w)} 个信号）===")
        for slots in (3, 5, 10, 20):
            rnd = [run(w, "random", s, slots) for s in range(20)]
            det = {r: run(w, r, 0, slots)
                   for r in ("code_asc", "liq_first", "mom_first")}
            q = np.percentile(rnd, [10, 50, 90])
            print(f"slots={slots:2d} 随机中位数 {q[1]:+6.2f}% "
                  f"[{q[0]:+6.2f} ~ {q[2]:+6.2f}]  |  "
                  f"code_asc {det['code_asc']:+6.2f}  "
                  f"liq_first {det['liq_first']:+6.2f}  "
                  f"mom_first {det['mom_first']:+6.2f}")


if __name__ == "__main__":
    main()
