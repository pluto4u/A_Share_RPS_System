"""Fill-order robustness for pool-ice portfolio (research only).

With ~186 candidates/day vs 20 slots, the fill rule matters. Test: code asc
(current), code desc, 5 random seeds, liquidity-first, momentum-first.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound

panel = load_panel(Path("output/daily_research_cache_20260930"))
dates = pd.DatetimeIndex(panel.dates)
oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))
t = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                encoding="utf-8-sig")
t = t[(t.channel == "ice") & (t.variant == "fixed5")].copy()

close = panel.field("close").astype(float)
amount = panel.field("amount").astype(float)
code_row = {str(c): i for i, c in enumerate(panel.codes)}
t["i"] = t.code.map(code_row)
amt20 = pd.DataFrame(amount).T.rolling(20).mean().T.to_numpy()
mom20 = close / pd.DataFrame(close).T.rolling(20).mean().T.to_numpy() - 1
t["liq"] = amt20[t.i.to_numpy(), t.entry_cal.to_numpy()]
t["mom"] = mom20[t.i.to_numpy(), t.entry_cal.to_numpy()]

def run(df, label):
    tr = df.rename(columns={"entry_cal": "sig_cal"})[["sig_cal", "code", "ret", "hold", "prio"]]
    out = {}
    for period, (a, b) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9), "ALL": (0, 10**9)}.items():
        s = tr[(tr.sig_cal >= a) & (tr.sig_cal <= b)]
        r = slot_compound(s, slots=20)
        out[period] = round(r.total_ret * 100, 2)
    print(f"{label:22s} IS {out['IS']:+8.2f}  OOS {out['OOS']:+8.2f}  ALL {out['ALL']:+8.2f}")

t["prio"] = 0
run(t, "code_asc(现行)")
run(t.assign(prio=-t.code.str.slice(0, 6).astype(int)), "code_desc")
for seed in range(5):
    rng = np.random.default_rng(seed)
    run(t.assign(prio=rng.permutation(len(t))), f"random_seed{seed}")
run(t.assign(prio=t.groupby("entry_cal")["liq"].rank(ascending=False)), "liq_first")
run(t.assign(prio=t.groupby("entry_cal")["mom"].rank(ascending=False)), "mom_first")
run(t.assign(prio=t.groupby("entry_cal")["liq"].rank(ascending=True)), "liq_last")
