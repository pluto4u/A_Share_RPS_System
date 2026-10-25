"""Native exits from published trend systems, on the same entries and data.

No parameter search. D keeps the variant already chosen in-sample by the prior
study (RPS120>=95, volume x2). Every other rule below was fixed before this run.
See TREND_EXIT_STUDY.md.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from run_trend_start_backtest import IS_END, IS_START, OOS_START, market_regimes
from trend_start.engine import (
    StockBars,
    chain,
    chandelier_trigger,
    curve_stats,
    execution_flags,
    first_close_below,
    first_true_from,
    outcomes,
    portfolio,
    sell_next_open,
    trade_stats,
)
from trend_start.engine import block_bootstrap_mean
from trend_start.panel import load_panel
from trend_start.signals import StartParams, stock_signals

ROOT = PROJECT_ROOT
SLOTS = 20
# Frozen from the previous in-sample selection. Not revisited here.
D_PARAM = StartParams(rps_now=95, fresh_below=101, vol_mult=2)
D_NAME = "D_r95_f101_v2"
ENTRIES = ["A_trend", "B_minervini", "C_turtle55", D_NAME, "W_stage2"]
# (entry, exit column, what the exit means)
PAIRS = [
    ("A_trend", "ma60", "收盘跌破MA60，次日开盘卖"),
    ("A_trend", "ma50", "收盘跌破MA50，次日开盘卖"),
    ("A_trend", "chand", "收盘跌破吊灯止损：持仓后最高价-3倍ATR"),
    ("B_minervini", "native", "先触及者：买入价下跌8%，或收盘跌破MA50"),
    ("B_minervini", "chand", "吊灯止损：持仓后最高价-3倍ATR"),
    ("B_minervini", "piv20", "形态失效：收盘跌回突破前20日最高价"),
    ("C_turtle55", "don20", "海龟系统二：收盘跌破前20日最低价"),
    ("C_turtle55", "turtle", "前20日最低价或买入价下方2倍ATR，先触及者"),
    ("D_r95_f101_v2", "ma60", "收盘跌破MA60"),
    ("D_r95_f101_v2", "chand", "吊灯止损：持仓后最高价-3倍ATR"),
    ("D_r95_f101_v2", "piv60", "形态失效：收盘跌回突破前60日最高收盘"),
    ("W_stage2", "ma150", "收盘跌破150日均线（30周线）"),
]
LEVEL_EXITS = ("open", "ma50", "ma60", "ma150", "don20")
PATH_EXITS = ("stop8", "chand", "native", "turtle", "piv20", "piv55", "piv60")


def _pack(b: StockBars, e: np.ndarray, trigger: np.ndarray, flags) -> dict[str, np.ndarray]:
    k, px, at_close, unf, ret = sell_next_open(b, trigger, flags, b.o[e])
    return {"bar": k, "px": px, "at": at_close, "unf": unf, "ret": ret}


def build(panel, cache: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    paths = [cache / "exit_events.pkl", cache / "exit_control.pkl"]
    if all(p.exists() for p in paths):
        return pd.read_pickle(paths[0]), pd.read_pickle(paths[1])
    ev_parts: list[pd.DataFrame] = []
    ctl_parts: list[pd.DataFrame] = []
    for i, code in enumerate(panel.codes):
        if i % 500 == 0:
            print(f"[EXIT] {i}/{len(panel.codes)}", flush=True)
        cols = np.flatnonzero(~np.isnan(panel.field("close")[i]))
        if len(cols) < 260:
            continue
        d = pd.DataFrame({f: panel.field(f)[i, cols].astype(np.float64)
                          for f in ("open", "high", "low", "close", "vol", "amount")})
        for k in ("RPS50", "RPS120", "RPS250", "RS"):
            d[k] = panel_rps[k][i, cols]
        sig = stock_signals(d, [D_PARAM])
        b = StockBars(
            str(code), cols, panel.dates[cols], d.open.to_numpy(), d.high.to_numpy(),
            d.low.to_numpy(), d.close.to_numpy(), sig.MA20.to_numpy(),
            ma50=sig.MA50.to_numpy(), ma60=sig.MA60.to_numpy(),
            ma150=sig.MA150.to_numpy(), don20=sig.DON20.to_numpy(),
        )
        out = outcomes(b)
        flags = execution_flags(b)
        chosen: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name in ENTRIES:
            s = np.flatnonzero(sig[name].to_numpy()[:-1])
            e = s + 1
            keep = out.buyable[e]
            chosen[name] = (s[keep], e[keep])
        elig = np.flatnonzero(sig.ELIGIBLE.to_numpy()[:-1]) + 1
        elig = elig[out.buyable[elig]]
        all_e = np.unique(np.concatenate([elig, *[e for _, e in chosen.values() if len(e)]]))
        if len(all_e) == 0:
            continue
        atr = sig.ATR.to_numpy()
        j_stop = first_close_below(b.c, all_e, b.o[all_e] * 0.92)
        j_2n = first_close_below(b.c, all_e, b.o[all_e] - 2 * atr[all_e - 1])
        j_ma50 = first_true_from(np.isfinite(b.ma50) & (b.c < b.ma50), all_e)
        j_don = first_true_from(np.isfinite(b.don20) & (b.c < b.don20), all_e)
        j_ch = chandelier_trigger(b.h, b.c, atr, all_e, mult=3.0)
        packed = {
            "stop8": _pack(b, all_e, j_stop, flags),
            "chand": _pack(b, all_e, j_ch, flags),
            "native": _pack(b, all_e, np.minimum(j_stop, j_ma50), flags),
            "turtle": _pack(b, all_e, np.minimum(j_don, j_2n), flags),
        }
        for piv, col in (("piv20", "PIV20"), ("piv55", "PIV55"), ("piv60", "PIV60")):
            level = sig[col].to_numpy()[all_e - 1]
            packed[piv] = _pack(b, all_e, first_close_below(b.c, all_e, level), flags)
        loc = pd.Series(np.arange(len(all_e)), index=all_e)

        def take(frame: dict[str, np.ndarray], e: np.ndarray) -> dict[str, np.ndarray]:
            at = loc.loc[e].to_numpy()
            return {k: v[at] for k, v in frame.items()}

        if len(elig):
            row = {"sig_cal": b.cal[elig - 1].astype(np.int32)}
            for name in LEVEL_EXITS:
                row[f"ret_{name}"] = out.ret[name][elig].astype(np.float32)
            got = {k: take(v, elig) for k, v in packed.items()}
            for name, g in got.items():
                row[f"ret_{name}"] = g["ret"].astype(np.float32)
            ctl_parts.append(pd.DataFrame(row))
        for name, (s, e) in chosen.items():
            if len(e) == 0:
                continue
            data = {
                "strategy": name, "code": str(code), "kind": "primary",
                "sig_bar": s, "sig_cal": b.cal[s], "entry_bar": e, "entry_cal": b.cal[e],
                "entry_px": b.o[e], "rps120": d.RPS120.to_numpy()[s],
                "lateness60": sig.lateness60.to_numpy()[s], "bias20": sig.bias20.to_numpy()[s],
            }
            for exit_name in LEVEL_EXITS:
                k = out.exit_bar[exit_name][e]
                data[f"exit_bar_{exit_name}"] = k
                data[f"exit_cal_{exit_name}"] = b.cal[k]
                data[f"exit_px_{exit_name}"] = out.exit_px[exit_name][e]
                data[f"exit_at_close_{exit_name}"] = out.exit_at_close[exit_name][e]
                data[f"unfinished_{exit_name}"] = out.unfinished[exit_name][e]
                data[f"ret_{exit_name}"] = out.ret[exit_name][e]
                data[f"hold_{exit_name}"] = k - e
            for exit_name, g0 in packed.items():
                g = take(g0, e)
                data[f"exit_bar_{exit_name}"] = g["bar"]
                data[f"exit_cal_{exit_name}"] = b.cal[g["bar"]]
                data[f"exit_px_{exit_name}"] = g["px"]
                data[f"exit_at_close_{exit_name}"] = g["at"]
                data[f"unfinished_{exit_name}"] = g["unf"]
                data[f"ret_{exit_name}"] = g["ret"]
                data[f"hold_{exit_name}"] = g["bar"] - e
            ev_parts.append(pd.DataFrame(data))
    events = pd.concat(ev_parts, ignore_index=True)
    control = pd.concat(ctl_parts, ignore_index=True)
    events.to_pickle(paths[0])
    control.to_pickle(paths[1])
    return events, control


def excess(trades: pd.DataFrame, control_mean: pd.DataFrame, dates: np.ndarray, key: str) -> dict:
    if trades.empty or f"ret_{key}" not in control_mean.columns:
        return {}
    ctl = control_mean[f"ret_{key}"].reindex(trades.sig_cal).to_numpy()
    x = trades[f"ret_{key}"].to_numpy() - ctl
    month = pd.Series(pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(x), month)
    return {"excess_mean_pct": float(np.nanmean(x) * 100),
            "excess_ci_lo_pct": lo * 100, "excess_ci_hi_pct": hi * 100,
            "control_mean_pct": float(np.nanmean(ctl) * 100)}


def main() -> int:
    global panel_rps
    as_of = pd.Timestamp("2026-09-29")
    cache = ROOT / "output" / f"trend_start_cache_{as_of:%Y%m%d}"
    panel = load_panel(cache)
    panel_rps = {k: np.load(cache / "rps.npz")[k] for k in ("RPS50", "RPS120", "RPS250", "RS")}
    events, control = build(panel, cache)
    print(f"[EXIT] events {len(events)} control {len(control)}", flush=True)
    dates = panel.dates
    ret_cols = [c for c in control.columns if c.startswith("ret_")]
    control_mean = control.groupby("sig_cal")[ret_cols].mean()
    regimes, market = market_regimes(panel)
    periods = {"IS": (IS_START, IS_END), "OOS": (OOS_START, as_of)}
    cal = pd.DatetimeIndex(dates)
    windows = {
        "IS": (int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right") - 1)),
        "OOS": (int(cal.searchsorted(OOS_START)), len(cal) - 1),
    }
    windows["FULL"] = (windows["IS"][0], len(cal) - 1)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "output" / f"trend_exit_study_{as_of:%Y%m%d}_{stamp}"
    out.mkdir(parents=True)
    trade_rows, pf_rows, curves = [], [], {}
    close_ff = pd.DataFrame(panel.field("close")).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    held_frames = []
    for strategy, exit_name, _rule in PAIRS:
        ev = events.loc[events.strategy == strategy]
        for regime, mask in regimes.items():
            if regime == "EW>MA60":
                continue
            sub = ev.loc[mask[ev.sig_cal.to_numpy()]]
            chained = chain(sub, exit_name) if len(sub) else sub
            for label, frame in (("signal", sub), ("chain", chained)):
                for period, (a, b0) in periods.items():
                    if len(frame) == 0:
                        t = frame
                    else:
                        when = pd.DatetimeIndex(dates[frame.sig_cal.to_numpy().astype(int)])
                        t = frame.iloc[np.flatnonzero((when >= a) & (when <= b0))]
                    row = {"strategy": strategy, "exit": exit_name, "regime": regime,
                           "set": label, "period": period, **trade_stats(t, exit_name)}
                    if len(t):
                        row.update(excess(t, control_mean, dates, exit_name))
                        row["ma20_exit_mean_pct"] = float(t.ret_open.mean() * 100)
                    trade_rows.append(row)
            if regime == "ALL":
                held_frames.append(chained.loc[chained[f"unfinished_{exit_name}"]].assign(
                    exit=exit_name) if len(chained) else chained)
            for window, (a, b0) in windows.items():
                curve, trades = portfolio(
                    sub, exit_name, close_ff, code_row, dates, a, b0, SLOTS)
                st = curve_stats(curve)
                st.update({"strategy": strategy, "exit": exit_name, "regime": regime,
                           "window": window, "pf_trades": len(trades),
                           "pf_mean_ret_pct": float(trades.pf_ret.mean() * 100) if len(trades) else np.nan})
                pf_rows.append(st)
                if window == "FULL" and regime == "ALL":
                    curves[f"{strategy}|{exit_name}"] = curve
        print(f"[PAIR] {strategy} {exit_name}", flush=True)
    for window, (a, b0) in windows.items():
        hs = pd.Series(market.CSI300.to_numpy()[a:b0 + 1])
        ew = pd.Series(market.EW.to_numpy()[a:b0 + 1])
        for name, curve in (("CSI300", hs / hs.iloc[0]), ("EqualWeightA", ew / ew.iloc[0])):
            pf_rows.append({"strategy": name, "exit": "-", "regime": "-", "window": window,
                            **curve_stats(pd.Series(curve.to_numpy(), index=cal[a:b0 + 1]))})
    pd.DataFrame(trade_rows).to_csv(out / "trade_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pf_rows).to_csv(out / "portfolio_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(curves).to_csv(out / "equity_curves.csv", encoding="utf-8-sig")
    held = pd.concat([h for h in held_frames if len(h)], ignore_index=True) if held_frames else pd.DataFrame()
    held.to_csv(out / "asof_rule_held.csv", index=False, encoding="utf-8-sig")
    audit = {
        "as_of": str(as_of.date()),
        "generated_at": datetime.now().isoformat(),
        "pairs": [{"entry": a, "exit": b, "rule": c} for a, b, c in PAIRS],
        "frozen_d": D_PARAM.__dict__,
        "no_parameter_search": True,
        "regime_now": {k: bool(v[-1]) for k, v in regimes.items()},
        "output": str(out),
    }
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
