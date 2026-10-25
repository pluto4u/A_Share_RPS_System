"""Window polish experiments: 2024-09 -> 2026-09 (research only).

Frozen F-combo signals (params selected pre-2022; this window is OOS for them).
What we tune here is PORTFOLIO CONSTRUCTION only: slot count x within-day fill
rule. Labeled in-window diagnostic — candidates must win across rules, not
just one lucky ordering.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

BIG = 10_000_000  # engine priority dominates within-day rank


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    t0 = int(dates.searchsorted(pd.Timestamp("2024-09-01")))

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/dual_trades.csv",
                       encoding="utf-8-sig")

    def pk(df: pd.DataFrame, m: pd.Series, prio: int) -> pd.DataFrame:
        d = df[m].copy()
        d["eprio"] = prio
        return d[["entry_cal", "code", "ret", "hold", "eprio"]]

    df = pd.concat([
        pk(pool, (pool.channel == "ice") & (pool.variant == "fixed5"), 0),
        pk(dual, (dual.engine == "ice") & (dual.variant == "fixed5"), 1),
        pk(dual, (dual.engine == "chase") & (dual.variant == "fixed3"), 2),
    ], ignore_index=True)
    df = df[df.entry_cal >= t0].copy()

    close = panel.field("close").astype(float)
    amount = panel.field("amount").astype(float)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    df["i"] = df.code.map(code_row)
    amt20 = pd.DataFrame(amount).T.rolling(20).mean().T.to_numpy()
    mom20 = (close / pd.DataFrame(close).T.rolling(20).mean().T
             - 1).to_numpy()
    df["liq"] = amt20[df.i.to_numpy(), df.entry_cal.to_numpy()]
    df["mom"] = mom20[df.i.to_numpy(), df.entry_cal.to_numpy()]

    def with_rule(rule: str, seed: int = 0) -> pd.DataFrame:
        d = df.copy()
        g = d.groupby(["entry_cal", "eprio"])
        if rule == "code_asc":
            rank = d.code.str.slice(0, 6).astype(int)
        elif rule == "code_desc":
            rank = -d.code.str.slice(0, 6).astype(int)
        elif rule == "liq_first":
            rank = g["liq"].rank(ascending=False)
        elif rule == "liq_last":
            rank = g["liq"].rank(ascending=True)
        elif rule == "mom_first":
            rank = g["mom"].rank(ascending=False)
        elif rule == "mom_last":
            rank = g["mom"].rank(ascending=True)
        else:  # random
            rng = np.random.default_rng(seed)
            rank = pd.Series(rng.permutation(len(d)), index=d.index)
        d["prio"] = d.eprio * BIG + rank
        return d.rename(columns={"entry_cal": "sig_cal"})

    rules = (["code_asc", "code_desc", "liq_first", "liq_last",
              "mom_first", "mom_last"]
             + [f"random_s{s}" for s in range(5)])
    rows = []
    for rule in rules:
        seed = int(rule.split("s")[-1]) if rule.startswith("random") else 0
        d = with_rule(rule, seed)
        for slots in (10, 20, 30, 40):
            r = slot_compound(
                d[["sig_cal", "code", "ret", "hold", "prio"]], slots=slots)
            eq = pd.Series(r.equity_by_day).sort_index()
            eq.index = dates[eq.index.to_numpy()]
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            rows.append({
                "rule": rule, "slots": slots,
                "total_pct": round(r.total_ret * 100, 2),
                "cagr_pct": round(((eq.iloc[-1] / eq.iloc[0])
                                   ** (1 / yrs) - 1) * 100, 2),
                "maxdd_pct": round((eq / eq.cummax() - 1).min() * 100, 2),
                "filled": r.filled, "skipped": r.skipped_locked})
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "output/final_combo_20261001/window_polish.csv",
               index=False, encoding="utf-8-sig")
    print(out.pivot_table(index="rule", columns="slots",
                          values="cagr_pct").round(2).to_string())
    print()
    print(out.pivot_table(index="rule", columns="slots",
                          values="total_pct").round(2).to_string())
    print()
    print(out.pivot_table(index="rule", columns="slots",
                          values="maxdd_pct").round(2).to_string())


if __name__ == "__main__":
    main()
