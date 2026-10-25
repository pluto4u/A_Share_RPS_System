"""强 RPS 缩量买入后的卖点：能否比 MA60 破位更早卖。

入场：entry_state_20261006 的 Shrink（当日量/20日均量<=0.6）、Shrink5（5日均量/60日均量<=0.7），
均在强势池（近 20 日进过 L1）内且收盘 >= MA20；次日开盘买，涨停不买。
卖点（收盘触发，次日开盘卖，跌停一字顺延，成本同 trend_start.engine）：
  MA20 / MA60：收盘跌破均线
  T10 / T20 / T40 / T60：持有满 N 根后卖
  Hot：量比>=1.8 或 收盘高于 MA20 15% 以上（entry_state 分桶里两段都最差的状态）
  HotVol / HotBias：只用其中一条
  TP10 / TP20：收盘较买入价涨 10% / 20%
  Trail10 / Trail15：收盘较买入后最高收盘回撤 10% / 15%
  组合卖点取最早触发，"_MA60" 表示 MA60 破位兜底。
比较：同一批入场逐笔配对，差值 = 卖点收益 - MA60 收益，按月块自助法求 95% 区间。

注意：Hot 的阈值来自 entry_state 分桶，该分桶看过 2022 后数据；本表 2022 后只是复盘。
限制：缺历史退市股；无历史 ST 标记。
用法: py -3.12 research/exit_timing_20261006/run.py --output output/exit_timing_20261006/v1
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, HERE.parents[1] / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, Path(spec.origin)


es, ES_PATH = _load("entry_state", "entry_state_20261006/run.py")
lb = es.lb

from trend_start.engine import (  # noqa: E402
    StockBars, block_bootstrap_mean, chain, curve_stats, execution_flags, first_true_from,
    outcomes, portfolio, sell_next_open,
)
from trend_start.panel import load_panel  # noqa: E402

STATES = ("Shrink", "Shrink5")
BASE = "MA60"
EXITS = ("MA20", "MA60", "T10", "T20", "T40", "T60",
         "Hot_MA60", "HotVol_MA60", "HotBias_MA60", "Hot_MA20",
         "TP10_MA60", "TP20_MA60", "Trail10_MA60", "Trail15_MA60")
PF_EXITS = ("MA20", "MA60", "T20", "T40", "Hot_MA60", "Hot_MA20", "TP20_MA60", "Trail15_MA60")


def first_close_vs(c: np.ndarray, e: np.ndarray, ref: np.ndarray, *, up: float | None = None,
                   trail: float | None = None) -> np.ndarray:
    """逐笔：首个 j>=e，收盘 >= ref*(1+up)，或收盘 < 自 e 以来最高收盘*(1-trail)。"""
    n = len(c)
    out = np.full(len(e), n, dtype=np.int64)
    for i, (a, r) in enumerate(zip(e, ref)):
        seg = c[a:]
        if up is not None:
            hit = np.flatnonzero(seg >= r * (1 + up))
        else:
            hit = np.flatnonzero(seg < np.maximum.accumulate(seg) * (1 - trail))
        if len(hit):
            out[i] = a + int(hit[0])
    return out


def exit_frame(b: StockBars, flags, e: np.ndarray, vr: np.ndarray, bias: np.ndarray) -> dict[str, dict]:
    n = len(b.c)
    with np.errstate(invalid="ignore"):
        trip60 = np.isfinite(b.ma60) & (b.c < b.ma60)
        trip20 = b.c < b.ma20
        hot_v = vr >= 1.8
        hot_b = bias >= 0.15
    j = {
        "MA20": first_true_from(trip20, e),
        "MA60": first_true_from(trip60, e),
        "hot": first_true_from(hot_v | hot_b, e),
        "hotv": first_true_from(hot_v, e),
        "hotb": first_true_from(hot_b, e),
        "tp10": first_close_vs(b.c, e, b.o[e], up=0.10),
        "tp20": first_close_vs(b.c, e, b.o[e], up=0.20),
        "tr10": first_close_vs(b.c, e, b.o[e], trail=0.10),
        "tr15": first_close_vs(b.c, e, b.o[e], trail=0.15),
    }
    trig = {
        "MA20": j["MA20"], "MA60": j["MA60"],
        **{f"T{h}": np.where(e + h - 1 < n, e + h - 1, n) for h in (10, 20, 40, 60)},
        "Hot_MA60": np.minimum(j["hot"], j["MA60"]),
        "HotVol_MA60": np.minimum(j["hotv"], j["MA60"]),
        "HotBias_MA60": np.minimum(j["hotb"], j["MA60"]),
        "Hot_MA20": np.minimum(j["hot"], j["MA20"]),
        "TP10_MA60": np.minimum(j["tp10"], j["MA60"]),
        "TP20_MA60": np.minimum(j["tp20"], j["MA60"]),
        "Trail10_MA60": np.minimum(j["tr10"], j["MA60"]),
        "Trail15_MA60": np.minimum(j["tr15"], j["MA60"]),
    }
    res = {}
    for name, t in trig.items():
        k, px, at_close, unf, r = sell_next_open(b, t, flags, b.o[e])
        res[name] = {"exit_bar": k, "exit_cal": b.cal[k].astype(np.int32), "exit_px": px,
                     "exit_at_close": at_close, "unfinished": unf, "ret": r, "hold": k - e}
    return res


def summarize(g: pd.DataFrame, v: str, dates) -> dict:
    r = g[f"ret_{v}"].to_numpy()
    hold = g[f"hold_{v}"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    d = r - g[f"ret_{BASE}"].to_numpy()
    month = pd.Series(pd.DatetimeIndex(dates[g.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(d), month)
    return {
        "trades": len(r), "win_rate_pct": len(wins) / len(r) * 100, "mean_ret_pct": r.mean() * 100,
        "median_ret_pct": np.median(r) * 100,
        "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else np.nan,
        "worst_5pct_pct": np.percentile(r, 5) * 100,
        "median_hold": float(np.median(hold)), "mean_hold": float(hold.mean()),
        "ret_per_100d_pct": r.mean() / max(hold.mean() + 1, 1) * 100 * 100,
        "unfinished_pct": g[f"unfinished_{v}"].mean() * 100,
        "vs_ma60_pct": d.mean() * 100, "vs_ma60_lo": lo * 100, "vs_ma60_hi": hi * 100,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit(f"拒绝覆盖已有目录: {args.output}")
    t0 = time.time()
    panel = load_panel(lb.CACHE)
    dates, close = panel.dates, panel.field("close")
    drps = lb.daily_rps(close)
    rows_n = args.limit or len(panel.codes)
    frames = []
    for i in range(rows_n):
        if i % 500 == 0:
            print(f"[STOCK] {i}/{rows_n}  {time.time() - t0:.0f}s", flush=True)
        cols = np.flatnonzero(~np.isnan(close[i]))
        if len(cols) < 260:
            continue
        g = {k: panel.field(f)[i, cols].astype(np.float64)
             for k, f in (("o", "open"), ("h", "high"), ("lo", "low"), ("c", "close"),
                          ("v", "vol"), ("amt", "amount"))}
        g.update(r50=drps["RPS50"][i, cols], r120=drps["RPS120"][i, cols], r250=drps["RPS250"][i, cols])
        lay = lb.daily_layers(g)
        sig, _, bias, vr = es.state_signals(g, lay)
        if not any(sig[s][:-1].any() for s in STATES):
            continue
        b = StockBars(str(panel.codes[i]), cols, dates[cols], g["o"], g["h"], g["lo"], g["c"],
                      lay["_ma20"], ma60=lay["_ma60"])
        buyable = outcomes(b).buyable
        flags = execution_flags(b)
        for st in STATES:
            s = np.flatnonzero(sig[st][:-1])
            s = s[buyable[s + 1]]
            if not len(s):
                continue
            e = s + 1
            f = {"state": st, "code": b.code, "sig_bar": s, "sig_cal": cols[s].astype(np.int32),
                 "entry_bar": e, "entry_cal": cols[e].astype(np.int32), "entry_px": b.o[e],
                 "rps120": g["r120"][s]}
            for name, x in exit_frame(b, flags, e, vr, bias).items():
                for k, arr in x.items():
                    f[f"{k}_{name}"] = arr
            frames.append(pd.DataFrame(f))

    ev = pd.concat(frames, ignore_index=True)
    dt = pd.DatetimeIndex(dates[ev.sig_cal.to_numpy()])
    ev = ev.loc[dt >= lb.IS_START].reset_index(drop=True)
    dt = pd.DatetimeIndex(dates[ev.sig_cal.to_numpy()])
    ev["period"] = np.where(dt <= lb.IS_END, "IS", "OOS_viewed")
    print(f"[EVENTS] {len(ev)}  {time.time() - t0:.0f}s", flush=True)
    args.output.mkdir(parents=True)

    rows = []
    for (st, p), g in ev.groupby(["state", "period"]):
        for v in EXITS:
            rows.append({"state": st, "exit": v, "basis": "every_signal", "period": p, **summarize(g, v, dates)})
    for st, g in ev.groupby("state"):
        for v in EXITS:
            ch = chain(g, v)
            for p in ("IS", "OOS_viewed"):
                t = ch.loc[ch.period == p]
                r = t[f"ret_{v}"]
                rows.append({"state": st, "exit": v, "basis": "chained", "period": p, "trades": len(t),
                             "win_rate_pct": (r > 0).mean() * 100, "mean_ret_pct": r.mean() * 100,
                             "median_hold": float(t[f"hold_{v}"].median()),
                             "sum_ret_pct": r.sum() * 100})
    tab = pd.DataFrame(rows)
    tab.to_csv(args.output / "exit_stats.csv", index=False, encoding="utf-8-sig")

    close_ff = pd.DataFrame(close).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    cal_is = (int(np.searchsorted(dates, np.datetime64(lb.IS_START.date()))),
              int(np.searchsorted(dates, np.datetime64(lb.IS_END.date()), side="right") - 1))
    cal_oos = (int(np.searchsorted(dates, np.datetime64(lb.OOS_START.date()))), len(dates) - 1)
    pf = []
    for st, g in ev.groupby("state"):
        for v in PF_EXITS:
            for p, (a, z) in (("IS", cal_is), ("OOS_viewed", cal_oos)):
                curve, trades = portfolio(g, v, close_ff, code_row, dates, a, z, slots=lb.SLOTS)
                pf.append({"state": st, "exit": v, "period": p, "pf_trades": len(trades), **curve_stats(curve)})
    c = close.astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = c[:, 1:] / c[:, :-1] - 1
    ew = pd.Series(np.cumprod(1 + np.r_[0.0, np.nanmean(r, axis=0)]), index=pd.DatetimeIndex(dates))
    for p, (a, z) in (("IS", cal_is), ("OOS_viewed", cal_oos)):
        pf.append({"state": "EqualWeightA", "exit": "buy_hold", "period": p, **curve_stats(ew.iloc[a:z + 1])})
    pft = pd.DataFrame(pf)
    pft.to_csv(args.output / "portfolio_stats.csv", index=False, encoding="utf-8-sig")

    (args.output / "manifest.json").write_text(json.dumps({
        "script_sha256": lb.sha256(HERE), "entry_state_sha256": lb.sha256(ES_PATH),
        "stocks_used": rows_n, "events": int(len(ev)),
        "limits": ["缺历史退市股，收益偏乐观", "无历史 ST 标记",
                   "Hot 阈值来自看过 2022 后数据的分桶，2022 后只是复盘"],
        "elapsed_s": round(time.time() - t0)}, ensure_ascii=False, indent=2), encoding="utf-8")
    with pd.option_context("display.width", 250, "display.max_rows", 300):
        e = tab.loc[tab.basis == "every_signal"]
        print(e[["state", "exit", "period", "trades", "win_rate_pct", "mean_ret_pct", "worst_5pct_pct",
                 "median_hold", "ret_per_100d_pct", "vs_ma60_pct", "vs_ma60_lo", "vs_ma60_hi"]]
              .round(2).to_string(index=False))
        print(pft[["state", "exit", "period", "cagr_pct", "max_drawdown_pct", "sharpe"]].round(2).to_string(index=False))
    print(f"[DONE] {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
