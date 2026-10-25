"""Frequency-expansion grids toward 20%/yr (research only).

Part 1: circle-pool x relaxed ice-gate grid (12 variants), vectorized f3/f5.
Part 2: circle-pool entries on market repair days.
Part 3: chase day-rule loosening grid (3x3) on breakout/confirmed tail entries.
All: costs approx (fixed BUY_COST + date sell_cost), no limit-lock filtering in
scan (finalists re-verified exactly). IS inspects, OOS validates; admission
requires same-sign positive in BOTH periods. Statistics only.
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
from pathlib import Path

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT, verify_snapshot
from trend_start.engine import BUY_COST, sell_cost
from trend_start.ice_opt import IceGateParams, ice_gate_mask
from trend_start.icepoint import classify_ice, daily_sentiment
from trend_start.panel import load_panel
from trend_start.slot_portfolio import slot_compound
from trend_start.train_track import train_track_signals

THRS = (0.22, 0.26, 0.30)
STREAKS = (1, 2)
WEAK = (True, False)
POOL_DAYS = 60


def stats_block(tr: pd.DataFrame, oos0: int, slots: int = 20) -> dict:
    out = {}
    for period, (a, b) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9)}.items():
        s = tr[(tr.entry_cal >= a) & (tr.entry_cal <= b)]
        if len(s) < 30:
            out[period] = None
            continue
        r = slot_compound(s.rename(columns={"entry_cal": "sig_cal"})
                          [["sig_cal", "code", "ret", "hold"]], slots=slots)
        out[period] = {"n": len(s), "mean": s.ret.mean() * 100,
                       "win": (s.ret > 0).mean() * 100,
                       "pf": r.total_ret * 100, "days": s.entry_cal.nunique()}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    verify_snapshot(args.snapshot, allow_drift=(
        r"vipdoc\sz\lday\sz300909.day", r"T0002\hq_cache\gbbq"))
    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))
    args.output.mkdir(parents=True, exist_ok=False)

    idx = [str(c) for c in panel.codes]
    c = pd.DataFrame(panel.field("close").astype(float), index=idx)
    o = pd.DataFrame(panel.field("open").astype(float), index=idx)
    h = pd.DataFrame(panel.field("high").astype(float), index=idx)
    lo = pd.DataFrame(panel.field("low").astype(float), index=idx)
    print("[SIG] circles ...", flush=True)
    circles = train_track_signals(o, h, lo, c).to_numpy()
    n_stk, n_day = circles.shape
    pool = np.zeros_like(circles)
    for s in range(n_stk):
        for b in np.flatnonzero(circles[s]):
            pool[s, b + 1:b + 1 + POOL_DAYS] = True

    sent = daily_sentiment(panel.codes, panel.dates, panel.field("open"),
                           panel.field("high"), panel.field("close"),
                           panel.field("amount"))
    ice0 = classify_ice(sent, panel.index_close)
    hot = np.asarray(sent.advance_share.to_numpy() >= 0.85)
    repair = ice0.repair.to_numpy(dtype=bool)

    close_np = c.to_numpy()
    sc = sell_cost(panel.dates)
    valid = np.isfinite(close_np)

    def pool_trades(day_mask: np.ndarray, hz: int) -> pd.DataFrame:
        m = pool & day_mask[None, :] & valid
        m[:, :260] = False
        m[:, n_day - hz:] = False
        si, ei = np.nonzero(m)
        entry = close_np[si, ei]
        exitp = close_np[si, ei + hz]
        ok = np.isfinite(entry) & np.isfinite(exitp)  # drop suspension gaps
        si, ei, entry, exitp = si[ok], ei[ok], entry[ok], exitp[ok]
        ret = exitp * (1 - sc[ei + hz]) / (entry * (1 + BUY_COST)) - 1
        return pd.DataFrame({"code": np.array(idx, dtype=object)[si],
                             "entry_cal": ei, "ret": ret, "hold": hz})

    rows = []
    print("=== Part 1: pool x gate grid ===", flush=True)
    for thr in THRS:
        for st in STREAKS:
            for wk in WEAK:
                p = IceGateParams(advance_thr=thr, min_streak=st,
                                  require_weakening=wk)
                mask = ice_gate_mask(sent, p) & ~hot
                for hz, vn in ((3, "f3"), (5, "f5")):
                    tr = pool_trades(mask, hz)
                    stt = stats_block(tr, oos0)
                    for period in ("IS", "OOS"):
                        if stt[period]:
                            rows.append({"part": "gate_grid", "thr": thr,
                                         "streak": st, "weak": wk, "exit": vn,
                                         "period": period, **stt[period]})
                print(f"  thr={thr} streak={st} weak={wk} days={int(mask.sum())}",
                      flush=True)

    print("=== Part 2: pool x repair-day entry ===", flush=True)
    for hz, vn in ((3, "f3"), (5, "f5")):
        tr = pool_trades(repair & ~hot, hz)
        stt = stats_block(tr, oos0)
        for period in ("IS", "OOS"):
            if stt[period]:
                rows.append({"part": "repair_entry", "exit": vn, "period": period,
                             **stt[period]})
    print(f"  repair days={int((repair & ~hot).sum())}", flush=True)

    print("=== Part 3: chase day-rule grid ===", flush=True)
    trades = pd.read_csv(ROOT / "output/pattern_regime_backtest_20260930/trades.csv")
    chase = trades.loc[(trades.kind.isin(["breakout", "confirmed"]))
                       & (trades.timing == "tail") & (trades.exit_set == "h20"),
                       ["code", "entry_cal"]].copy()
    # per-stock segment mapping: calendar day -> trading-day position, so that
    # exits are hz TRADING days later even across suspension gaps
    code_row = {str(k): i for i, k in enumerate(idx)}
    seg_cols = {i: np.flatnonzero(np.isfinite(close_np[i])) for i in range(n_stk)}
    ci = chase.code.map(code_row).to_numpy()
    ce = chase.entry_cal.to_numpy()
    pos = np.full(len(chase), -1)
    for i in np.unique(ci):
        m = ci == i
        cols_i = seg_cols[i]
        p = np.searchsorted(cols_i, ce[m])
        p = np.clip(p, 0, len(cols_i) - 1)
        hit = cols_i[p] == ce[m]
        pos[m] = np.where(hit, p, -1)
    chase["pos"] = pos
    chase = chase[pos >= 0]
    amount_surge = (sent.total_amount / sent.prev5_amount).to_numpy()
    zt = sent.zt_count.to_numpy()
    streak = sent.max_streak.to_numpy()
    for amt_thr in (1.05, 1.1, 1.2):
        for zt_thr in (10, 20, 30):
            mask = repair & (amount_surge >= amt_thr) & (zt >= zt_thr) & (streak > 2)
            sel = chase.loc[mask[chase.entry_cal.to_numpy()]]
            for hz, vn in ((3, "f3"), (5, "f5")):
                ii = sel.code.map(code_row).to_numpy()
                p = sel.pos.to_numpy()
                ok = np.array([p[k] + hz < len(seg_cols[ii[k]])
                               for k in range(len(sel))])
                s = sel[ok]
                ii = ii[ok]
                p = p[ok]
                exit_cal = np.array([seg_cols[ii[k]][p[k] + hz]
                                     for k in range(len(s))])
                e = s.entry_cal.to_numpy()
                ret = (close_np[ii, exit_cal] * (1 - sc[exit_cal])
                       / (close_np[ii, e] * (1 + BUY_COST)) - 1)
                tr = pd.DataFrame({"code": s.code.to_numpy(), "entry_cal": e,
                                   "ret": ret, "hold": hz})
                stt = stats_block(tr, oos0)
                for period in ("IS", "OOS"):
                    if stt[period]:
                        rows.append({"part": "chase_grid", "amt": amt_thr,
                                     "zt": zt_thr, "exit": vn, "period": period,
                                     **stt[period]})
            print(f"  amt>={amt_thr} zt>={zt_thr} days={int(mask.sum())}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(args.output / "freq_grids.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 200)
    print(out.round(2).to_string(index=False))
    (args.output / "audit.json").write_text(json.dumps({
        "note": "vectorized scan, no limit-lock filter; admission=IS&OOS same sign",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
