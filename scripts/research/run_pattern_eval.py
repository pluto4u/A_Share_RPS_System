"""Evaluate the current right-side list against decline -> dry base -> right-side volume.

The pattern thresholds live in trend_start.pattern and are not searched.
Sample split matches the earlier studies: 2016-09 to 2021 in sample, 2022 to
2026-09-29 out of sample. Research output only.
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

from run_daily_rps import names_from_tnf
from run_trend_start_backtest import IS_END, IS_START, OOS_START
from trend_start.engine import (
    StockBars,
    block_bootstrap_mean,
    chain,
    curve_stats,
    outcomes,
    portfolio,
    trade_stats,
)
from trend_start.pattern import PHASES, _roll_mean, a_trend_mask, phase_flags

ROOT = PROJECT_ROOT
SLOTS = 20
LIQ = 1e8
EXITS = ("open", "ma60")


def _ffill(row: np.ndarray) -> np.ndarray:
    out = row.astype(np.float32, copy=True)
    bad = np.isnan(out)
    if not bad.any():
        return out
    idx = np.where(~bad, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    filled = out[idx]
    filled[bad & (idx == 0)] = np.nan
    return filled


def build(panel_path: Path, rps_path: Path) -> tuple[pd.DataFrame, dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    z = np.load(panel_path)
    rps = np.load(rps_path)
    dates = np.asarray(z["dates"])
    codes = np.asarray(z["codes"]).astype(str)
    fields = {k: np.asarray(z[k]) for k in ("open", "high", "low", "close", "vol", "amount")}
    r120_all = np.asarray(rps["RPS120"])
    r250_all = np.asarray(rps["RPS250"])
    z.close()
    rps.close()
    print("[PATTERN] arrays loaded", flush=True)
    n_dates = len(dates)
    cal = pd.DatetimeIndex(dates)
    is_lo, is_hi = int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right"))
    oos_lo = int(cal.searchsorted(OOS_START))
    diag = {p: {k: 0 for k in ("a_days", "a_new", "pattern_days", *(f"a_new_{name}" for name in PHASES),
                               *(f"a_all_{name}" for name in PHASES))}
            for p in ("IS", "OOS")}
    ctl_sum = {k: np.zeros(n_dates) for k in ("open", "ma60", "fwd20", "fwd60")}
    ctl_n = {k: np.zeros(n_dates) for k in ("open", "ma60", "fwd20", "fwd60")}
    parts: list[pd.DataFrame] = []
    today: list[dict] = []
    close_ff = np.full((len(codes), n_dates), np.nan, dtype=np.float32)
    last = n_dates - 1
    for i, code in enumerate(codes):
        if i % 500 == 0:
            print(f"[PATTERN] {i}/{len(codes)}", flush=True)
        raw_c = fields["close"][i]
        close_ff[i] = _ffill(raw_c.astype(np.float32))
        cols = np.flatnonzero(~np.isnan(raw_c))
        if len(cols) < 260:
            continue
        o = fields["open"][i][cols].astype(np.float64, copy=False)
        h = fields["high"][i][cols].astype(np.float64, copy=False)
        lo = fields["low"][i][cols].astype(np.float64, copy=False)
        c = raw_c[cols].astype(np.float64, copy=False)
        v = fields["vol"][i][cols].astype(np.float64, copy=False)
        amt = fields["amount"][i][cols].astype(np.float64, copy=False)
        r120 = r120_all[i][cols].astype(np.float64, copy=False)
        r250 = r250_all[i][cols].astype(np.float64, copy=False)
        flags = phase_flags(o, h, lo, c, v)
        trend = a_trend_mask(c, v, r120, r250)
        fresh = trend & ~np.r_[False, trend[:-1]]
        ma20 = _roll_mean(c, 20)
        ma60 = _roll_mean(c, 60)
        b = StockBars(code, cols, dates[cols], o, h, lo, c, ma20, ma60=ma60)
        result = outcomes(b)
        roll_min = np.full(len(c), np.nan)
        if len(c) >= 60:
            roll_min[59:] = np.lib.stride_tricks.sliding_window_view(c, 60).min(axis=1)
        lateness = c / np.r_[np.nan, roll_min[:-1]] - 1
        bias = c / ma20 - 1
        liq = _roll_mean(amt, 20) >= LIQ
        eligible = (np.arange(len(c)) >= 250) & liq & (c > ma20)
        s_elig = np.flatnonzero(eligible[:-1])
        if len(s_elig):
            e_elig = s_elig + 1
            ok = result.buyable[e_elig]
            s_elig, e_elig = s_elig[ok], e_elig[ok]
            cal_i = cols[s_elig]
            for key in ctl_sum:
                val = result.ret[key][e_elig]
                finite = np.isfinite(val)
                np.add.at(ctl_sum[key], cal_i[finite], val[finite])
                np.add.at(ctl_n[key], cal_i[finite], 1)
        marked = trend | flags["pattern"]
        _count_diag(diag, cols, marked, trend, fresh, flags, is_lo, is_hi, oos_lo)
        s = np.flatnonzero(marked[:-1])
        if len(s):
            e = s + 1
            ok = result.buyable[e]
            s, e = s[ok], e[ok]
            if len(s):
                parts.append(_event_frame(code, s, e, cols, o, r120, trend, fresh, flags, lateness, bias, result))
        if int(cols[-1]) == last and marked[-1]:
            today.append(_today(code, len(c) - 1, c, flags, trend, fresh, r120, bias, ma20, ma60))
    print(f"[PATTERN] stocks done, frames {len(parts)}", flush=True)
    events = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return events, ctl_sum, ctl_n, dates, close_ff, pd.DataFrame(today), diag, codes


def _count_diag(diag, cols, marked, trend, fresh, flags, is_lo, is_hi, oos_lo) -> None:
    cal = cols
    is_m = (cal >= is_lo) & (cal < is_hi)
    oos_m = cal >= oos_lo
    for period, mask in (("IS", is_m), ("OOS", oos_m)):
        bag = diag[period]
        on = trend & mask
        bag["a_days"] += int(on.sum())
        bag["a_new"] += int((fresh & mask).sum())
        bag["pattern_days"] += int((flags["pattern"] & mask).sum())
        for name in PHASES:
            bag[f"a_all_{name}"] += int((on & flags[name]).sum())
            bag[f"a_new_{name}"] += int((fresh & mask & flags[name]).sum())


def _event_frame(code, s, e, cols, o, r120, trend, fresh, flags, lateness, bias, result) -> pd.DataFrame:
    data = {
        "code": code, "sig_bar": s, "sig_cal": cols[s], "entry_bar": e,
        "entry_cal": cols[e], "entry_px": o[e], "rps120": r120[s],
        "a": trend[s], "new": fresh[s], "lateness60": lateness[s], "bias20": bias[s],
    }
    for name in PHASES:
        data[name] = flags[name][s]
    for exit_name in (*EXITS, "fwd20", "fwd60"):
        if exit_name.startswith("fwd"):
            data[f"ret_{exit_name}"] = result.ret[exit_name][e]
            continue
        k = result.exit_bar[exit_name][e]
        data[f"exit_bar_{exit_name}"] = k
        data[f"exit_cal_{exit_name}"] = cols[k]
        data[f"exit_px_{exit_name}"] = result.exit_px[exit_name][e]
        data[f"exit_at_close_{exit_name}"] = result.exit_at_close[exit_name][e]
        data[f"unfinished_{exit_name}"] = result.unfinished[exit_name][e]
        data[f"ret_{exit_name}"] = result.ret[exit_name][e]
        data[f"hold_{exit_name}"] = k - e
    return pd.DataFrame(data)


def _today(code, s, c, flags, trend, fresh, r120, bias, ma20, ma60) -> dict:
    return {
        "code": code, "close": float(c[s]), "a": bool(trend[s]), "new": bool(fresh[s]),
        "pattern": bool(flags["pattern"][s]), "decline": bool(flags["decline"][s]),
        "base": bool(flags["base"][s]), "dry": bool(flags["dry"][s]),
        "right": bool(flags["right"][s]), "rps120": float(r120[s]),
        "bias20": float(bias[s]), "ma20": float(ma20[s]), "ma60": float(ma60[s]),
    }


def _subset(events: pd.DataFrame, name: str) -> pd.DataFrame:
    a, new, pat = events["a"], events["new"], events["pattern"]
    if name == "新入选":
        return events.loc[new]
    if name == "新入选且符合模式":
        return events.loc[new & pat]
    if name == "新入选但不符合模式":
        return events.loc[new & ~pat]
    if name == "在名单上且当天符合模式":
        return events.loc[a & pat]
    if name == "模式但未进右侧名单":
        return events.loc[pat & ~a]
    raise KeyError(name)


def _excess(trades: pd.DataFrame, mean: pd.Series, dates: np.ndarray, key: str) -> dict:
    if trades.empty:
        return {}
    ctl = mean.reindex(trades.sig_cal).to_numpy()
    x = trades[f"ret_{key}"].to_numpy() - ctl
    month = pd.Series(pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(x), month)
    return {"excess_mean_pct": float(np.nanmean(x) * 100),
            "excess_ci_lo_pct": lo * 100, "excess_ci_hi_pct": hi * 100}


def _diff(left: pd.DataFrame, right: pd.DataFrame, dates: np.ndarray, key: str) -> dict:
    if left.empty or right.empty:
        return {}
    def pack(frame: pd.DataFrame) -> pd.DataFrame:
        month = pd.DatetimeIndex(dates[frame.sig_cal.to_numpy()]).to_period("M").astype(str)
        return pd.DataFrame({"x": frame[f"ret_{key}"].to_numpy(), "m": month})
    a, b = pack(left).dropna(), pack(right).dropna()
    months = sorted(set(a["m"]).intersection(b["m"]))
    if not months:
        return {}
    sa, ca = a.groupby("m").x.agg(["sum", "count"]).reindex(months).fillna(0).to_numpy().T
    sb, cb = b.groupby("m").x.agg(["sum", "count"]).reindex(months).fillna(0).to_numpy().T
    rng = np.random.default_rng(7)
    idx = rng.integers(0, len(months), size=(2000, len(months)))
    ma = sa[idx].sum(1) / np.maximum(ca[idx].sum(1), 1)
    mb = sb[idx].sum(1) / np.maximum(cb[idx].sum(1), 1)
    d = ma - mb
    return {"diff_mean_pct": float((a.x.mean() - b.x.mean()) * 100),
            "diff_ci_lo_pct": float(np.percentile(d, 2.5) * 100),
            "diff_ci_hi_pct": float(np.percentile(d, 97.5) * 100)}


def main() -> int:
    cache = ROOT / "output" / "trend_start_cache_20260929"
    events, ctl_sum, ctl_n, dates, close_ff, today, diag, codes = build(
        cache / "panel.npz", cache / "rps.npz")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "output" / f"trend_pattern_eval_20260929_{stamp}"
    out.mkdir(parents=True)
    events.to_pickle(out / "events.pkl")
    pd.DataFrame(diag).to_csv(out / "diagnosis.csv", encoding="utf-8-sig")
    mean = {k: pd.Series(ctl_sum[k] / np.where(ctl_n[k] > 0, ctl_n[k], np.nan))
            for k in ctl_sum}
    cal = pd.DatetimeIndex(dates)
    periods = {"IS": (IS_START, IS_END), "OOS": (OOS_START, pd.Timestamp(dates[-1]))}
    names = ("新入选", "新入选且符合模式", "新入选但不符合模式", "在名单上且当天符合模式", "模式但未进右侧名单")
    trade_rows = []
    diff_rows = []
    for name in names:
        sub = _subset(events, name)
        for exit_name in (*EXITS, "fwd20", "fwd60"):
            chained = chain(sub, "open" if exit_name.startswith("fwd") else exit_name) if len(sub) else sub
            frame = chained if exit_name in EXITS else sub
            for period, (a, b0) in periods.items():
                if len(frame) == 0:
                    t = frame
                else:
                    when = pd.DatetimeIndex(dates[frame.sig_cal.to_numpy().astype(int)])
                    t = frame.iloc[np.flatnonzero((when >= a) & (when <= b0))]
                row = {"group": name, "exit": exit_name, "period": period}
                if exit_name in EXITS:
                    row.update(trade_stats(t, exit_name))
                else:
                    r = t[f"ret_{exit_name}"].dropna() if len(t) else t
                    row["trades"] = int(len(r))
                    row["mean_ret_pct"] = float(r.mean() * 100) if len(r) else np.nan
                if len(t):
                    row.update(_excess(t, mean[exit_name], dates, exit_name))
                trade_rows.append(row)
        print(f"[GROUP] {name}", flush=True)
    for period, (a, b0) in periods.items():
        both = _subset(events, "新入选且符合模式")
        plain = _subset(events, "新入选但不符合模式")
        for key in ("open", "fwd60"):
            left, right = both, plain
            if len(left) and len(right):
                wl = pd.DatetimeIndex(dates[left.sig_cal.to_numpy().astype(int)])
                wr = pd.DatetimeIndex(dates[right.sig_cal.to_numpy().astype(int)])
                left = left.iloc[np.flatnonzero((wl >= a) & (wl <= b0))]
                right = right.iloc[np.flatnonzero((wr >= a) & (wr <= b0))]
            if key == "open" and len(left) and len(right):
                left, right = chain(left, "open"), chain(right, "open")
            diff_rows.append({"period": period, "exit": key, **_diff(left, right, dates, key)})
    code_row = {c: i for i, c in enumerate(codes)}
    windows = {
        "IS": (int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right") - 1)),
        "OOS": (int(cal.searchsorted(OOS_START)), len(cal) - 1),
    }
    windows["FULL"] = (windows["IS"][0], len(cal) - 1)
    pf_rows = []
    for name in ("新入选", "新入选且符合模式", "新入选但不符合模式"):
        sub = _subset(events, name)
        for exit_name in EXITS:
            for window, (a, b0) in windows.items():
                curve, trades = portfolio(sub, exit_name, close_ff, code_row, dates, a, b0, SLOTS)
                st = curve_stats(curve)
                st.update({"group": name, "exit": exit_name, "window": window, "pf_trades": len(trades)})
                pf_rows.append(st)
    names_map = names_from_tnf(Path("C:/new_tdx/T0002/hq_cache"))
    if len(today):
        today["名称"] = today["code"].map(names_map)
        today = today.loc[~today["名称"].fillna("").str.contains("ST|退")]
    today.to_csv(out / "asof_20260929.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trade_rows).to_csv(out / "trade_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(diff_rows).to_csv(out / "difference.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pf_rows).to_csv(out / "portfolio_stats.csv", index=False, encoding="utf-8-sig")
    (out / "audit.json").write_text(json.dumps({
        "as_of": str(pd.Timestamp(dates[-1]).date()),
        "pattern": "40日下跌>=15% 且低点在后半段; 随后40日振幅<=15% 漂移<=8% 不破前低; 横盘均量<=下跌均量的70%; 信号日阳线、收盘>MA20、高于横盘中位数、量>=横盘均量的1.5倍",
        "right_side": "run_daily_rps / A_trend，新入选=当天刚满足、前一天不满足",
        "no_parameter_search": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
