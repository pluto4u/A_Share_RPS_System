"""放量因子能否让“右侧趋势预警（YJ）”更早、更准？事件研究（2026-10-25 运行前登记，运行后不调参）。

数据已被看过，结论是描述性比较，不是独立验证。信号 t 日收盘后形成，t+1 开盘进入；开盘即涨停的剔除。
基线：YJ1（用户公式原样）、YJ2（V2：高点在低点前、20日均成交额≥1亿；与 backtest_siruipu_right_side.py 的 V2 一致）、QD2（已有放量启动，对照）。
候选：YJ2 + 六个放量因子之一（trend_start.right_trend.VOLUME_FACTORS）。
每个变体、每只股票 20 个交易日内只记第一次信号。
时期：设计期 2018-01～2021-12 选；压力期 2022-01～2024-01；近期 2024-02 起，各只看一次。
指标（持有 h 个交易日到收盘，减同期同流动性五分组等权平均）：
  excess10/20/40；成功率＝入场后 40 日内收盘先涨到 +20%（早于先跌到 −8%）；
  离低点涨幅＝入场价/30日最低价−1（越小越“早”）；
  确认率＝20 日内出现 QD2/QR2/QQR2 的比例，领先天数＝信号到首次确认的中位数。
选择规则（预先声明）：候选里设计期样本≥100 且 excess20 的 Newey-West t 最大者。
成本未扣除（来回约 0.3%）；比较的是变体之间的差别。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = next(p for p in Path(__file__).absolute().parents if (p / "rps_analyzer").is_dir())
sys.path[:0] = [str(ROOT), str(ROOT / "skills/a-share-rotation-trader/scripts"), str(ROOT / "scripts/daily")]
from run_daily_rps import names_from_tnf, read_day  # noqa: E402
from trend_start.daily_board import build_recent_panel  # noqa: E402
from trend_start.icepoint import limit_matrix  # noqa: E402
from trend_start.right_trend import VOLUME_FACTORS, VF_LABELS, compute, fill_suspensions, roll, shift  # noqa: E402

PERIODS = {"design_2018-2021": ("2018-01-01", "2021-12-31"), "stress_2022-2024.01": ("2022-01-01", "2024-01-31"),
           "recent_2024.02-": ("2024-02-01", "2026-12-31")}
HORIZONS = (10, 20, 40)
GAP, TARGET, STOP, SUCC_WIN, CONF_WIN = 20, 0.20, 0.08, 40, 20


def nw_t(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 15:
        return np.nan
    e = x - x.mean()
    v = e @ e / len(x)
    for k in range(1, min(lag, len(x) - 1) + 1):
        v += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / len(x)
    return float(x.mean() / np.sqrt(v / len(x))) if v > 0 else np.nan


def first_signal_only(sig: np.ndarray, gap: int = GAP) -> np.ndarray:
    prior = shift(roll(sig.astype(np.float32), gap, "max", min_periods=1), 1, 0.0) > 0
    return sig & ~prior


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default="2026-10-23")
    ap.add_argument("--sessions", type=int, default=2500)
    ap.add_argument("--tdx-root", type=Path, default=Path("C:/new_tdx"))
    ap.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    args = ap.parse_args()
    panel, audit = build_recent_panel(args.tdx_root, pd.Timestamp(args.as_of), sessions=args.sessions)
    dates = pd.DatetimeIndex(panel.dates)
    codes = panel.codes.astype(str)
    names = names_from_tnf(args.tdx_root / "T0002/hq_cache")
    st = np.array(["ST" in names.get(c, "").upper() or "退" in names.get(c, "") for c in codes])
    n, m = len(codes), len(dates)
    print(f"[PANEL] {n} x {m} {dates[0].date()}..{dates[-1].date()}", flush=True)
    rt = compute(panel, st)
    d = fill_suspensions(panel)
    O = panel.field("open").astype(np.float32)
    C, L = d["close"], d["low"]
    llv30 = roll(L, 30, "min")
    lim = (limit_matrix(panel.codes, panel.dates) - 0.005).astype(np.float32)
    adv20 = rt.aux["adv20"]
    base = rt.aux["base"]
    confirm = rt.confirm()
    print("[SIGNAL] counts raw:", {k: int(getattr(rt, k).sum()) for k in ("yj1", "yj2", "qd2", "qr2", "qqr2")}, flush=True)

    variants = {"YJ1": rt.yj1, "YJ2": rt.yj2, "QD2": rt.qd2}
    for k in VOLUME_FACTORS:
        variants["YJ2+" + k] = rt.yj2 & rt.vf[k]
    t0 = int(dates.searchsorted(pd.Timestamp("2018-01-01")))
    # CONTROL：同期随机抽样的流动性合格股票（每 10 个交易日抽 150 只，固定种子），给出“什么都不筛”的基准成功率
    rng = np.random.default_rng(7)
    ctrl = np.zeros((n, m), bool)
    for t in range(t0, m - 1, 10):
        pool_ = np.flatnonzero(base[:, t] & (adv20[:, t] >= 1e8))
        if len(pool_) > 150:
            ctrl[rng.choice(pool_, 150, replace=False), t] = True
    variants["CONTROL"] = ctrl
    events = []
    for name, sig in variants.items():
        ev = sig.copy() if name == "CONTROL" else first_signal_only(sig)
        ev[:, :t0] = False
        ii, tt = np.nonzero(ev)
        keep = (tt + 1 < m)
        ii, tt = ii[keep], tt[keep]
        ok = np.isfinite(O[ii, tt + 1]) & ~((O[ii, tt + 1] / C[ii, tt] - 1) >= lim[ii, tt])
        events.append(pd.DataFrame({"variant": name, "i": ii[ok], "t": tt[ok]}))
    ev = pd.concat(events, ignore_index=True)
    print(f"[EVENTS] {len(ev)}", flush=True)

    # same-liquidity benchmark per signal date
    quint = {}
    bench = {h: {} for h in HORIZONS}
    for t in np.unique(ev.t.to_numpy()):
        okm = base[:, t] & np.isfinite(adv20[:, t])
        pr = pd.Series(adv20[okm, t]).rank(pct=True).to_numpy()
        q = np.full(n, -1)
        q[okm] = np.minimum((pr * 5 - 1e-9).astype(int), 4)
        quint[t] = q
        for h in HORIZONS:
            if t + 1 + h > m - 1:
                continue
            with np.errstate(invalid="ignore", divide="ignore"):
                r = C[:, t + 1 + h] / O[:, t + 1] - 1
            b = np.full(5, np.nan)
            for k in range(5):
                sel = (q == k) & np.isfinite(r)
                if sel.sum() >= 20:
                    b[k] = r[sel].mean()
            bench[h][t] = b
    ii, tt = ev.i.to_numpy(), ev.t.to_numpy()
    entry = O[ii, tt + 1]
    for h in HORIZONS:
        col = np.full(len(ev), np.nan)
        for j in range(len(ev)):
            t, i = tt[j], ii[j]
            b, q = bench[h].get(t), quint[t][i]
            if b is not None and q >= 0 and t + 1 + h <= m - 1:
                col[j] = (C[i, t + 1 + h] / entry[j] - 1 - b[q]) * 100
        ev[f"ex{h}"] = col
    # success within 40 days (+20% before -8%), off-low, confirmation
    succ, off_low, lead = np.full(len(ev), np.nan), np.full(len(ev), np.nan), np.full(len(ev), np.nan)
    for j in range(len(ev)):
        t, i = tt[j], ii[j]
        off_low[j] = (entry[j] / llv30[i, t] - 1) * 100 if llv30[i, t] > 0 else np.nan
        if t + 1 + SUCC_WIN <= m - 1:
            w = C[i, t + 1:t + 1 + SUCC_WIN] / entry[j] - 1
            up, dn = np.flatnonzero(w >= TARGET), np.flatnonzero(w <= -STOP)
            succ[j] = float(len(up) > 0 and (len(dn) == 0 or up[0] < dn[0]))
        if t + CONF_WIN <= m - 1:
            hit = np.flatnonzero(confirm[i, t + 1:t + 1 + CONF_WIN])
            lead[j] = (hit[0] + 1) if len(hit) else 0
    ev["success"], ev["off_low"], ev["lead"] = succ, off_low, lead
    ev["date"] = dates[tt]
    ev["code"] = codes[ii]
    # 市场状态（探索性切分）：上证与等权指数都在 60 日线上且 20 日线在 60 日线上；近 10 日内出现过冰点日
    traded = d["traded"]
    with np.errstate(invalid="ignore", divide="ignore"):
        chg = C / shift(C, 1) - 1
    okr = traded & np.isfinite(shift(C, 1))
    ew = pd.Series(np.cumprod(1 + np.nan_to_num(np.nanmean(np.where(okr & (np.abs(chg) < .25), chg, np.nan), axis=0))), index=dates)
    sse = pd.Series(read_day(args.tdx_root / "vipdoc/sh/lday/sh000001.day").set_index("trade_date").close).reindex(dates)

    def trend_ok(s):
        return ((s > s.rolling(60).mean()) & (s.rolling(20).mean() > s.rolling(60).mean())).to_numpy()
    gate = trend_ok(sse) & trend_ok(ew)
    adv_share = (np.where(okr, chg > 0, False).sum(axis=0) / np.maximum(okr.sum(axis=0), 1))
    ice = adv_share <= 0.22
    after_ice = pd.Series(ice).rolling(10, min_periods=1).max().to_numpy() > 0
    ev["gate"], ev["after_ice"] = gate[ev.t.to_numpy()], after_ice[ev.t.to_numpy()]
    ev["period"] = [next((p for p, (a, b) in PERIODS.items() if pd.Timestamp(a) <= d_ <= pd.Timestamp(b)), None) for d_ in ev.date]
    ev = ev.dropna(subset=["period"])
    # whether the YJ2 event also had each volume factor (for the with/without split)
    for k in VOLUME_FACTORS:
        ev[k] = rt.vf[k][ev.i.to_numpy(), ev.t.to_numpy()]

    def summarize(g: pd.DataFrame) -> pd.Series:
        daily = g.groupby("date").ex20.mean()
        has_lead = g.lead.dropna()
        return pd.Series({"events": len(g), "stocks": g.code.nunique(), "ex10": g.ex10.mean(), "ex20": g.ex20.mean(),
                          "ex20_median": g.ex20.median(), "ex20_win%": (g.ex20 > 0).mean() * 100, "ex40": g.ex40.mean(),
                          "nw_t_ex20": nw_t(daily.sort_index().to_numpy(), 4), "success%": g.success.mean() * 100,
                          "off_low_median%": g.off_low.median(), "confirm%": (has_lead > 0).mean() * 100 if len(has_lead) else np.nan,
                          "lead_days_median": has_lead[has_lead > 0].median() if (has_lead > 0).any() else np.nan})

    summ = ev.groupby(["variant", "period"]).apply(summarize, include_groups=False).reset_index()
    split = []
    y2 = ev[ev.variant == "YJ2"]
    for k in VOLUME_FACTORS:
        for p, g in y2.groupby("period"):
            for flag, gg in g.groupby(k):
                s = summarize(gg); s["variant"], s["period"], s["factor"], s["has_factor"] = "YJ2", p, k, bool(flag)
                split.append(s)
    split = pd.DataFrame(split)
    regime = []
    for name in ("CONTROL", "YJ2", "YJ2+vf4_updown", "QD2"):
        for col in ("gate", "after_ice"):
            for (p, flag), g in ev[ev.variant == name].groupby(["period", col]):
                s = summarize(g); s["variant"], s["period"], s["split"], s["on"] = name, p, col, bool(flag)
                regime.append(s)
    regime = pd.DataFrame(regime)
    design = summ[(summ.period == "design_2018-2021") & summ.variant.str.startswith("YJ2+") & (summ.events >= 100)]
    pick = design.sort_values("nw_t_ex20", ascending=False).iloc[0]["variant"] if len(design) else None
    yearly = ev.assign(year=ev.date.dt.year).pivot_table(index="variant", columns="year", values="ex20", aggfunc="mean")
    args.output.mkdir(parents=True, exist_ok=True)
    summ.to_csv(args.output / "summary.csv", index=False, encoding="utf-8-sig")
    regime.to_csv(args.output / "regime_split.csv", index=False, encoding="utf-8-sig")
    split.to_csv(args.output / "yj2_with_vs_without_factor.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(args.output / "yearly_ex20.csv", encoding="utf-8-sig")
    ev.drop(columns=list(VOLUME_FACTORS)).to_csv(args.output / "events.csv.gz", index=False, encoding="utf-8-sig")
    (args.output / "audit.json").write_text(json.dumps({**audit, "pre_declared_pick": pick, "factors": VF_LABELS}, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    order = ["YJ1", "YJ2", "QD2"] + ["YJ2+" + k for k in VOLUME_FACTORS]
    for p in PERIODS:
        print(f"\n== {p}")
        print(summ[summ.period == p].set_index("variant").loc[[v for v in order if v in set(summ[summ.period == p].variant)]].round(2).drop(columns="period").to_string())
    print("\n== 市场状态切分（gate=指数趋势向上；after_ice=近10日有冰点日）")
    print(regime[["variant", "period", "split", "on", "events", "ex20", "nw_t_ex20", "success%", "ex20_win%"]].round(2).sort_values(["variant", "split", "period", "on"]).to_string(index=False))
    print("\n== YJ2 内部：有/无该放量因子（设计期与其余时期）")
    print(split[["period", "factor", "has_factor", "events", "ex20", "nw_t_ex20", "success%", "off_low_median%"]].round(2).to_string(index=False))
    print("\nPRE-DECLARED PICK:", pick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
