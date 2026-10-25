"""Pullback-in-uptrend swing miner, abstracted from the six winner weeklies.

Charts show the money was NOT made chasing the breakout week; the operable
entries were pullbacks to the rising weekly MA10/MA20 with volume drying up
WHILE RPS stayed pegged high (trend intact).

Rule (weekly bars, causal at Friday close):
  trend   : RPS120 >= rps_min in >=3 of last 4 weeks; close > MA20w;
            MA10w > MA20w (aligned up)
  pullback: this week's LOW <= MA10w * touch (or MA20w * touch)
            and close still >= near_high x 52w-high
  dry-up  : this week's volume <= vol_ratio x mean(prior 4 weeks)
  env(optional): CSI300 weekly close >= its MA20w
Entry: next trading day OPEN after signal week (no look-ahead).
Exits: fixed 4/8/12 weekly closes; weekly MA10 break.
Part A dissects the 4 named stocks; Part B runs the market-wide grid.
Statistics only, no buy/sell advice. Window: 2024-10-01..2026-08-31.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from research.experiments.exp_weekly_rps import build_weekly, pct_rank_col, rolling_mean
from run_pattern_regime_backtest import ROOT

T0, T1 = pd.Timestamp("2024-10-01"), pd.Timestamp("2026-08-31")
NAMED = {"300308.SZ": "中际旭创", "301080.SZ": "百普赛斯",
         "688072.SH": "拓荆科技", "688143.SH": "长盈通"}

MAINBOARD = ("600", "601", "603", "605", "000", "001", "002", "003")


def st_exclusions(codes: np.ndarray, close: np.ndarray,
                  high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """ST exclusion mask (True = exclude).

    Layer 1 (today's names, from TDX tnf tables): code currently named
    ST/*ST/退 — proxy with documented survivorship limitation.
    Layer 2 (historical evidence): mainboard stocks that closed at a
    +/-5% limit (close==high/low, |pct| in [4.85%, 5.15%]) on >=2 days —
    only ST stocks have a 5% limit on the mainboard, so this catches
    stocks that were ST at some point in the panel window.
    """
    n = len(codes)
    excl = np.zeros(n, dtype=bool)
    names_path = ROOT / "output/monitor/code_names.json"
    if names_path.exists():
        names = json.loads(names_path.read_text(encoding="utf-8"))
        for i, c in enumerate(codes):
            nm = names.get(str(c), "").upper()
            if "ST" in nm or "退" in nm:
                excl[i] = True
        print(f"[ST-L1] 今日名称命中 {int(excl.sum())} 只")
    else:
        print("[ST-L1][WARN] code_names.json 缺失, 名称层未生效")
    pc = close[:, :-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = close[:, 1:] / pc - 1.0
    up5 = (pct >= 0.0485) & (pct <= 0.0515) & (close[:, 1:] >= high[:, 1:] * 0.999)
    dn5 = (pct <= -0.0485) & (pct >= -0.0515) & (close[:, 1:] <= low[:, 1:] * 1.001)
    hits = np.nansum(np.nan_to_num(up5) + np.nan_to_num(dn5), axis=1)
    main = np.array([str(c)[:3] in MAINBOARD for c in codes])
    hist = (hits >= 2) & main
    print(f"[ST-L2] 5%%涨跌停历史命中 {int(hist.sum())} 只 "
          f"(其中非今日ST {int((hist & ~excl).sum())} 只)")
    return excl | hist

GRID = [
    {"ma": 10, "touch": 1.02, "rps_min": 90, "vol_ratio": 0.8,
     "near_high": 0.75, "env": False},
    {"ma": 10, "touch": 1.02, "rps_min": 90, "vol_ratio": 1.01,
     "near_high": 0.75, "env": False},   # 不卡缩量
    {"ma": 20, "touch": 1.03, "rps_min": 90, "vol_ratio": 0.8,
     "near_high": 0.75, "env": False},   # 深一档回踩20周线
    {"ma": 10, "touch": 1.02, "rps_min": 90, "vol_ratio": 0.8,
     "near_high": 0.75, "env": True},    # 加环境闸
    {"ma": 20, "touch": 1.03, "rps_min": 90, "vol_ratio": 0.8,
     "near_high": 0.75, "env": True},
    {"ma": 20, "touch": 1.03, "rps_min": 80, "vol_ratio": 0.9,
     "near_high": 0.70, "env": True},    # 放宽RPS
]


def build_ctx():
    (wc, wv, ma10, ma_max, ma_min, hi52, r120w, r250w, last_day,
     wdates, dates, close, open_, codes, n, nw, m) = build_weekly()
    z = np.load(ROOT / "output/daily_research_cache_20260930/panel.npz",
                mmap_mode="r")
    full_dates = pd.DatetimeIndex(z["dates"])
    keep = full_dates >= pd.Timestamp("2022-06-01")
    low = np.asarray(z["low"][:, keep], dtype=np.float32)
    high = np.asarray(z["high"][:, keep], dtype=np.float32)
    idx_close = np.asarray(z["index_close"], dtype=np.float64)[keep]

    wk = dates.to_period("W-FRI")
    wk_uniq = wk.unique().sort_values()
    pos = {p: i for i, p in enumerate(wk_uniq)}
    wid = np.array([pos[p] for p in wk])
    wl = np.full((n, nw), np.inf, dtype=np.float32)
    np.minimum.at(wl, (slice(None), wid),
                  np.where(np.isfinite(low), low, np.inf))
    wl[np.isinf(wl)] = np.nan

    idx_w = idx_close[last_day]
    idx_ma20 = pd.Series(idx_w).rolling(20).mean().to_numpy()
    env_ok = idx_w >= idx_ma20  # NaN -> False
    ma20 = rolling_mean(wc, 20)
    excl = st_exclusions(codes, close, high, low)
    print(f"[ST] 合计剔除 {int(excl.sum())} / {n} 只")
    # RPS re-ranked on the ST-free universe (ST must not occupy percentiles)
    close_x = close.copy()
    close_x[excl] = np.nan
    for w, j in enumerate(last_day):
        if j >= 250:
            r120w[:, w] = pct_rank_col(close_x[:, j] / close_x[:, j - 120] - 1)
            r250w[:, w] = pct_rank_col(close_x[:, j] / close_x[:, j - 250] - 1)
        elif j >= 120:
            r120w[:, w] = pct_rank_col(close_x[:, j] / close_x[:, j - 120] - 1)
    return dict(wc=wc, wl=wl, wv=wv, ma10=ma10, ma20=ma20, hi52=hi52,
                r120w=r120w, r250w=r250w, last_day=last_day, wdates=wdates,
                dates=dates, close=close, open_=open_, codes=codes,
                idx_w=idx_w, idx_ma20w=idx_ma20, excl=excl,
                n=n, nw=nw, m=m, env_ok=env_ok)


def signals(ctx, p) -> np.ndarray:
    wc, wl, wv = ctx["wc"], ctx["wl"], ctx["wv"]
    ma = ctx["ma10"] if p["ma"] == 10 else ctx["ma20"]
    r = ctx["r120w"]
    # >=3 of last 4 weeks with RPS >= rps_min
    hi = (r >= p["rps_min"]).astype(float)
    cnt = np.cumsum(hi, axis=1)
    c4 = cnt.copy()
    c4[:, 4:] = cnt[:, 4:] - cnt[:, :-4]
    rps_ok = (c4 >= 3) & (r >= p["rps_min"])
    vol_fast_prev = rolling_mean(np.hstack(
        [np.full((ctx["n"], 1), np.nan), wv[:, :-1]]), 4)
    with np.errstate(divide="ignore", invalid="ignore"):
        dry = wv / vol_fast_prev <= p["vol_ratio"]
        nh = wc / ctx["hi52"] >= p["near_high"]
    sig = (
        rps_ok
        & (wc > ctx["ma20"]) & (ctx["ma10"] > ctx["ma20"])
        & (wl <= ma * p["touch"]) & (wc >= ma * 0.97)  # 触及但未有效跌破
        & dry & nh
    )
    sig = np.isfinite(sig.astype(float)) & sig
    sig &= ~ctx["excl"][:, None]  # ST exclusion (ticket 01)
    if p["env"]:
        sig &= ctx["env_ok"][None, :]
    # first occurrence within 4 weeks (回踩允许多次, 但不连周重复)
    first = sig.copy()
    first[:, 1:] = sig[:, 1:] & ~sig[:, :-1].copy()
    for back in range(2, 4):
        first[:, back:] &= ~sig[:, :-back]
    return first


def entries(ctx, sig) -> pd.DataFrame:
    rows = []
    wc, ma10 = ctx["wc"], ctx["ma10"]
    for wi in range(ctx["nw"] - 13):
        wd = ctx["wdates"][wi]
        if wd < T0 or wd > T1:
            continue
        hits = np.where(sig[:, wi])[0]
        if len(hits) == 0:
            continue
        j_sig = ctx["last_day"][wi]
        if j_sig + 1 >= ctx["m"]:
            continue
        e_day = j_sig + 1
        for c in hits:
            e_px = ctx["open_"][c, e_day]
            if not np.isfinite(e_px) or e_px <= 0:
                continue
            rec = {"code": ctx["codes"][c], "sig_week": str(wd.date()),
                   "entry_date": str(ctx["dates"][e_day].date()),
                   "entry_px": float(e_px),
                   "rps120": float(ctx["r120w"][c, wi])}
            for hz in (4, 8, 12):
                if wi + hz < ctx["nw"]:
                    x = ctx["close"][c, ctx["last_day"][wi + hz]]
                    if np.isfinite(x):
                        rec[f"fwd{hz}w"] = float(x / e_px - 1)
            for k in range(wi + 1, min(wi + 26, ctx["nw"])):
                if np.isfinite(ma10[c, k]) and wc[c, k] < ma10[c, k]:
                    x = ctx["close"][c, ctx["last_day"][k]]
                    if np.isfinite(x):
                        rec["ma10_ret"] = float(x / e_px - 1)
                        rec["ma10_weeks"] = k - wi
                    break
            rows.append(rec)
    return pd.DataFrame(rows)


def summarize(tag: str, res: pd.DataFrame) -> None:
    if len(res) < 20:
        print(f"{tag}: n={len(res)} (过少)")
        return
    parts = [f"{tag}: n={len(res)}, {res.sig_week.nunique()}周"]
    for col in ("fwd4w", "fwd8w", "fwd12w", "ma10_ret"):
        g = res[col].dropna()
        parts.append(f"{col} {g.mean() * 100:+.1f}%/"
                     f"win{(g > 0).mean() * 100:.0f}%/"
                     f"med{g.median() * 100:+.1f}%")
    print("  ".join(parts), flush=True)


def main() -> None:
    ctx = build_ctx()

    print("== Part A: 四只提名股的回踩窗口复盘 ==")
    for tag_a, p_a in (
        ("标准(ma20/rps90/缩量0.9)", {"ma": 20, "touch": 1.03, "rps_min": 90,
                                     "vol_ratio": 0.9, "near_high": 0.70,
                                     "env": False}),
        ("放宽(ma20/rps90/不卡量)", {"ma": 20, "touch": 1.03, "rps_min": 90,
                                     "vol_ratio": 9.9, "near_high": 0.65,
                                     "env": False}),
        ("更宽(ma10+ma20/rps85)", {"ma": 10, "touch": 1.04, "rps_min": 85,
                                   "vol_ratio": 9.9, "near_high": 0.60,
                                   "env": False}),
    ):
        print(f"--- {tag_a} ---")
        res_a = entries(ctx, signals(ctx, p_a))
        for code, name in NAMED.items():
            sub = res_a[res_a.code == code].copy()
            if sub.empty:
                print(f"{name}({code}): 无信号")
                continue
            sub["fwd8w"] = (sub["fwd8w"] * 100).round(1)
            sub["fwd12w"] = (sub["fwd12w"] * 100).round(1)
            sub["rps120"] = sub["rps120"].round(0)
            print(f"{name}({code}):")
            print(sub[["sig_week", "entry_date", "entry_px", "rps120",
                       "fwd8w", "fwd12w"]].to_string(index=False))

    print("\n== Part B: 全市场参数网格 ==")
    best, best_key = None, None
    for i, p in enumerate(GRID):
        res = entries(ctx, signals(ctx, p))
        tag = (f"[G{i}] ma{p['ma']} touch{p['touch']} rps>={p['rps_min']} "
               f"vol<={p['vol_ratio']} hi>={p['near_high']} env={p['env']}")
        summarize(tag, res)
        if p["env"] and p["ma"] == 20 and not res.empty:
            best, best_key = res, tag
    if best is not None:
        out = ROOT / "output/monitor/pullback_signals.csv"
        best.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[OUT] {out}  ({best_key})")


if __name__ == "__main__":
    main()
