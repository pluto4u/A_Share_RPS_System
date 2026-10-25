"""Train-track (顺向火车轨) signal backtest on the frozen panel (research only).

Entry: tail buy at close on the first-in-8-bars signal day; skip one-price
limit-up closes (cannot fill). Exits (all with costs, T+1, limit-down roll):
  fixed5 / fixed10 / fixed20  - time exits (close)
  ma20_break                  - close < MA20 -> sell next open (cap 60d)
  trail_s04_t08               - hard stop -4% + trail 8% off peak (cap 20d)
Protocol: IS (2016-09->2021-12) inspects, OOS (2022-01->2026-09) validates.
Portfolio: 20-slot cash compounding (trend_start.slot_portfolio).
Statistics only; no buy/sell instructions.
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
from trend_start.ice_opt import simulate_trail_exit
from trend_start.panel import load_panel
from trend_start.pattern_backtest import _sell_close, _sell_next_open, exec_flags
from trend_start.slot_portfolio import slot_compound
from trend_start.train_track import train_track_signals

EXITS = ("fixed5", "fixed10", "fixed20", "ma20_break", "trail_s04_t08")
MA20_CAP = 60


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=20)
    args = ap.parse_args()
    # Post-freeze TDX refresh on 2026-10-01 evening touched sz300909.day (tail
    # records verified identical to the frozen panel) and gbbq (adjustment
    # table; panel is NOT rebuilt). Whitelist them; any other drift raises.
    verify_snapshot(args.snapshot, allow_drift=(
        r"vipdoc\sz\lday\sz300909.day", r"T0002\hq_cache\gbbq"))
    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    args.output.mkdir(parents=True, exist_ok=False)

    print("[SIG] computing train-track signals ...", flush=True)
    idx = [str(c) for c in panel.codes]
    c = pd.DataFrame(panel.field("close").astype(float), index=idx)
    o = pd.DataFrame(panel.field("open").astype(float), index=idx)
    h = pd.DataFrame(panel.field("high").astype(float), index=idx)
    lo = pd.DataFrame(panel.field("low").astype(float), index=idx)
    sig = train_track_signals(o, h, lo, c)
    n_sig = int(sig.to_numpy().sum())
    days_with = int(sig.any(axis=0).sum())
    print(f"[SIG] signals {n_sig}, days with >=1 signal {days_with} "
          f"({days_with / sig.shape[1]:.1%} of {sig.shape[1]} sessions)", flush=True)
    yearly = sig.copy()
    yearly.columns = dates
    yearly.T.groupby(yearly.T.index.year).sum().sum(axis=1).to_csv(
        args.output / "signals_by_year.csv", encoding="utf-8-sig")

    sc = sell_cost(panel.dates)
    oos0 = int(dates.searchsorted(pd.Timestamp("2022-01-01")))

    rows: list[dict] = []
    sig_mat = sig.to_numpy()
    for k, code in enumerate(idx):
        if k % 400 == 0:
            print(f"[SIM] {k} stocks", flush=True)
        bars = np.flatnonzero(sig_mat[k])
        if len(bars) == 0:
            continue
        arr = {f: panel.field(f)[k].astype(float) for f in ("open", "high", "low", "close")}
        fin = np.isfinite(arr["close"])
        if not fin.all():
            continue  # signals only exist where 250d history exists; keep full-length rows
        o_, h_, lo_, c_ = arr["open"], arr["high"], arr["low"], arr["close"]
        fl = exec_flags(code, o_, h_, lo_, c_, panel.dates)
        up_locked, down_open, locked = fl["up_locked"], fl["down_open"], fl["locked"]
        ma20 = pd.Series(c_).rolling(20).mean().to_numpy()
        n = len(c_)
        for e in bars:
            if up_locked[e]:
                continue  # 一字涨停尾盘买不进
            entry_px = float(c_[e])

            def rec(variant: str, bar: int, px: float, reason: str, ac: bool,
                    _code: str = code, _e: int = int(e),
                    _px: float = entry_px) -> None:
                rows.append({"variant": variant, "code": _code, "entry_cal": _e,
                             "exit_cal": int(bar), "hold": int(bar - _e),
                             "exit_reason": reason, "exit_at_close": bool(ac),
                             "ret": float(px * (1 - sc[bar])
                                          / (_px * (1 + BUY_COST)) - 1)})

            for hz in (5, 10, 20):
                bar, px, unf, ac = _sell_close(min(e + hz, n - 1), n, locked,
                                               down_open, o_, c_)
                rec(f"fixed{hz}", bar, px, "unfinished" if unf else "time_stop", ac)
            # ma20_break
            trig = n
            for j in range(e + 1, min(e + MA20_CAP, n - 1) + 1):
                if c_[j] < ma20[j]:
                    trig = j
                    break
            if trig < n:
                bar, px, unf, ac = _sell_next_open(trig, n, down_open, locked, o_, c_)
                rec("ma20_break", bar, px, "unfinished" if unf else "ma20_break", ac)
            else:
                bar, px, unf, ac = _sell_close(min(e + MA20_CAP, n - 1), n, locked,
                                               down_open, o_, c_)
                rec("ma20_break", bar, px, "unfinished" if unf else "time_stop", ac)
            bar, px, reason, ac = simulate_trail_exit(
                code, int(e), o_, h_, lo_, c_, panel.dates,
                hard_stop=0.04, trail_pct=0.08, max_hold=20)
            rec("trail_s04_t08", bar, px, reason, ac)

    sim = pd.DataFrame(rows)
    sim.to_csv(args.output / "train_track_trades.csv", index=False, encoding="utf-8-sig")
    sim["period"] = np.where(sim.entry_cal >= oos0, "OOS", "IS")
    print(f"[SIM] trades x exits: {len(sim)}", flush=True)

    # market benchmark on signal days (CSI300 fwd from close)
    idx_close = panel.index_close.astype(float)
    sig_days = np.flatnonzero(sig.any(axis=0).to_numpy())
    bench = {}
    for hz in (5, 20):
        f = np.full(len(idx_close), np.nan)
        f[:-hz] = idx_close[hz:] / idx_close[:-hz] - 1
        for tag, (a, b) in {"IS": (250, oos0), "OOS": (oos0, len(idx_close) - hz - 1)}.items():
            d = sig_days[(sig_days >= a) & (sig_days <= b)]
            bench[f"csi300_fwd{hz}_{tag}"] = float(np.nanmean(f[d]) * 100)
            allidx = np.arange(a, b)
            bench[f"csi300_all_fwd{hz}_{tag}"] = float(np.nanmean(f[allidx]) * 100)

    st_rows = []
    for v in EXITS:
        for period in ("IS", "OOS"):
            g = sim[(sim.variant == v) & (sim.period == period)]
            st_rows.append({"variant": v, "period": period, "n": len(g),
                            "mean_ret_pct": g.ret.mean() * 100,
                            "win_pct": (g.ret > 0).mean() * 100,
                            "median_hold": g.hold.median(),
                            "unfinished_pct": (g.exit_reason == "unfinished").mean() * 100})
    stats = pd.DataFrame(st_rows)
    stats.to_csv(args.output / "train_track_stats.csv", index=False, encoding="utf-8-sig")
    print("\n=== per-trade ===")
    print(stats.round(2).to_string(index=False))
    print("bench:", {k: round(v, 2) for k, v in bench.items()})

    pf_rows = []
    for v in EXITS:
        sub = sim[sim.variant == v]
        for period, (a, b) in {"IS": (0, oos0 - 1), "OOS": (oos0, 10**9),
                               "ALL": (0, 10**9)}.items():
            s = sub[(sub.entry_cal >= a) & (sub.entry_cal <= b)]
            tr = s.rename(columns={"entry_cal": "sig_cal"})[
                ["sig_cal", "code", "ret", "hold"]]
            r = slot_compound(tr, slots=args.slots)
            pf_rows.append({"variant": v, "period": period,
                            "total_ret_pct": r.total_ret * 100,
                            "filled": r.filled, "skipped_locked": r.skipped_locked})
    pf = pd.DataFrame(pf_rows)
    pf.to_csv(args.output / "train_track_portfolio.csv", index=False, encoding="utf-8-sig")
    print("\n=== slot portfolio ===")
    print(pf.round(2).to_string(index=False))

    (args.output / "audit.json").write_text(json.dumps({
        "signal": "train-track (TDX 顺向火车轨), turnover<10% omitted (no float shares)",
        "exits": list(EXITS), "slots": args.slots, "bench": bench,
        "protocol": "IS inspects, OOS validates; statistics only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
