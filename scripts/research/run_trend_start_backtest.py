"""Right-side trend-start backtest on local TDX daily bars, then the as-of signal list.

Research only: output ends at rules, signal states and invalidation levels.
See TREND_START_BACKTEST.md for definitions and results.
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
from typing import Any

import numpy as np
import pandas as pd

from trend_start.engine import (
    FWD,
    VARIANTS,
    StockBars,
    block_bootstrap_mean,
    chain,
    curve_stats,
    event_rows,
    outcomes,
    portfolio,
    trade_stats,
)
from trend_start.panel import build_panel, load_panel
from trend_start.signals import StartParams, cross_section_rps, stock_signals

ROOT = PROJECT_ROOT
IS_START, IS_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2021-12-31")
OOS_START = pd.Timestamp("2022-01-01")
MIN_IS_TRADES = 300
SLOTS = 20
REGIME_MA = 60
GRID = [
    StartParams(rps_now=r, fresh_below=f, vol_mult=v)
    for r in (85.0, 90.0, 95.0)
    for f in (60.0, 80.0, 101.0)
    for v in (1.5, 2.0)
]
FIXED = ["A_trend", "A_start", "B_minervini", "C_turtle55"]
EXTRA_KEYS = ["rps120", "rps250", "lateness60", "bias20", "sig_close", "sig_ma20"]


def compute_rps(panel: Any, cache: Path) -> dict[str, np.ndarray]:
    path = cache / "rps.npz"
    if path.exists():
        z = np.load(path)
        return {k: z[k] for k in z.files}
    rps = cross_section_rps(panel.field("close"))
    np.savez(path, **rps)
    return rps


def build_events(panel: Any, rps: dict[str, np.ndarray],
                 cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paths = [cache / f"{k}_v2.pkl" for k in ("events", "control", "today")]
    if all(p.exists() for p in paths):
        return tuple(pd.read_pickle(p) for p in paths)  # type: ignore[return-value]
    rows: list[dict] = []
    ctl: list[pd.DataFrame] = []
    today: list[dict] = []
    dates = panel.dates
    last_cal = len(dates) - 1
    for i, code in enumerate(panel.codes):
        if i % 500 == 0:
            print(f"[EVENTS] {i}/{len(panel.codes)} events={len(rows)}", flush=True)
        cols = np.flatnonzero(~np.isnan(panel.field("close")[i]))
        if len(cols) < 260:
            continue
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(np.float64)
                          for f in ("open", "high", "low", "close", "vol", "amount")})
        for k in ("RPS50", "RPS120", "RPS250", "RS"):
            d[k] = rps[k][i, cols].astype(np.float64)
        sig = stock_signals(d, GRID)
        b = StockBars(str(code), cols, dates[cols], d.open.to_numpy(), d.high.to_numpy(),
                      d.low.to_numpy(), d.close.to_numpy(), sig.MA20.to_numpy())
        out = outcomes(b)
        extra = dict(zip(EXTRA_KEYS, (
            d.RPS120.to_numpy(), d.RPS250.to_numpy(), sig.lateness60.to_numpy(),
            sig.bias20.to_numpy(), d.close.to_numpy(), sig.MA20.to_numpy(),
        )))
        for name in [c for c in sig.columns if sig[c].dtype == bool and c != "ELIGIBLE"]:
            rows.extend(event_rows(b, out, sig[name].to_numpy(), name, extra))
            if cols[-1] == last_cal and bool(sig[name].iloc[-1]):
                today.append({"strategy": name, "code": str(code),
                              **{k: float(a[-1]) for k, a in extra.items()}})
        s = np.flatnonzero(sig.ELIGIBLE.to_numpy()[:-1])
        s = s[out.buyable[s + 1]]
        ctl.append(pd.DataFrame({
            "sig_cal": cols[s].astype(np.int32),
            **{f"ret_{k}": out.ret[k][s + 1].astype(np.float32)
               for k in (*VARIANTS, *(f"fwd{h}" for h in FWD))},
        }))
    frames = (pd.DataFrame(rows), pd.concat(ctl, ignore_index=True),
              pd.DataFrame(today, columns=["strategy", "code", *EXTRA_KEYS]))
    for frame, p in zip(frames, paths):
        frame.to_pickle(p)
    return frames


def in_period(df: pd.DataFrame, dates: np.ndarray, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    d = pd.DatetimeIndex(dates[df.sig_cal.to_numpy()])
    return np.asarray((d >= start) & (d <= end))


def excess_stats(trades: pd.DataFrame, control_mean: pd.DataFrame, dates: np.ndarray,
                 key: str) -> dict[str, Any]:
    ctl = control_mean[f"ret_{key}"].reindex(trades.sig_cal).to_numpy()
    x = trades[f"ret_{key}"].to_numpy() - ctl
    month = pd.Series(pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(x), month)
    return {"control_mean_pct": float(np.nanmean(ctl) * 100),
            "excess_mean_pct": float(np.nanmean(x) * 100),
            "excess_ci_lo_pct": lo * 100, "excess_ci_hi_pct": hi * 100}


def market_regimes(panel: Any) -> tuple[dict[str, np.ndarray], pd.DataFrame]:
    hs = pd.Series(panel.index_close).ffill()
    c = panel.field("close").astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = c[:, 1:] / c[:, :-1] - 1
    ew = pd.Series(np.cumprod(1 + np.r_[0.0, np.nanmean(r, axis=0)]))
    frame = pd.DataFrame({"CSI300": hs, "CSI300_MA": hs.rolling(REGIME_MA).mean(),
                          "EW": ew, "EW_MA": ew.rolling(REGIME_MA).mean()})
    regimes = {
        "ALL": np.ones(len(hs), dtype=bool),
        "HS300>MA60": (frame.CSI300 > frame.CSI300_MA).to_numpy(),
        "EW>MA60": (frame.EW > frame.EW_MA).to_numpy(),
    }
    return regimes, frame


def benchmark_curves(panel: Any, frame: pd.DataFrame, start: int, end: int) -> dict[str, pd.Series]:
    idx = pd.DatetimeIndex(panel.dates[start : end + 1])
    hs = pd.Series(frame.CSI300.to_numpy()[start : end + 1], index=idx)
    ew = pd.Series(frame.EW.to_numpy()[start : end + 1], index=idx)
    return {"CSI300": hs / hs.iloc[0], "EqualWeightA": ew / ew.iloc[0]}


def yearly(curve: pd.Series) -> dict[str, float]:
    y = curve.groupby(curve.index.year).last()
    prev = pd.Series([curve.iloc[0], *y.iloc[:-1]], index=y.index)
    return {str(k): float((y[k] / prev[k] - 1) * 100) for k in y.index}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--as-of", default="2026-09-29")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    as_of = pd.Timestamp(args.as_of)
    cache = ROOT / "output" / f"trend_start_cache_{as_of:%Y%m%d}"
    if args.rebuild or not (cache / "panel.npz").exists():
        panel, _ = build_panel(args.tdx_root, as_of, cache)
    else:
        panel = load_panel(cache)
    panel_audit = json.loads((cache / "panel_audit.json").read_text(encoding="utf-8"))
    if pd.Timestamp(panel.dates[-1]) != as_of:
        raise ValueError("panel last session != as_of")
    print(f"[PANEL] {len(panel.codes)} stocks x {len(panel.dates)} sessions", flush=True)
    rps = compute_rps(panel, cache)
    events, control, today_sig = build_events(panel, rps, cache)
    del rps
    dates = panel.dates
    control_mean = control.groupby("sig_cal")[[c for c in control.columns if c.startswith("ret_")]].mean()
    regimes, market = market_regimes(panel)
    print(f"[EVENTS] {len(events)} signal events; control entries {len(control)}", flush=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "output" / f"trend_start_backtest_{as_of:%Y%m%d}_{stamp}"
    out.mkdir(parents=True)
    periods = {"IS": (IS_START, IS_END), "OOS": (OOS_START, as_of)}
    reentry = events.loc[events.strategy == "REENTRY"]
    by_strategy = {s: g for s, g in events.groupby("strategy")}

    def strat_events(name: str, regime: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
        base = name.removesuffix("+RE")
        ev = by_strategy[base]
        ev = ev.loc[regimes[regime][ev.sig_cal.to_numpy()]]
        re = None
        if name.endswith("+RE"):
            re = reentry.loc[regimes[regime][reentry.sig_cal.to_numpy()]]
        return ev, re

    rows: list[dict] = []
    chains: dict[tuple[str, str, str], pd.DataFrame] = {}

    def record(name: str, regime: str, variants: tuple[str, ...]) -> None:
        ev, re = strat_events(name, regime)
        for v in variants:
            ch = chain(ev, v, re)
            chains[(name, regime, v)] = ch
            for p, (a, b) in periods.items():
                t = ch.loc[in_period(ch, dates, a, b)]
                row = {"strategy": name, "regime": regime, "variant": v, "period": p,
                       **trade_stats(t, v)}
                if len(t):
                    row.update(excess_stats(t, control_mean, dates, v))
                    row["reentry_share_pct"] = float((t.kind == "reentry").mean() * 100)
                    row["giveback_pct"] = float(((t.mfe_open >= 0.20) & (t[f"ret_{v}"] <= 0)).mean() * 100)
                    row["mfe20_share_pct"] = float((t.mfe_open >= 0.20).mean() * 100)
                    for h in FWD:
                        fx = excess_stats(t, control_mean, dates, f"fwd{h}")
                        row[f"fwd{h}_mean_pct"] = float(t[f"ret_fwd{h}"].mean() * 100)
                        row[f"fwd{h}_excess_pct"] = fx["excess_mean_pct"]
                        row[f"fwd{h}_ci_lo"] = fx["excess_ci_lo_pct"]
                        row[f"fwd{h}_ci_hi"] = fx["excess_ci_hi_pct"]
                rows.append(row)

    # 1) proposed D grid, no regime, both mandated exit timings
    for s in sorted(k for k in by_strategy if k.startswith("D_")):
        record(s, "ALL", ("open", "close"))
    grid = pd.DataFrame(rows)
    grid = grid.loc[(grid.variant == "open") & (grid.period == "IS") & (grid.trades >= MIN_IS_TRADES)]
    best = str(grid.sort_values(["excess_mean_pct", "trades"], ascending=False).iloc[0].strategy)
    print(f"[SELECT-1] in-sample best D variant: {best}", flush=True)
    candidates = [*FIXED, best, best + "+RE"]
    for s in candidates:
        for rg in regimes:
            record(s, rg, VARIANTS if rg == "ALL" else ("open", "close"))
    trade_table = pd.DataFrame(rows)
    trade_table.to_csv(out / "trade_stats.csv", index=False, encoding="utf-8-sig")

    # 2) portfolios
    close_ff = pd.DataFrame(panel.field("close")).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    cal = pd.DatetimeIndex(dates)
    windows = {
        "IS": (int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right") - 1)),
        "OOS": (int(cal.searchsorted(OOS_START)), len(cal) - 1),
    }
    windows["FULL"] = (windows["IS"][0], len(cal) - 1)
    pf_rows, curves = [], {}
    for s in candidates:
        for rg in regimes:
            ev, re = strat_events(s, rg)
            for v in (VARIANTS if rg == "ALL" else ("open", "close")):
                for w, (a, b) in windows.items():
                    curve, trades = portfolio(ev, v, close_ff, code_row, dates, a, b, SLOTS, reentry=re)
                    st = curve_stats(curve)
                    exposure = np.nan
                    if len(trades):
                        held_days = (np.minimum(trades.pf_exit_cal, b) - trades.entry_cal + 1).sum()
                        exposure = float(held_days / (SLOTS * (b - a + 1)) * 100)
                    st.update({"strategy": s, "regime": rg, "variant": v, "window": w,
                               "pf_trades": len(trades), "slot_usage_pct": exposure,
                               "pf_win_rate_pct": float((trades.pf_ret > 0).mean() * 100) if len(trades) else np.nan,
                               "pf_mean_ret_pct": float(trades.pf_ret.mean() * 100) if len(trades) else np.nan})
                    pf_rows.append(st)
                    if w == "FULL":
                        curves[f"{s}|{rg}|{v}"] = curve
                        trades.to_csv(out / f"pf_trades_{s}_{rg.replace('>', 'gt')}_{v}.csv",
                                      index=False, encoding="utf-8-sig")
        print(f"[PORTFOLIO] {s} done", flush=True)
    for w, (a, b) in windows.items():
        for name, curve in benchmark_curves(panel, market, a, b).items():
            pf_rows.append({"strategy": name, "regime": "-", "variant": "-", "window": w, **curve_stats(curve)})
            if w == "FULL":
                curves[f"{name}|-|-"] = curve
    pf_table = pd.DataFrame(pf_rows)
    pf_table.to_csv(out / "portfolio_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(curves).to_csv(out / "equity_curves.csv", encoding="utf-8-sig")
    pd.DataFrame({k: yearly(c) for k, c in curves.items()}).to_csv(out / "yearly_returns.csv", encoding="utf-8-sig")

    # 3) pre-declared final choice: best in-sample portfolio Sharpe, next-open exit
    isp = pf_table.loc[(pf_table.window == "IS") & (pf_table.variant == "open")
                       & pf_table.strategy.isin(candidates) & (pf_table.pf_trades >= 100)]
    pick = isp.sort_values("sharpe", ascending=False).iloc[0]
    final = {"strategy": str(pick.strategy), "regime": str(pick.regime)}
    print(f"[SELECT-2] final (IS Sharpe): {final}", flush=True)

    # 4) diagnostics: entry gap and MA20 extension buckets
    diag = []
    for s in dict.fromkeys([final["strategy"].removesuffix("+RE"), best, "A_trend", "B_minervini"]):
        ch = chains[(s, "ALL", "open")]
        for p, (a, b) in periods.items():
            t = ch.loc[in_period(ch, dates, a, b)].copy()
            t["gap"] = t.entry_px / t.sig_close - 1
            for col, bins in (("gap", [-1, 0, 0.02, 0.05, 1]), ("bias20", [-1, 0.05, 0.10, 0.15, 5])):
                t["bucket"] = pd.cut(t[col], bins)
                for k, g in t.groupby("bucket", observed=True):
                    diag.append({"strategy": s, "period": p, "factor": col, "bucket": str(k),
                                 "trades": len(g), "mean_ret_pct": g.ret_open.mean() * 100,
                                 "win_rate_pct": (g.ret_open > 0).mean() * 100})
    pd.DataFrame(diag).to_csv(out / "diagnostics.csv", index=False, encoding="utf-8-sig")

    # 5) as-of lists
    from run_daily_rps import names_from_tnf

    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    last = len(dates) - 1
    close_now = panel.field("close")[:, last]

    def live_fields(code: str) -> dict[str, Any]:
        i = code_row[code]
        c = panel.field("close")[i, np.flatnonzero(~np.isnan(panel.field("close")[i]))].astype(np.float64)
        ma20 = c[-20:].mean()
        return {
            "代码": code, "名称": names.get(code, "缺"), "收盘": round(float(c[-1]), 2),
            "MA20": round(float(ma20), 3), "MA20乖离%": round((c[-1] / ma20 - 1) * 100, 2),
            "次日MA20失效临界收盘": round(float(c[-19:].sum() / 19), 3),
            "有今日日K": bool(np.isfinite(close_now[i])),
        }

    def risky(code: str) -> bool:
        n = names.get(code, "")
        return not n or "ST" in n.upper().replace(" ", "") or "退" in n

    live_rows = []
    for r in today_sig.loc[today_sig.strategy.isin([*FIXED, best, "REENTRY"])].itertuples():
        if not risky(r.code):
            live_rows.append({"方案": r.strategy, "状态": "今日新信号", **live_fields(r.code),
                              "RPS120": round(r.rps120, 1), "RPS250": round(r.rps250, 1),
                              "距60日最低收盘涨幅%": round(r.lateness60 * 100, 1)})
    held_rows = []
    for s in dict.fromkeys([final["strategy"], best, best + "+RE", "A_trend"]):
        ch = chains[(s, final["regime"] if s == final["strategy"] else "ALL", "open")]
        for r in ch.loc[ch.unfinished_open].itertuples():
            if not risky(r.code):
                held_rows.append({"方案": s, "状态": "规则持有中", **live_fields(r.code),
                                  "信号日": str(pd.Timestamp(dates[r.sig_cal]).date()),
                                  "入场日": str(pd.Timestamp(dates[r.entry_cal]).date()),
                                  "入场价(前复权)": round(r.entry_px, 3),
                                  "持有至今收益%": round(r.ret_open * 100, 2), "类型": r.kind})
    pd.DataFrame(live_rows).to_csv(out / "asof_new_signals.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(held_rows).to_csv(out / "asof_rule_held.csv", index=False, encoding="utf-8-sig")
    regime_now = {k: bool(v[last]) for k, v in regimes.items()}
    market.tail(5).assign(date=cal[-5:]).to_csv(out / "market_regime_tail.csv", index=False, encoding="utf-8-sig")

    audit = {
        "as_of": str(as_of.date()),
        "generated_at": datetime.now().isoformat(),
        "panel": {k: v for k, v in panel_audit.items() if k != "excluded_detail"},
        "periods": {"IS": [str(IS_START.date()), str(IS_END.date())],
                    "OOS": [str(OOS_START.date()), str(as_of.date())]},
        "selection_1": f"D variant with max IS excess mean (next-open exit), trades>={MIN_IS_TRADES}: {best}",
        "selection_2": f"max IS portfolio Sharpe over {candidates} x {list(regimes)} (open, >=100 trades): {final}",
        "regime_now": regime_now,
        "grid": [p.__dict__ for p in GRID],
        "slots": SLOTS,
        "events": int(len(events)),
        "control_entries": int(len(control)),
        "output": str(out),
    }
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str),
                                    encoding="utf-8")
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
