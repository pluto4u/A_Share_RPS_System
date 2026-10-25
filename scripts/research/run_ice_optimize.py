"""Ice-point tail-buy optimization study: entry grid, exit variants, sectors, scenarios.

Pre-declared protocol:
  - Entry gate grid selected on IS (2016-09..2021-12) by excess fwd2 with >=100
    IS trades; OOS (2022-01..2026-09) is validation, never re-selected.
  - The full grid is reported; multiple-comparison risk is disclosed.
  - Output ends at statistics; no buy/sell instructions.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT, trade_row, verify_snapshot
from trend_start.engine import BUY_COST, block_bootstrap_mean, sell_cost
from trend_start.ice_opt import (
    EXIT_VARIANTS,
    IceGateParams,
    ice_gate_mask,
    load_tdx_industry,
    simulate_exit_variants,
)
from trend_start.icepoint import classify_ice, daily_sentiment, limit_matrix
from trend_start.panel import load_panel
from trend_start.pattern_backtest import control_fwd_returns

IS_END = pd.Timestamp("2021-12-31")
MIN_IS_TRADES = 100

GRID = [
    IceGateParams(advance_thr=a, min_streak=s, require_weakening=w, resonance=r)
    for a in (0.15, 0.18, 0.22, 0.26)
    for s in (1, 2)
    for w in (True, False)
    for r in ("none", "any")
]


def excess_cols(g: pd.DataFrame, control: dict[int, np.ndarray],
                dates: np.ndarray, horizons: tuple[int, ...] = (2, 5)) -> dict[str, float]:
    out: dict[str, float] = {}
    for hz in horizons:
        x = g[f"fwd{hz}"].to_numpy() - control[hz][g.sig_cal.to_numpy()]
        ok = np.isfinite(x)
        if ok.sum() < 5:
            continue
        month = pd.Series(
            pd.DatetimeIndex(dates[g.sig_cal.to_numpy()[ok]]).to_period("M").astype(str))
        lo, hi = block_bootstrap_mean(pd.Series(x[ok]), month)
        out[f"excess_fwd{hz}"] = float(np.nanmean(x) * 100)
        out[f"ci_lo_fwd{hz}"] = lo * 100
        out[f"ci_hi_fwd{hz}"] = hi * 100
    return out


def eval_subset(name: str, g: pd.DataFrame, control: dict[int, np.ndarray],
                dates: np.ndarray) -> list[dict]:
    rows = []
    for period in ("IS", "OOS"):
        gp = g.loc[g.period == period]
        row = {"spec": name, "period": period, **trade_row(gp)}
        if len(gp) >= 20:
            row.update(excess_cols(gp, control, dates))
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--trades", type=Path,
                    default=ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--gate-override", default=None,
                    help="skip IS selection; use this grid spec name for exits/scenarios")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    dates = panel.dates
    args.output.mkdir(parents=True, exist_ok=False)

    print("[SENT] computing sentiment frame", flush=True)
    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    control = control_fwd_returns(panel.field("close"), dates)

    trades = pd.read_csv(args.trades)
    trades["d"] = pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()])
    trades["period"] = np.where(trades.d <= IS_END, "IS", "OOS")
    quiet = trades.loc[(trades.kind == "quiet") & (trades.timing == "tail")
                       & (trades.exit_set == "h2")].copy()

    # ---------- Task 1: entry gate grid ----------
    print("[GRID] entry gate grid", flush=True)
    grid_rows: list[dict] = []
    masks: dict[str, np.ndarray] = {}
    for p in GRID:
        name = f"a{p.advance_thr}_s{p.min_streak}_w{int(p.require_weakening)}_{p.resonance}"
        mask = ice_gate_mask(sent, p)
        masks[name] = mask
        g = quiet.loc[mask[quiet.sig_cal.to_numpy()]]
        for r in eval_subset(name, g, control, dates):
            r.update({"advance_thr": p.advance_thr, "min_streak": p.min_streak,
                      "require_weakening": p.require_weakening, "resonance": p.resonance,
                      "gate_days": int(mask.sum())})
            grid_rows.append(r)
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(args.output / "grid_entry.csv", index=False, encoding="utf-8-sig")
    is_grid = grid.loc[(grid.period == "IS") & (grid.trades >= MIN_IS_TRADES)]
    if args.gate_override is not None:
        best_name = args.gate_override
        print(f"[SELECT] gate override: {best_name}", flush=True)
    else:
        best = is_grid.sort_values(["excess_fwd2", "trades"],
                                   ascending=False).iloc[0]
        best_name = str(best.spec)
        print(f"[SELECT] IS best gate: {best_name} (excess_fwd2={best.excess_fwd2:.3f}%)",
              flush=True)

    # ---------- Task 2: exit variants on the selected gate ----------
    print("[EXIT] exit-variant simulation", flush=True)
    sel_mask = masks[best_name]
    sel = quiet.loc[sel_mask[quiet.sig_cal.to_numpy()]].copy()
    repair_days = ice0.repair.to_numpy(dtype=bool)
    climax_days = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    by_code = {c: g for c, g in sel.groupby("code")}
    exit_rows: list[dict] = []
    sc = sell_cost(dates)
    from trend_start.drop_base_retest import PatternConfig, pattern_entry_events

    pcfg = PatternConfig.model_validate(
        json.loads((ROOT / "config/drop_base_retest.json").read_text(encoding="utf-8")))
    for k, (code, g) in enumerate(by_code.items()):
        if k % 300 == 0:
            print(f"[EXIT] {k}/{len(by_code)}", flush=True)
        i = code_row[code]
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(float) for f in panel.data})
        try:
            entries = pattern_entry_events(d, pcfg)
        except ValueError:
            continue
        levels = {b: (pv, fl) for b, kd, pv, fl in entries if kd == "quiet"}
        o = d.open.to_numpy()
        h = d.high.to_numpy()
        lo = d.low.to_numpy()
        c = d.close.to_numpy()
        cal = cols.astype(np.int64)
        dts = dates[cols]
        for r in g.itertuples(index=False):
            if int(r.sig_bar) not in levels:
                continue
            pivot, floor = levels[int(r.sig_bar)]
            out = simulate_exit_variants(code, int(r.entry_bar), pivot, floor,
                                         o, h, lo, c, cal, dts, repair_days, climax_days)
            for v, (bar, px, reason) in out.items():
                ret = px * (1 - sc[int(cal[min(bar, len(cal) - 1)])]) / (
                    c[int(r.entry_bar)] * (1 + BUY_COST)) - 1
                exit_rows.append({"code": code, "sig_cal": int(r.sig_cal),
                                  "period": r.period, "variant": v,
                                  "exit_reason": reason, "hold": bar - int(r.entry_bar),
                                  "ret": float(ret)})
    ev = pd.DataFrame(exit_rows)
    ev.to_csv(args.output / "exit_trades.csv", index=False, encoding="utf-8-sig")
    exit_stats = []
    for v in EXIT_VARIANTS:
        for period in ("IS", "OOS"):
            g = ev.loc[(ev.variant == v) & (ev.period == period)]
            r = g.ret.dropna()
            exit_stats.append({"variant": v, "period": period, "trades": len(g),
                               "win_rate_pct": float((r > 0).mean() * 100) if len(r) else np.nan,
                               "mean_ret_pct": float(r.mean() * 100) if len(r) else np.nan,
                               "median_ret_pct": float(r.median() * 100) if len(r) else np.nan,
                               "median_hold": float(g.hold.median()) if len(g) else np.nan})
    exit_table = pd.DataFrame(exit_stats)
    exit_table.to_csv(args.output / "exit_variants.csv", index=False, encoding="utf-8-sig")

    # ---------- Task 3: sector direction ----------
    print("[SECTOR] industry mapping and resonance", flush=True)
    ind_map, _ = load_tdx_industry(str(args.tdx_root), level=2)
    quiet["industry"] = [ind_map.get(str(c)[:6], "未知") for c in quiet.code]
    gated = quiet.loc[sel_mask[quiet.sig_cal.to_numpy()]].copy()
    ind_rows = []
    for period in ("IS", "OOS"):
        g = gated.loc[gated.period == period]
        for ind, gi in g.groupby("industry"):
            if len(gi) < 15:
                continue
            ind_rows.append({"industry": ind, "period": period, "trades": len(gi),
                             "win_rate_pct": float((gi.ret > 0).mean() * 100),
                             "mean_ret_pct": float(gi.ret.mean() * 100),
                             "fwd2_mean_pct": float(gi.fwd2.mean() * 100),
                             "fwd5_mean_pct": float(gi.fwd5.mean() * 100)})
    pd.DataFrame(ind_rows).to_csv(args.output / "sector_stats.csv",
                                  index=False, encoding="utf-8-sig")
    # sector resonance: sector limit-up count on the signal day
    lim = limit_matrix(panel.codes, dates) - 0.003
    cmat = panel.field("close").astype(np.float64)
    prev = np.full_like(cmat, np.nan)
    prev[:, 1:] = cmat[:, :-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        chg = cmat / prev - 1
    sealed = np.isfinite(chg) & (chg >= lim)
    ind_of = np.array([ind_map.get(str(c)[:6], "未知") for c in panel.codes])
    ind_list = sorted(set(ind_of))
    ind_idx = {v: i for i, v in enumerate(ind_list)}
    sec_zt = np.zeros((len(ind_list), len(dates)))
    cnt = np.zeros((len(ind_list), len(dates)))
    row_ind = np.array([ind_idx[v] for v in ind_of])
    for j in range(len(ind_list)):
        m = row_ind == j
        sec_zt[j] = sealed[m].sum(axis=0)
        cnt[j] = np.isfinite(cmat[m]).sum(axis=0)
    gated["sec_zt_sig"] = [sec_zt[ind_idx[ind_map.get(str(cd)[:6], "未知")], sc_]
                           for cd, sc_ in zip(gated.code, gated.sig_cal, strict=True)]
    res_rows = eval_subset("sector_zt>=2_on_sig",
                           gated.loc[gated.sec_zt_sig >= 2], control, dates)
    res_rows += eval_subset("sector_zt<2_on_sig",
                            gated.loc[gated.sec_zt_sig < 2], control, dates)
    pd.DataFrame(res_rows).to_csv(args.output / "sector_resonance.csv",
                                  index=False, encoding="utf-8-sig")

    # ---------- Task 4: additional scenarios ----------
    print("[SCEN] scenario backtests", flush=True)
    scen_rows: list[dict] = []
    idx = pd.Series(panel.index_close).ffill().to_numpy()
    idx_chg = np.r_[np.nan, idx[1:] / idx[:-1] - 1]
    adv = sent.advance_share.to_numpy()
    base_ice = masks[best_name]

    # S1 护盘假冰点: index flat/up while breadth collapses
    fake = base_ice & (idx_chg > -0.002)
    scen_rows += eval_subset("S1_fake_ice_guarded", quiet.loc[fake[quiet.sig_cal.to_numpy()]],
                             control, dates)
    scen_rows += eval_subset("S1_true_ice",
                             quiet.loc[(base_ice & ~fake)[quiet.sig_cal.to_numpy()]],
                             control, dates)
    # S2 过热高潮日
    hot = adv >= 0.85
    scen_rows += eval_subset("S2_overheated_day", quiet.loc[hot[quiet.sig_cal.to_numpy()]],
                             control, dates)
    scen_rows += eval_subset("S2_normal_day", quiet.loc[(~hot)[quiet.sig_cal.to_numpy()]],
                             control, dates)
    # S3 节前最后交易日 (next session >=4 calendar days later)
    gap_next = np.r_[np.diff(pd.DatetimeIndex(dates).asi8) / 86400e9, np.nan]
    prehol = gap_next >= 4
    ice_prehol = base_ice & prehol
    scen_rows += eval_subset("S3_ice_preholiday", quiet.loc[ice_prehol[quiet.sig_cal.to_numpy()]],
                             control, dates)
    scen_rows += eval_subset("S3_ice_normal_cal",
                             quiet.loc[(base_ice & ~prehol)[quiet.sig_cal.to_numpy()]],
                             control, dates)
    # S4 二次冰点: ice day with another ice day within previous 10 sessions
    prev_ice10 = pd.Series(base_ice).shift(1).rolling(10, min_periods=1).max().to_numpy(dtype=bool)
    scen_rows += eval_subset("S4_second_ice",
                             quiet.loc[(base_ice & prev_ice10)[quiet.sig_cal.to_numpy()]],
                             control, dates)
    scen_rows += eval_subset("S4_first_ice",
                             quiet.loc[(base_ice & ~prev_ice10)[quiet.sig_cal.to_numpy()]],
                             control, dates)
    # S5 冰点买宽基 (beta): control fwd on ice days vs all days
    for hz in (1, 2, 5, 20):
        v = control[hz]
        scen_rows.append({"spec": f"S5_beta_after_ice_fwd{hz}", "period": "FULL",
                          "trades": int(np.isfinite(v[base_ice]).sum()),
                          "mean_ret_pct": float(np.nanmean(v[base_ice]) * 100),
                          "fwd_note": "equal-weight market, tail口径"})
        scen_rows.append({"spec": f"S5_beta_all_days_fwd{hz}", "period": "FULL",
                          "trades": int(np.isfinite(v).sum()),
                          "mean_ret_pct": float(np.nanmean(v) * 100)})
    # S6 弱共振反转放宽: amount>=1.1, zt>=20, streak>2
    amount_surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    repair = ice0.repair.to_numpy(dtype=bool)
    weak_rev = (repair & (amount_surge >= 1.1)
                & (sent.zt_count.to_numpy() >= 20)
                & (sent.max_streak.to_numpy() > 2))
    chase = trades.loc[(trades.kind.isin(["breakout", "confirmed"]))
                       & (trades.timing == "tail") & (trades.exit_set == "h20")]
    scen_rows += eval_subset("S6_weak_reversal_chase",
                             chase.loc[weak_rev[chase.sig_cal.to_numpy()]], control, dates)
    scen_rows += eval_subset("S6_strict_reversal_chase",
                             chase.loc[ice0.reversal.to_numpy()[chase.sig_cal.to_numpy()]],
                             control, dates)
    pd.DataFrame(scen_rows).to_csv(args.output / "scenarios.csv",
                                   index=False, encoding="utf-8-sig")

    audit = {
        "generated_at": datetime.now().isoformat(),
        "snapshot": str(args.snapshot.resolve()),
        "protocol": "IS selects, OOS validates; full grid reported; no re-selection on OOS",
        "grid_size": len(GRID),
        "selected_gate": best_name,
        "min_is_trades": MIN_IS_TRADES,
        "limits": [
            "网格 32 组存在多重比较风险, OOS 显著性按单一口径解读.",
            "板块为通达信二级行业静态映射, 未含历史变更.",
            "节前口径=次一交易日间隔>=4个自然日; 护盘假冰点=指数涨跌>-0.2%.",
            "结果不构成买卖建议.",
        ],
    }
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[DONE] {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
