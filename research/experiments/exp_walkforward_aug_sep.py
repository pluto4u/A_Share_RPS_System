"""Walk-forward replay of the F combo, 2026-08-01..2026-09-30 (causal).

For every session T in the window: environment flags (ice gate / repair /
chase day) and the signals that fired AT T's close, using only data <= T
(all signals are trailing-window by construction). Outcome column is the
realized net return of that signal's own exit rule. No look-ahead in
signal generation; exits are the result, not the trigger.
Statistics only; no buy/sell instructions.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel

GATE = IceGateParams(advance_thr=0.22, min_streak=2, require_weakening=True)
T0, T1 = "2026-08-01", "2026-09-30"


def main() -> None:
    panel = load_panel(ROOT / "output/daily_research_cache_20260930")
    dates = pd.DatetimeIndex(panel.dates)
    codes = [str(c) for c in panel.codes]
    names = json.loads((ROOT / "output/monitor/code_names.json")
                       .read_text(encoding="utf-8"))

    sent = daily_sentiment(codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    gate = ice_gate_mask(sent, GATE)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    ice_ok = gate & ~hot
    surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    chase_day = (ice0.repair.to_numpy(dtype=bool) & (surge >= 1.05)
                 & (sent.zt_count.to_numpy() >= 20)
                 & (sent.max_streak.to_numpy() > 2))

    pool = pd.read_csv(ROOT / "output/pool_pullback_20261001/pool_trades.csv",
                       encoding="utf-8-sig")
    dual = pd.read_csv(ROOT / "output/dual_engine_loose_20261001/"
                       "dual_trades.csv", encoding="utf-8-sig")
    col = "entry_cal" if "entry_cal" in dual.columns else "sig_cal"

    picks = {
        "圈池冰点": pool[(pool.channel == "ice") & (pool.variant == "fixed5")]
        [["entry_cal", "code", "ret"]],
        "形态冰点": dual[(dual.engine == "ice") & (dual.variant == "fixed5")]
        [[col, "code", "ret"]].rename(columns={col: "entry_cal"}),
        "放量追高": dual[(dual.engine == "chase") & (dual.variant == "fixed3")]
        [[col, "code", "ret"]].rename(columns={col: "entry_cal"}),
    }
    sig = pd.concat([d.assign(engine=k) for k, d in picks.items()],
                    ignore_index=True)
    sig["date"] = dates[sig.entry_cal.to_numpy()]
    w = sig[(sig.date >= T0) & (sig.date <= T1)].copy()
    w["name"] = [names.get(c.split(".")[0], c) for c in w.code]

    a, b = int(dates.searchsorted(pd.Timestamp(T0))), \
        int(dates.searchsorted(pd.Timestamp(T1)))
    lines = [f"# 逐日走查 {T0} ~ {T1}（信号因果、无前视）", ""]
    for cal in range(a, b + 1):
        d = dates[cal]
        flags = []
        if ice_ok[cal]:
            flags.append("冰点闸门")
        if bool(ice0.repair.iloc[cal]):
            flags.append("修复")
        if chase_day[cal]:
            flags.append("追高分")
        day = w[w.entry_cal == cal]
        head = f"## {d.date()}  [{'|'.join(flags) or '无信号日'}]  " \
               f"上涨占比 {sent.advance_share.iloc[cal]:.0%}  " \
               f"涨停 {int(sent.zt_count.iloc[cal])} " \
               f"跌停 {int(sent.dt_count.iloc[cal])}"
        lines.append(head)
        if day.empty:
            lines.append("（无出手）")
        else:
            for _, r in day.iterrows():
                lines.append(f"- {r.engine} {r.code} {r['name']} "
                             f"→ {r.ret * 100:+.2f}%")
        lines.append("")

    summ = w.groupby("engine").ret.agg(
        n="count", mean_pct=lambda x: round(x.mean() * 100, 2),
        win_pct=lambda x: round((x > 0).mean() * 100, 1))
    out = ROOT / "output/monitor/walkforward_202608_202609.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DAYS] {b - a + 1} sessions, signals {len(w)}")
    print(summ.to_string())
    print(f"[OUT] {out}")
    print("\n信号日分布:")
    print(w.groupby(w.date.dt.date).size().to_string())


if __name__ == "__main__":
    main()
