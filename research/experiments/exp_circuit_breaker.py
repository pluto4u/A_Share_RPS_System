"""Regime circuit-breaker backtest for the F combo (research only).

Two kill regimes found in exp_regime_review:
  K1 grinding bear without leadership (2018): all engines negative together;
  K2 liquidity/style shock (2024-01/02): index fine, pool edge collapses.
Guardrails (all causal, day-t values only):
  G1 edge monitor : halt entries while the trailing mean of the last 30
                    CLOSED trades is < 0; resume when it recovers >= 0.
  G3 shock filter : no entries on days with dt_count >= 100 (mass limit-down)
                    or index 20-day return <= -8%.
Tests each guardrail and G1+G3 on the FULL period; reports total, maxdd and
the yearly results that must improve (2018, 2024) vs must not die (2022/23/25).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.icepoint import daily_sentiment
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

G1_WINDOW = 30
G3_DT = 100
G3_IDX20 = -0.08


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    dt_count = sent.dt_count.to_numpy()
    idx = pd.Series(np.asarray(panel.index_close, dtype=float), index=dates)
    idx20 = (idx / idx.shift(20) - 1).to_numpy()

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")
    col = "entry_cal" if "entry_cal" in dual.columns else "sig_cal"

    def pk(df: pd.DataFrame, m: pd.Series, prio: int) -> pd.DataFrame:
        d = df[m].copy()
        d["prio"] = prio
        return d.rename(columns={col: "sig_cal"})[
            ["sig_cal", "code", "ret", "hold", "prio"]]

    base = pd.concat([
        pk(pool, (pool.channel == "ice") & (pool.variant == "fixed5"), 0),
        pk(dual, (dual.engine == "ice") & (dual.variant == "fixed5"), 1),
        pk(dual, (dual.engine == "chase") & (dual.variant == "fixed3"), 2),
    ], ignore_index=True)

    # G1: trailing mean of last 30 closed trades, causal by exit day
    closed = base.assign(exit_cal=base.sig_cal + base.hold)
    closed = closed.sort_values("exit_cal")
    ex = closed.exit_cal.to_numpy()
    rv = closed.ret.to_numpy()
    cs = np.cumsum(np.concatenate([[0.0], rv]))

    def g1_ok(day: int) -> bool:
        j = int(np.searchsorted(ex, day, side="right"))  # closed by `day`
        if j < G1_WINDOW:
            return True
        return (cs[j] - cs[j - G1_WINDOW]) / G1_WINDOW >= 0.0

    g3_bad = (dt_count >= G3_DT) | (idx20 <= G3_IDX20)

    variants = {
        "F baseline": base,
        "F+G1": base[[g1_ok(int(d)) for d in base.sig_cal]],
        "F+G3": base[[not g3_bad[int(d)] for d in base.sig_cal]],
        "F+G1+G3": base[[g1_ok(int(d)) and not g3_bad[int(d)]
                         for d in base.sig_cal]],
    }
    print("variant      total%   maxdd%   " +
          "  ".join(f"{y}" for y in (2018, 2022, 2024, 2025)))
    for name, d in variants.items():
        r = slot_compound(d, slots=20)
        eq = pd.Series(r.equity_by_day).sort_index()
        eq.index = dates[eq.index.to_numpy()]
        dd = (eq / eq.cummax() - 1).min() * 100
        yr = eq.groupby(eq.index.year).last()
        prev = pd.Series([eq.iloc[0], *yr.iloc[:-1]], index=yr.index)
        yearly = (yr / prev - 1) * 100
        cells = "  ".join(f"{yearly.get(y, np.nan):+6.1f}"
                          for y in (2018, 2022, 2024, 2025))
        print(f"{name:12s} {r.total_ret * 100:+7.2f}  {dd:+7.2f}   {cells}"
              f"   filled={r.filled}")


if __name__ == "__main__":
    main()
