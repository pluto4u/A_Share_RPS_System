"""日常 RPS 各层（L1 / L2 / Super / 火车轨 / 早期启涨）放进研究成交引擎的历史复盘。

口径
- 输入：研究缓存 trend_start_cache_20260930 的前复权面板（只读）。
- 各层条件逐条复刻 rps_analyzer 的实现，包括其实际行为：
  L1/L2 的"接近 N 日新高"只看最近 35 根（screen 只取 35 根窗口）；
  火车轨周线 RPS 在当日 L1 股票内排名；火车轨只在每周最后一个交易日评估。
  另加 L1_fix120 / L2_fix250 变体，按文档意图使用完整 120 / 250 日窗口。
- 信号：名单"新进入"当日收盘；Super 另测"连续 3 日在榜"；L2、火车轨另测首次触发。
- 成交与退出：trend_start.engine（次日开盘买、涨停不买、跌停顺延、佣金滑点印花税）。
- 对照：同日 close>MA20、20 日均额>=1 亿、>=251 根的股票，同规则买卖。

限制：股票池缺历史退市股，收益偏乐观；2022 年后已被看过，只是复盘，不是独立样本外。
用法: py -3.12 research/layer_backtest_20261006/run.py --output output/layer_backtest_20261006/v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from trend_start.engine import (  # noqa: E402
    BUY_COST, StockBars, block_bootstrap_mean, chain, curve_stats, outcomes, portfolio,
)
from trend_start.panel import load_panel  # noqa: E402
from trend_start.pattern import a_trend_mask  # noqa: E402

CACHE = Path(r"F:\88888.投资\08.选股\07.investment_research\right_side_strategy_bundle_20260924_221054"
             r"\right_side_strategy\output\trend_start_cache_20260930")
IS_START, IS_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2021-12-31")
OOS_START = pd.Timestamp("2022-01-01")
EXITS = ("open", "open2", "ma60")
FWD = ("fwd20", "fwd60")
PF_EXITS = ("open", "ma60")
SLOTS = 20


def roll_sum(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    cs = np.cumsum(np.r_[0.0, x])
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = cs[n:] - cs[:-n]
    return out


def ma(c: np.ndarray, n: int, min_periods: int | None = None) -> np.ndarray:
    return pd.Series(c).rolling(n, min_periods=min_periods or n).mean().to_numpy()


def gt(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.asarray(a > b, dtype=bool)


def ge(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.asarray(a >= b, dtype=bool)


def lag(x: np.ndarray, k: int, fill=np.nan) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float64 if fill is np.nan else x.dtype)
    out[:k] = fill
    out[k:] = x[:-k] if k else x
    return out


def high_low_drawdown(h: np.ndarray, lo: np.ndarray, n: int) -> np.ndarray:
    """_cond3_drawdown: 窗口内最高价首次出现之后（含当日）的最低价相对最高价的回撤。"""
    out = np.full(len(h), np.nan)
    if len(h) < n:
        return out
    hw, lw = swv(h, n), swv(lo, n)
    idx = hw.argmax(1)
    mask = np.arange(n)[None, :] >= idx[:, None]
    low_after = np.where(mask, lw, np.inf).min(1)
    peak = hw[np.arange(len(hw)), idx]
    out[n - 1:] = np.where(peak > 0, (peak - low_after) / peak, 0.0)
    return out


def ma_trend(ma10: np.ndarray, ma20: np.ndarray, n: int = 5) -> np.ndarray:
    """_cond4_ma_trend: 严格(最近 n-1 个 MA20 增量>=0 且最近 n 根 MA10>=MA20) 或 宽松。"""
    rising = np.ones(len(ma20), dtype=bool)
    for k in range(n - 1):
        rising &= ge(lag(ma20, k), lag(ma20, k + 1))
    above = np.ones(len(ma20), dtype=bool)
    for k in range(n):
        above &= ge(lag(ma10, k), lag(ma20, k))
    strict = rising & above
    strict[: n] = False
    loose = ge(ma10, lag(ma10, 1)) & ge(ma20, lag(ma20, 1)) & ge(ma10, ma20)
    return strict | loose


def tdx_conds(c, h, lo, amt, m, r120, r250, *, rps_sum, d250, d200, d20s, near_n, full_window):
    """SXHCG1..5（L2 只用这 5 条）。near_n 是实际回看根数。"""
    c1 = gt(r120 + r250, rps_sum)
    c2 = (gt(c, m[20])
          & (roll_sum(gt(c, m[250]), 30) >= d250)
          & (roll_sum(gt(c, m[200]), 30) >= d200)
          & ((roll_sum(gt(c, m[20]), 10) >= d20s)
             | ((roll_sum(gt(c, m[10]), 4) >= 3) & (roll_sum(gt(c, m[20]), 4) >= 3))))
    dd = high_low_drawdown(h, lo, 20)
    hh = pd.Series(c).rolling(near_n, min_periods=1 if full_window else near_n).max().to_numpy()
    c3 = ~gt(dd, 0.25) & np.isfinite(dd) & ~(c / hh <= 0.80)
    c4 = ma_trend(m[10], m[20])
    return c1 & c2 & c3 & c4


def daily_layers(d: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    c, h, lo, v, amt = d["c"], d["h"], d["lo"], d["v"], d["amt"]
    r50, r120, r250 = d["r50"], d["r120"], d["r250"]
    n = len(c)
    m = {p: ma(c, p) for p in (5, 10, 20, 60, 200, 250)}
    bar = np.arange(n)
    has_rps = np.isfinite(r120) & np.isfinite(r250)
    ok35 = (bar >= 34) & has_rps

    base = dict(rps_sum=180.0, d250=20, d200=20, d20s=7)
    extra = ((roll_sum(gt(c, m[5]), 10) >= 6)
             & (roll_sum(gt(amt, 1e8), 10) >= 7)
             & (roll_sum(gt(m[5], c), 3) <= 1))
    l1 = ok35 & tdx_conds(c, h, lo, amt, m, r120, r250, **base, near_n=35, full_window=False) & extra
    l1_fix = ok35 & tdx_conds(c, h, lo, amt, m, r120, r250, **base, near_n=120, full_window=True) & extra

    strict = dict(rps_sum=185.0, d250=25, d200=25, d20s=9)
    l2_cond = ok35 & tdx_conds(c, h, lo, amt, m, r120, r250, **strict, near_n=35, full_window=False)
    l2_cond_fix = ok35 & tdx_conds(c, h, lo, amt, m, r120, r250, **strict, near_n=250, full_window=True)
    prior40 = roll_sum_partial(lag(l2_cond.astype(float), 1, 0.0), 40)
    l2 = l1 & l2_cond
    l2_first = l2 & (prior40 == 0)
    l2_fix = l1_fix & l2_cond_fix

    ret20 = c / lag(c, 20) - 1
    sup = l1 & ge(r120, 98) & ge(r250, 98) & ~gt(c / m[60] - 1, 0.60) & ~gt(ret20, 0.60) & ge(amt, 5e7)
    sup &= np.isfinite(m[60]) & np.isfinite(ret20)
    sup3 = sup & lag(sup, 1, False) & lag(sup, 2, False)

    # 早期启涨
    above20 = gt(c, m[20])
    streak = np.zeros(n, dtype=np.int64)
    run = 0
    for i in range(n):
        run = run + 1 if above20[i] else 0
        streak[i] = run
    seg_start = bar - streak + 1
    base_idx = np.clip(seg_start - 1, 0, n - 1)
    seg_ret = c / c[base_idx] - 1
    volma60 = ma(v, 60, 30)
    pct = c / lag(c, 1) - 1
    bo = ge(v, 1.3 * volma60) & ge(pct, 0.025)
    bo_cs = np.cumsum(np.r_[0, bo.astype(np.int64)])
    bo_cnt = bo_cs[bar + 1] - bo_cs[np.clip(seg_start, 0, n)]
    nh = pd.Series(h).rolling(60, min_periods=1).max().to_numpy()
    v_cs = np.cumsum(np.r_[0.0, v])
    has_pre = seg_start >= 80
    s0 = np.clip(seg_start, 0, n)
    pre = (v_cs[s0] - v_cs[np.clip(s0 - 20, 0, n)]) / 20
    pre_base = (v_cs[np.clip(s0 - 20, 0, n)] - v_cs[np.clip(s0 - 80, 0, n)]) / 60
    with np.errstate(invalid="ignore", divide="ignore"):
        pre_ratio = np.where(pre_base > 0, pre / pre_base, np.nan)
    el7 = ~has_pre | np.isnan(pre_ratio) | ~gt(pre_ratio, 1.30)
    early = ((bar >= 89) & np.isfinite(r120) & np.isfinite(m[60]) & np.isfinite(m[20]) & ge(amt, 5e7)
             & ge(r120, 70) & ~gt(r120, 100)
             & (streak >= 3) & (streak <= 12) & (seg_start >= 1)
             & ge(seg_ret, 0.05) & ~gt(seg_ret, 0.45)
             & (bo_cnt >= 1)
             & ~gt(c / m[60] - 1, 0.40)
             & ge(c / nh, 0.85)
             & el7)

    liq = ge(ma(amt, 20), 1e8)
    control = (bar >= 250) & liq & above20
    return {
        "L1": l1, "L1_fix120": l1_fix, "L2": l2, "L2_first": l2_first, "L2_fix250": l2_fix,
        "Super": sup, "Super_hold3": sup3, "Early": early, "Early_L1": early & l1,
        "_control": control, "_ma20": m[20], "_ma60": m[60], "_ma10": m[10],
    }


def roll_sum_partial(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=1).sum().to_numpy()


def new_entry(mask: np.ndarray) -> np.ndarray:
    return mask & ~lag(mask, 1, False)


# ----------------------------------------------------------------------------- 周线火车轨

def weekly_frame(dates: np.ndarray, o, h, lo, c) -> pd.DataFrame:
    dt = pd.DatetimeIndex(dates)
    wk = (dt - pd.to_timedelta(dt.weekday, unit="D") + pd.Timedelta(days=4)).normalize()
    df = pd.DataFrame({"wk": wk, "o": o, "h": h, "lo": lo, "c": c, "bar": np.arange(len(c))})
    w = df.groupby("wk").agg(h=("h", "max"), lo=("lo", "min"), c=("c", "last"), last_bar=("bar", "last"))
    cw = w.c.to_numpy()
    m = {p: ma(cw, p) for p in (10, 20, 60, 120)}
    s2 = gt(cw, m[20]) & (roll_sum(gt(cw, m[60]), 30) >= 25) & (roll_sum(gt(cw, m[20]), 10) >= 9)
    s2 &= np.isfinite(m[20])
    dd = high_low_drawdown(w.h.to_numpy(), w.lo.to_numpy(), 20)
    hh = pd.Series(cw).rolling(120, min_periods=1).max().to_numpy()
    s3 = np.isfinite(dd) & ~gt(dd, 0.25) & ~(cw / hh <= 0.80)
    s4 = ma_trend(m[10], m[20])
    k = np.arange(len(cw))
    w["s234"] = s2 & s3 & s4
    w["evaluable"] = (k >= 27) & np.isfinite(m[120])
    w["roc120"] = cw / lag(cw, 120) - 1
    return w


def huoche_signals(panel, codes_idx, weekly: dict[int, pd.DataFrame], l1_on_date: dict[int, np.ndarray],
                   week_end_cal: np.ndarray) -> list[tuple[int, int, bool]]:
    """返回 (stock_row, cal, is_first)；只在每周最后一个交易日评估。"""
    out = []
    week_keys = pd.DatetimeIndex(panel.dates[week_end_cal])
    week_keys = (week_keys - pd.to_timedelta(week_keys.weekday, unit="D") + pd.Timedelta(days=4)).normalize()
    for cal, wk in zip(week_end_cal, week_keys):
        members = l1_on_date.get(int(cal))
        if members is None or len(members) == 0:
            continue
        frames = {i: weekly[i] for i in members if i in weekly and wk in weekly[i].index}
        if not frames:
            continue
        pos = {i: f.index.get_loc(wk) for i, f in frames.items()}
        needed = sorted({f.index[p] for i, f in frames.items()
                         for p in range(max(0, pos[i] - 8), pos[i] + 1)})
        rps = {}
        for w in needed:
            vals = {i: f.at[w, "roc120"] for i, f in frames.items() if w in f.index}
            s = pd.Series(vals, dtype=float)
            rps[w] = s.rank(pct=True) * 100
        for i, f in frames.items():
            p = pos[i]
            row = f.iloc[p]
            if not row.evaluable:
                continue
            r_now = rps[wk].get(i, np.nan)
            if not (np.isfinite(r_now) and r_now >= 90 and row.s234):
                continue
            first = True
            for q in range(max(0, p - 8), p):
                wq = f.index[q]
                rq = rps[wq].get(i, np.nan)
                if np.isfinite(rq) and rq >= 90 and f.s234.iloc[q]:
                    first = False
                    break
            out.append((i, int(cal), first))
    return out


# ----------------------------------------------------------------------------- 事件与统计

def event_frame(b: StockBars, out, sig: np.ndarray, name: str, rps120: np.ndarray) -> pd.DataFrame | None:
    s = np.flatnonzero(sig[:-1])
    if not len(s):
        return None
    e = s + 1
    s, e = s[out.buyable[e]], e[out.buyable[e]]
    if not len(s):
        return None
    f = {"strategy": name, "code": b.code, "sig_bar": s, "sig_cal": b.cal[s].astype(np.int32),
         "entry_bar": e, "entry_cal": b.cal[e].astype(np.int32), "entry_px": b.o[e],
         "rps120": rps120[s], "bias20": b.c[s] / b.ma20[s] - 1}
    for v in EXITS:
        k = out.exit_bar[v][e]
        f[f"ret_{v}"] = out.ret[v][e]
        f[f"exit_bar_{v}"] = k
        f[f"exit_cal_{v}"] = b.cal[k].astype(np.int32)
        f[f"exit_px_{v}"] = out.exit_px[v][e]
        f[f"exit_at_close_{v}"] = out.exit_at_close[v][e]
        f[f"unfinished_{v}"] = out.unfinished[v][e]
        f[f"hold_{v}"] = k - e
    for k in FWD:
        f[f"ret_{k}"] = out.ret[k][e]
    return pd.DataFrame(f)


def stats(t: pd.DataFrame, key: str, control_mean: pd.DataFrame, dates: np.ndarray) -> dict:
    r = t[f"ret_{key}"]
    ok = r.notna()
    t, r = t.loc[ok], r.loc[ok]
    if r.empty:
        return {"trades": 0}
    wins, losses = r[r > 0], r[r <= 0]
    ctl = control_mean[f"ret_{key}"].reindex(t.sig_cal).to_numpy()
    x = r.to_numpy() - ctl
    month = pd.Series(pd.DatetimeIndex(dates[t.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(x), month)
    row = {
        "trades": int(len(r)),
        "win_rate_pct": len(wins) / len(r) * 100,
        "mean_ret_pct": r.mean() * 100,
        "median_ret_pct": r.median() * 100,
        "avg_win_pct": wins.mean() * 100 if len(wins) else np.nan,
        "avg_loss_pct": losses.mean() * 100 if len(losses) else np.nan,
        "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else np.nan,
        "control_mean_pct": float(np.nanmean(ctl) * 100),
        "excess_mean_pct": float(np.nanmean(x) * 100),
        "excess_ci_lo_pct": lo * 100,
        "excess_ci_hi_pct": hi * 100,
    }
    if f"hold_{key}" in t:
        row["median_hold"] = float(t[f"hold_{key}"].median())
        row["unfinished_pct"] = float(t[f"unfinished_{key}"].mean() * 100)
    return row


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def daily_rps(close: np.ndarray, min_bars: int = 250) -> dict[str, np.ndarray]:
    """calculate_rps 口径：个股交易日 shift(n)，按日期 rank(pct=True)；总根数 < min_bars 的股票不入池。"""
    n, m = close.shape
    out = {}
    for p in (50, 120, 250):
        mat = np.full((n, m), np.nan)
        for i in range(n):
            cols = np.flatnonzero(~np.isnan(close[i]))
            if len(cols) < min_bars or len(cols) <= p:
                continue
            c = close[i, cols].astype(np.float64)
            mat[i, cols[p:]] = c[p:] / c[:-p] - 1
        out[f"RPS{p}"] = (pd.DataFrame(mat).rank(axis=0, pct=True) * 100).to_numpy()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 只股票（冒烟测试）")
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit(f"拒绝覆盖已有目录: {args.output}")
    t0 = time.time()
    panel = load_panel(CACHE)
    rps_research = np.load(CACHE / "rps.npz")
    r120_res, r250_res = rps_research["RPS120"], rps_research["RPS250"]
    dates = panel.dates
    close = panel.field("close")
    print(f"[PANEL] {len(panel.codes)} x {len(dates)}  {dates[0]}..{dates[-1]}", flush=True)
    drps = daily_rps(close)
    print(f"[RPS] daily-system RPS done {time.time() - t0:.0f}s", flush=True)

    rows_n = len(panel.codes) if not args.limit else args.limit
    events: list[pd.DataFrame] = []
    control: list[pd.DataFrame] = []
    weekly: dict[int, pd.DataFrame] = {}
    l1_members: dict[int, list[int]] = {}

    def build(i: int):
        cols = np.flatnonzero(~np.isnan(close[i]))
        if len(cols) < 260:
            return None
        g = {k: panel.field(f)[i, cols].astype(np.float64)
             for k, f in (("o", "open"), ("h", "high"), ("lo", "low"), ("c", "close"),
                          ("v", "vol"), ("amt", "amount"))}
        g.update(r50=drps["RPS50"][i, cols], r120=drps["RPS120"][i, cols], r250=drps["RPS250"][i, cols])
        lay = daily_layers(g)
        b = StockBars(str(panel.codes[i]), cols, dates[cols], g["o"], g["h"], g["lo"], g["c"],
                      lay["_ma20"], ma60=lay["_ma60"])
        return cols, g, lay, b, outcomes(b)

    for i in range(rows_n):
        if i % 500 == 0:
            print(f"[STOCK] {i}/{rows_n}  {time.time() - t0:.0f}s", flush=True)
        built = build(i)
        if built is None:
            continue
        cols, g, lay, b, out = built
        sigs = {
            "L1_new": new_entry(lay["L1"]),
            "L1_fix120_new": new_entry(lay["L1_fix120"]),
            "L2_new": new_entry(lay["L2"]),
            "L2_first": lay["L2_first"],
            "L2_fix250_new": new_entry(lay["L2_fix250"]),
            "Super_new": new_entry(lay["Super"]),
            "Super_hold3": new_entry(lay["Super_hold3"]),
            "Early_new": new_entry(lay["Early"]),
            "EarlyL1_new": new_entry(lay["Early_L1"]),
            "A_trend_new": new_entry(a_trend_mask(g["c"], g["v"], r120_res[i, cols].astype(float),
                                                  r250_res[i, cols].astype(float))),
        }
        for name, s in sigs.items():
            ev = event_frame(b, out, s, name, g["r120"])
            if ev is not None:
                events.append(ev)
        s = np.flatnonzero(lay["_control"][:-1])
        s = s[out.buyable[s + 1]]
        control.append(pd.DataFrame({"sig_cal": cols[s].astype(np.int32),
                                     **{f"ret_{k}": out.ret[k][s + 1].astype(np.float32)
                                        for k in (*EXITS, *FWD)}}))
        l1_bars = np.flatnonzero(lay["L1"])
        for j in l1_bars:
            l1_members.setdefault(int(cols[j]), []).append(i)
        if len(l1_bars):
            weekly[i] = weekly_frame(dates[cols], g["o"], g["h"], g["lo"], g["c"])

    print(f"[HUOCHE] weekly evaluation  {time.time() - t0:.0f}s", flush=True)
    dt = pd.DatetimeIndex(dates)
    wk = (dt - pd.to_timedelta(dt.weekday, unit="D")).normalize()
    week_end_cal = np.flatnonzero(np.r_[wk[1:] != wk[:-1], True])
    hits = huoche_signals(panel, None, weekly, l1_members, week_end_cal)
    hu = pd.DataFrame(hits, columns=["row", "cal", "first"])
    if not hu.empty:
        hu = hu.sort_values(["row", "cal"])
        prev_week = {int(c): int(p) for p, c in zip(week_end_cal[:-1], week_end_cal[1:])}
        hu["prev_cal"] = hu.cal.map(prev_week)
        in_set = set(zip(hu.row, hu.cal))
        hu["new"] = [(r, pc) not in in_set for r, pc in zip(hu.row, hu.prev_cal)]
        for i, gq in hu.groupby("row"):
            _, g, _, b, out = build(int(i))
            r120 = g["r120"]
            pos = pd.Series(np.arange(len(b.cal)), index=b.cal)
            for name, sel in (("Huoche_new", gq.new), ("Huoche_first", gq["first"])):
                cals = gq.cal[sel.to_numpy()]
                cals = cals[cals.isin(pos.index)]
                if cals.empty:
                    continue
                mask = np.zeros(len(b.cal), dtype=bool)
                mask[pos.loc[cals].to_numpy()] = True
                ev = event_frame(b, out, mask, name, r120)
                if ev is not None:
                    events.append(ev)

    ev = pd.concat(events, ignore_index=True)
    ctl = pd.concat(control, ignore_index=True)
    control_mean = ctl.groupby("sig_cal")[[c for c in ctl.columns if c.startswith("ret_")]].mean()
    sig_dt = pd.DatetimeIndex(dates[ev.sig_cal.to_numpy()])
    ev = ev.loc[sig_dt >= IS_START].reset_index(drop=True)
    sig_dt = pd.DatetimeIndex(dates[ev.sig_cal.to_numpy()])
    ev["period"] = np.where(sig_dt <= IS_END, "IS", "OOS_viewed")
    print(f"[EVENTS] {len(ev)} events; control entries {len(ctl)}  {time.time() - t0:.0f}s", flush=True)

    args.output.mkdir(parents=True)
    trade_rows = []
    for name, g in ev.groupby("strategy"):
        for v in EXITS:
            ch = chain(g, v)
            for p in ("IS", "OOS_viewed"):
                t = ch.loc[ch.period == p]
                trade_rows.append({"strategy": name, "exit": v, "basis": "chained", "period": p,
                                   **stats(t, v, control_mean, dates)})
        for k in FWD:
            for p in ("IS", "OOS_viewed"):
                t = g.loc[g.period == p]
                trade_rows.append({"strategy": name, "exit": k, "basis": "every_signal", "period": p,
                                   **stats(t, k, control_mean, dates)})
    trade_table = pd.DataFrame(trade_rows)
    trade_table.to_csv(args.output / "trade_stats.csv", index=False, encoding="utf-8-sig")

    close_ff = pd.DataFrame(close).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    cal_is = (int(np.searchsorted(dates, np.datetime64(IS_START.date()))),
              int(np.searchsorted(dates, np.datetime64(IS_END.date()), side="right") - 1))
    cal_oos = (int(np.searchsorted(dates, np.datetime64(OOS_START.date()))), len(dates) - 1)
    pf_rows, curves = [], {}
    for name, g in ev.groupby("strategy"):
        for v in PF_EXITS:
            for p, (a, z) in (("IS", cal_is), ("OOS_viewed", cal_oos)):
                curve, trades = portfolio(g, v, close_ff, code_row, dates, a, z, slots=SLOTS)
                st = curve_stats(curve)
                pf_rows.append({"strategy": name, "exit": v, "period": p, "pf_trades": len(trades), **st})
                curves[f"{name}|{v}|{p}"] = curve
    c = close.astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = c[:, 1:] / c[:, :-1] - 1
    ew = pd.Series(np.cumprod(1 + np.r_[0.0, np.nanmean(r, axis=0)]), index=pd.DatetimeIndex(dates))
    hs = pd.Series(panel.index_close, index=pd.DatetimeIndex(dates)).ffill()
    for p, (a, z) in (("IS", cal_is), ("OOS_viewed", cal_oos)):
        for bname, s in (("EqualWeightA", ew), ("CSI300", hs)):
            pf_rows.append({"strategy": bname, "exit": "buy_hold", "period": p, **curve_stats(s.iloc[a:z + 1])})
    pd.DataFrame(pf_rows).to_csv(args.output / "portfolio_stats.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(curves).to_csv(args.output / "equity_curves.csv", encoding="utf-8-sig")
    yearly = (ev.assign(year=sig_dt.year).groupby(["strategy", "year"]).size().unstack(fill_value=0))
    yearly.to_csv(args.output / "signals_per_year.csv", encoding="utf-8-sig")

    last = len(dates) - 1
    mine = {str(panel.codes[i]) for i in l1_members.get(last, [])}
    fidelity: dict = {"l1_last_day_vectorized": sorted(mine)}
    ref = ROOT / "output" / "price_basis_20261006" / "v1" / "rps_by_stock.csv"
    if ref.exists() and not args.limit:
        prod = set(pd.read_csv(ref, encoding="utf-8-sig").query("L1_qfq").ts_code)
        fidelity["l1_last_day_production_qfq"] = sorted(prod)
        fidelity["l1_only_vectorized"] = sorted(mine - prod)
        fidelity["l1_only_production"] = sorted(prod - mine)
        print(f"[FIDELITY] L1 {pd.Timestamp(dates[last]).date()}: vectorized {len(mine)}, "
              f"production {len(prod)}, both {len(mine & prod)}", flush=True)
    manifest = {
        "inputs": {"panel.npz": sha256(CACHE / "panel.npz"), "rps.npz": sha256(CACHE / "rps.npz")},
        "script_sha256": sha256(Path(__file__)),
        "stocks_used": rows_n,
        "events": int(len(ev)),
        "control_entries": int(len(ctl)),
        "periods": {"IS": [str(IS_START.date()), str(IS_END.date())],
                    "OOS_viewed": [str(OOS_START.date()), str(pd.Timestamp(dates[-1]).date())]},
        "costs": {"buy": BUY_COST, "sell": "0.125% + stamp 0.1%/0.05% after 2023-08-28"},
        "limits": [
            "股票池缺历史退市股，收益偏乐观",
            "2022 年后已被查看，只是复盘，不是独立样本外",
            "火车轨只在每周最后一个交易日评估；L1 只在个股交易日评估（不沿用停牌日旧数据）",
            "日常 RPS 股票池按全文件根数>=250 入池，与生产一致，含轻微未来条件",
            "未模拟 run_portfolio_check 的 -8% 止损、Super 缺席等完整 SOP 退出",
        ],
        **fidelity,
        "elapsed_s": round(time.time() - t0),
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    show = trade_table.loc[trade_table.exit.isin(["open", "fwd60"]),
                           ["strategy", "exit", "period", "trades", "win_rate_pct", "mean_ret_pct",
                            "excess_mean_pct", "excess_ci_lo_pct", "excess_ci_hi_pct"]]
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(show.round(2).to_string(index=False))
        print(pd.DataFrame(pf_rows)[["strategy", "exit", "period", "cagr_pct", "max_drawdown_pct", "sharpe"]]
              .round(2).to_string(index=False))
    print(f"[DONE] {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
