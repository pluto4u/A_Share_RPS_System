"""Weekly leader-pullback forward scanner (statistics only; no advice).

Canonical rule (spec .scratch/pullback-swing-selector/spec.md):
  pool  : RPS120>=90 (>=3 of last 4w) + close>MA20w + MA10w>MA20w
          + week-low <= MA20w*1.03 + close >= MA20w*0.97
          + close/52w-high >= 0.70 + 52w gain <= 150% + ST excluded
          + first occurrence within 4 weeks
  rank  : IC-weighted score within the week's pool (post-ST weights)
  output: candidates of the LATEST completed week, entry = next trading
          day open (forward walk-forward record, no look-ahead).

Data: the monitor's live panel cache (output/monitor/cache), rebuilt by
monitor_daily.py from local TDX. Memory-lean: mmap + last 600 sessions.
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
from pathlib import Path

import numpy as np
import pandas as pd
from research.experiments.exp_pullback import st_exclusions
from research.experiments.exp_weekly_rps import pct_rank_col, rolling_mean
from run_pattern_regime_backtest import ROOT

MON_CACHE = PROJECT_ROOT / "output" / "monitor" / "cache"
LOOKBACK_DAYS = 600
WEIGHTS = {"close_recover": -0.23, "rps120": -0.21, "low_touch": -0.14,
           "vol_dry": -0.11, "ret52": -0.10, "trend_age": 0.09,
           "ma_align": -0.08}


def rank_pct_small(x: np.ndarray) -> np.ndarray:
    """0..1 rank for small pools (n>=5), NaN-preserving."""
    out = np.full(len(x), np.nan)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n < 5:
        return out
    order = np.argsort(x[ok], kind="stable")
    r = np.empty(n)
    r[order] = np.arange(n)
    out[ok] = r / (n - 1) if n > 1 else 0.5
    return out


def scan(cache: Path = MON_CACHE, as_of: pd.Timestamp | None = None
         ) -> pd.DataFrame:
    z = np.load(cache / "panel.npz", mmap_mode="r")
    dates = pd.DatetimeIndex(z["dates"])
    dates = dates[-LOOKBACK_DAYS:]
    close = np.asarray(z["close"][:, -LOOKBACK_DAYS:], dtype=np.float32)
    high = np.asarray(z["high"][:, -LOOKBACK_DAYS:], dtype=np.float32)
    low = np.asarray(z["low"][:, -LOOKBACK_DAYS:], dtype=np.float32)
    amount = np.asarray(z["amount"][:, -LOOKBACK_DAYS:], dtype=np.float32)
    codes = np.array([str(c) for c in z["codes"]])
    n = len(codes)

    excl = st_exclusions(codes, close, high, low)

    wk = dates.to_period("W-FRI")
    wk_uniq = wk.unique().sort_values()
    pos = {p: i for i, p in enumerate(wk_uniq)}
    wid = np.array([pos[p] for p in wk])
    nw = len(wk_uniq)
    last_day = np.full(nw, -1)
    for j, w in enumerate(wid):
        last_day[w] = j
    wdates = dates[last_day]
    wc = close[:, last_day]
    wl = np.full((n, nw), np.inf, dtype=np.float32)
    np.minimum.at(wl, (slice(None), wid),
                  np.where(np.isfinite(low), low, np.inf))
    wl[np.isinf(wl)] = np.nan
    wv = np.zeros((n, nw), dtype=np.float64)
    vol = np.asarray(z["vol"][:, -LOOKBACK_DAYS:], dtype=np.float32)
    np.add.at(wv, (slice(None), wid), np.where(np.isfinite(vol), vol, 0.0))

    ma10 = rolling_mean(wc, 10)
    ma20 = rolling_mean(wc, 20)
    hi52 = np.full_like(wc, np.nan)
    lo52 = np.full_like(wc, np.nan)
    for w in range(51, nw):
        win = wc[:, w - 51:w + 1]
        ok = np.isfinite(win).all(axis=1)
        hi52[ok, w] = win[ok].max(axis=1)
        lo52[ok, w] = win[ok].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret52 = wc / lo52 - 1.0
    above = np.isfinite(ma20) & (wc > ma20)
    runlen = np.zeros_like(wc, dtype=float)
    for w in range(1, nw):
        runlen[:, w] = np.where(above[:, w], runlen[:, w - 1] + 1, 0)

    # RPS on ST-free universe
    close_x = close.copy()
    close_x[excl] = np.nan
    r120w = np.full((n, nw), np.nan)
    for w, j in enumerate(last_day):
        if j >= 120:
            with np.errstate(divide="ignore", invalid="ignore"):
                r120w[:, w] = pct_rank_col(close_x[:, j]
                                           / close_x[:, j - 120] - 1)
    hi90 = (r120w >= 90).astype(float)
    cnt = np.cumsum(hi90, axis=1)
    peg = cnt.copy()
    peg[:, 4:] = cnt[:, 4:] - cnt[:, :-4]
    prev = np.hstack([np.full((n, 1), np.nan), wv[:, :-1]])
    c4 = np.nancumsum(np.where(np.isfinite(prev), prev, 0.0), axis=1)
    k4 = np.cumsum(np.isfinite(prev), axis=1)
    prev4 = np.full_like(wv, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev4[:, 4:] = (c4[:, 4:] - c4[:, :-4]) / (k4[:, 4:] - k4[:, :-4])

    with np.errstate(divide="ignore", invalid="ignore"):
        sig = (
            (r120w >= 90) & (peg >= 3)
            & (wc > ma20) & (ma10 > ma20)
            & (wl <= ma20 * 1.03) & (wc >= ma20 * 0.97)
            & (wc / hi52 >= 0.70) & (ret52 <= 1.5)
        )
    sig = np.isfinite(sig.astype(float)) & sig & ~excl[:, None]
    first = sig.copy()
    first[:, 1:] = sig[:, 1:] & ~sig[:, :-1].copy()
    for back in range(2, 4):
        first[:, back:] &= ~sig[:, :-back]

    wi = nw - 1  # latest completed week
    if as_of is not None:
        cand = [w for w in range(nw) if wdates[w] <= as_of]
        if not cand:
            return pd.DataFrame()
        wi = cand[-1]
    wd = wdates[wi]
    hits = np.where(first[:, wi])[0]

    # environment tier at signal week: index close < daily MA60 (facts up to wd)
    env_weak: bool | None = None
    if "index_close" in z:
        ic = pd.Series(np.asarray(z["index_close"], dtype=float)[-len(dates):],
                       index=dates).ffill()
        ma60 = ic.rolling(60).mean()
        v = ma60.iloc[last_day[wi]]
        if np.isfinite(v):
            env_weak = bool(ic.iloc[last_day[wi]] < v)

    names = {}
    names_path = ROOT / "output/monitor/code_names.json"
    if names_path.exists():
        names = json.loads(names_path.read_text(encoding="utf-8"))

    with np.errstate(divide="ignore", invalid="ignore"):
        dims = {"rps120": r120w[hits, wi],
                "close_recover": wc[hits, wi] / ma20[hits, wi] - 1,
                "low_touch": wl[hits, wi] / ma20[hits, wi] - 1,
                "vol_dry": wv[hits, wi] / prev4[hits, wi],
                "ret52": ret52[hits, wi],
                "trend_age": runlen[hits, wi],
                "ma_align": ma10[hits, wi] / ma20[hits, wi] - 1}
    score = np.zeros(len(hits))
    for d, wgt in WEIGHTS.items():
        r = rank_pct_small(dims[d])
        score += wgt * np.where(np.isfinite(r), r, 0.5)  # 缺失按中性
    amt20 = np.nanmean(amount[hits][:, -20:], axis=1)

    df = pd.DataFrame({
        "code": codes[hits],
        "name": [names.get(c, "") for c in codes[hits]],
        "sig_week": str(wd.date()),
        "close": wc[hits, wi],
        "rps120": np.round(r120w[hits, wi], 1),
        "ret52_pct": np.round(ret52[hits, wi] * 100, 1),
        "trend_age_w": runlen[hits, wi].astype(int),
        "dist_ma20w_pct": np.round(dims["close_recover"] * 100, 2),
        "vol_dry": np.round(dims["vol_dry"], 2),
        "amt20_亿": np.round(amt20 / 1e8, 1),
        "score": np.round(score, 3),
        "env_weak": env_weak,
    })
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def main() -> None:
    df = scan()
    out = ROOT / "output/monitor/weekly_forward.csv"
    if df.empty:
        print("[SCAN] 最新一周无候选")
        return
    week = str(df.sig_week.iloc[0])
    if out.exists():
        old = pd.read_csv(out, encoding="utf-8-sig")
        if week in set(old.sig_week.astype(str)):
            # week already registered: only backfill env_weak (a fact about
            # that Friday), never touch scores or membership
            old.loc[old.sig_week.astype(str) == week, "env_weak"] = \
                df.env_weak.iloc[0]
            old.to_csv(out, index=False, encoding="utf-8-sig")
            print(f"[SCAN] {week} 已登记，仅回填 env_weak="
                  f"{df.env_weak.iloc[0]}")
            return
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[SCAN] 信号周 {week}, 候选 {len(df)} 只 -> {out}")
    print(df.head(25).to_string(index=False))


if __name__ == "__main__":
    main()
