"""强 RPS 股票在什么状态出手：回踩均线 / 缩量 / 放量突破，以及按乖离和量比分桶。

口径
- 强势池：最近 20 个交易日（含当日）进过日常 L1（复刻 rps_analyzer 实现，见 layer_backtest_20261006）。
- 状态信号（均在池内，当日收盘判定，次日开盘买，成交与退出同 trend_start.engine）：
  Pull20：MA20 五日不降、最低价 <= MA20*1.02、收盘 >= MA20，且前 5 日未触发（首次回踩）
  Pull10：同上换 MA10
  Shrink：收盘 >= MA20 且 当日量 / 20 日均量 <= 0.6，新进入
  Shrink5：收盘 >= MA20 且 5 日均量 / 60 日均量 <= 0.7，新进入
  PullShrink：Pull20 且 当日量 / 20 日均量 <= 0.8
  Breakout：收盘创 20 日收盘新高、量比 >= 1.5、涨幅 >= 3%
  L1_new：基准（进入 L1 当日）
- 两个对照：同日全市场（close>MA20、20 日均额>=1 亿）；同日强势池全体。
- 分桶：池内每个交易日按乖离 c/MA20-1 与量比 v/MA(v,20) 分桶，看 fwd20 / fwd60 超额。
- 阈值事先固定，不在本数据上调参。选参期 2016-09..2021；2022 后已被看过，只是复盘。

限制：股票池缺历史退市股；无历史 ST 标记。
用法: py -3.12 research/entry_state_20261006/run.py --output output/entry_state_20261006/v1
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
_spec = importlib.util.spec_from_file_location(
    "layer_bt", HERE.parents[1] / "layer_backtest_20261006" / "run.py")
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

from trend_start.engine import StockBars, block_bootstrap_mean, chain, curve_stats, outcomes, portfolio  # noqa: E402
from trend_start.panel import load_panel  # noqa: E402

EXITS, FWD = lb.EXITS, lb.FWD
KEYS = (*EXITS, *FWD)
BIAS_BINS = [-np.inf, 0.0, 0.03, 0.06, 0.10, 0.15, np.inf]
VR_BINS = [0.0, 0.5, 0.7, 0.9, 1.2, 1.8, np.inf]


def le(a, b):
    with np.errstate(invalid="ignore"):
        return np.asarray(a <= b, dtype=bool)


def first_in(mask: np.ndarray, k: int) -> np.ndarray:
    prior = lb.roll_sum_partial(lb.lag(mask.astype(float), 1, 0.0), k)
    return mask & (prior == 0)


def state_signals(g: dict, lay: dict) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, np.ndarray]:
    c, lo, v = g["c"], g["lo"], g["v"]
    m10, m20 = lay["_ma10"], lay["_ma20"]
    pool = lb.roll_sum_partial(lay["L1"].astype(float), 20) > 0
    vr = v / lb.ma(v, 20)
    v5_60 = lb.ma(v, 5) / lb.ma(v, 60)
    pct = c / lb.lag(c, 1) - 1
    above20 = lb.ge(c, m20)

    touch20 = pool & lb.ge(m20, lb.lag(m20, 5)) & le(lo, m20 * 1.02) & above20
    touch10 = pool & lb.ge(m10, lb.lag(m10, 5)) & le(lo, m10 * 1.02) & lb.ge(c, m10)
    pull20 = first_in(touch20, 5)
    hh20 = lb.lag(pd.Series(c).rolling(20).max().to_numpy(), 1)
    sig = {
        "L1_new": lb.new_entry(lay["L1"]),
        "Pull20": pull20,
        "Pull10": first_in(touch10, 5),
        "Shrink": lb.new_entry(pool & above20 & le(vr, 0.6)),
        "Shrink5": lb.new_entry(pool & above20 & le(v5_60, 0.7)),
        "PullShrink": pull20 & le(vr, 0.8),
        "Breakout": pool & lb.ge(c, hh20) & lb.ge(vr, 1.5) & lb.ge(pct, 0.03),
    }
    return sig, pool, c / m20 - 1, vr


def excess_stats(t: pd.DataFrame, key: str, ctl: pd.DataFrame, dates) -> dict:
    s = lb.stats(t, key, ctl, dates)
    return {k: s.get(k) for k in ("trades", "win_rate_pct", "mean_ret_pct", "excess_mean_pct",
                                  "excess_ci_lo_pct", "excess_ci_hi_pct", "median_hold")}


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
    print(f"[RPS] {time.time() - t0:.0f}s", flush=True)

    rows_n = args.limit or len(panel.codes)
    events, market, pool_rows = [], [], []
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
        b = StockBars(str(panel.codes[i]), cols, dates[cols], g["o"], g["h"], g["lo"], g["c"],
                      lay["_ma20"], ma60=lay["_ma60"])
        out = outcomes(b)
        sig, pool, bias, vr = state_signals(g, lay)
        for name, s in sig.items():
            ev = lb.event_frame(b, out, s, name, g["r120"])
            if ev is not None:
                events.append(ev)
        for mask, sink in ((lay["_control"], market), (pool, pool_rows)):
            s = np.flatnonzero(mask[:-1])
            s = s[out.buyable[s + 1]]
            if not len(s):
                continue
            d = {"sig_cal": cols[s].astype(np.int32),
                 **{f"ret_{k}": out.ret[k][s + 1].astype(np.float32) for k in KEYS}}
            if sink is pool_rows:
                d.update(bias20=bias[s].astype(np.float32), vr=vr[s].astype(np.float32))
            sink.append(pd.DataFrame(d))

    ev = pd.concat(events, ignore_index=True)
    mk = pd.concat(market, ignore_index=True)
    pl = pd.concat(pool_rows, ignore_index=True)
    rcols = [f"ret_{k}" for k in KEYS]
    ctl_mkt = mk.groupby("sig_cal")[rcols].mean()
    ctl_pool = pl.groupby("sig_cal")[rcols].mean()

    def tag(df: pd.DataFrame) -> pd.DataFrame:
        dt = pd.DatetimeIndex(dates[df.sig_cal.to_numpy()])
        df = df.loc[dt >= lb.IS_START].reset_index(drop=True)
        dt = pd.DatetimeIndex(dates[df.sig_cal.to_numpy()])
        df["period"] = np.where(dt <= lb.IS_END, "IS", "OOS_viewed")
        return df

    ev, pl = tag(ev), tag(pl)
    print(f"[EVENTS] {len(ev)} signals; pool-days {len(pl)}  {time.time() - t0:.0f}s", flush=True)
    args.output.mkdir(parents=True)

    rows = []
    for name, g in ev.groupby("strategy"):
        for v in EXITS:
            ch = chain(g, v)
            for p in ("IS", "OOS_viewed"):
                t = ch.loc[ch.period == p]
                a, z = excess_stats(t, v, ctl_mkt, dates), excess_stats(t, v, ctl_pool, dates)
                rows.append({"state": name, "exit": v, "basis": "chained", "period": p, **a,
                             "vs_pool_pct": z["excess_mean_pct"], "vs_pool_lo": z["excess_ci_lo_pct"],
                             "vs_pool_hi": z["excess_ci_hi_pct"]})
        for k in FWD:
            for p in ("IS", "OOS_viewed"):
                t = g.loc[g.period == p]
                a, z = excess_stats(t, k, ctl_mkt, dates), excess_stats(t, k, ctl_pool, dates)
                rows.append({"state": name, "exit": k, "basis": "every_signal", "period": p, **a,
                             "vs_pool_pct": z["excess_mean_pct"], "vs_pool_lo": z["excess_ci_lo_pct"],
                             "vs_pool_hi": z["excess_ci_hi_pct"]})
    st = pd.DataFrame(rows)
    st.to_csv(args.output / "state_stats.csv", index=False, encoding="utf-8-sig")

    pl["bias_bin"] = pd.cut(pl.bias20, BIAS_BINS, right=False).astype(str)
    pl["vr_bin"] = pd.cut(pl.vr, VR_BINS, right=False).astype(str)
    month = pd.DatetimeIndex(dates[pl.sig_cal.to_numpy()]).to_period("M").astype(str)
    pl["month"] = month
    bucket_rows = []
    for dim in ("bias_bin", "vr_bin"):
        for (p, bkt), g in pl.groupby(["period", dim]):
            row = {"dim": dim, "bucket": bkt, "period": p, "days": len(g)}
            for k in ("fwd20", "fwd60", "open"):
                x = g[f"ret_{k}"].to_numpy(np.float64) - ctl_pool[f"ret_{k}"].reindex(g.sig_cal).to_numpy()
                ok = np.isfinite(x)
                lo, hi = block_bootstrap_mean(pd.Series(x[ok]), pd.Series(g.month.to_numpy()[ok]))
                row.update({f"{k}_vs_pool_pct": np.nanmean(x) * 100, f"{k}_lo": lo * 100, f"{k}_hi": hi * 100})
            bucket_rows.append(row)
    bk = pd.DataFrame(bucket_rows)
    bk.to_csv(args.output / "bucket_stats.csv", index=False, encoding="utf-8-sig")

    close_ff = pd.DataFrame(close).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    cal_is = (int(np.searchsorted(dates, np.datetime64(lb.IS_START.date()))),
              int(np.searchsorted(dates, np.datetime64(lb.IS_END.date()), side="right") - 1))
    cal_oos = (int(np.searchsorted(dates, np.datetime64(lb.OOS_START.date()))), len(dates) - 1)
    pf = []
    for name, g in ev.groupby("strategy"):
        for v in lb.PF_EXITS:
            for p, (a, z) in (("IS", cal_is), ("OOS_viewed", cal_oos)):
                curve, trades = portfolio(g, v, close_ff, code_row, dates, a, z, slots=lb.SLOTS)
                pf.append({"state": name, "exit": v, "period": p, "pf_trades": len(trades), **curve_stats(curve)})
    pd.DataFrame(pf).to_csv(args.output / "portfolio_stats.csv", index=False, encoding="utf-8-sig")

    (args.output / "manifest.json").write_text(json.dumps({
        "script_sha256": lb.sha256(HERE), "layer_module_sha256": lb.sha256(Path(_spec.origin)),
        "stocks_used": rows_n, "signals": int(len(ev)), "pool_days": int(len(pl)),
        "limits": ["缺历史退市股，收益偏乐观", "无历史 ST 标记", "2022 后已被查看，只是复盘",
                   "分桶样本按日重叠，置信区间按月块自助法，仍偏窄"],
        "elapsed_s": round(time.time() - t0)}, ensure_ascii=False, indent=2), encoding="utf-8")

    with pd.option_context("display.width", 250, "display.max_rows", 300):
        print(st.loc[st.exit.isin(["open", "ma60", "fwd20", "fwd60"]),
                     ["state", "exit", "period", "trades", "win_rate_pct", "mean_ret_pct", "excess_mean_pct",
                      "excess_ci_lo_pct", "excess_ci_hi_pct", "vs_pool_pct", "vs_pool_lo", "vs_pool_hi",
                      "median_hold"]].round(2).to_string(index=False))
        print(bk.round(2).to_string(index=False))
        print(pd.DataFrame(pf)[["state", "exit", "period", "cagr_pct", "max_drawdown_pct", "sharpe"]]
              .round(2).to_string(index=False))
    print(f"[DONE] {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
