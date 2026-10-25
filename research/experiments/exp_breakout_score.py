"""Direction 1: score-ranked fill for daily breakout signals (research only).

Weekly score model (score_model_report.md, weights frozen 2026-10-03) migrated
to daily breakout candidates: rank each signal day's candidates by the same
seven features (daily analogues), fill top-N only. Verdict standard: beat the
random-fill median from exp_breakout_fill.py (fixed10 +5.1%, fixed5 +2.1%,
ma20_break +2.9%, trail +1.9%). Sign-flipped score runs as falsification
control. Statistics only; no buy/sell instructions.

Feature definitions (daily analogues of the weekly model):
  close_recover = close / MA20c      (weight -0.23, nearer to MA20 better)
  rps120        = RPS120 from cache  (weight -0.21, 90-95 beat 99-100)
  low_touch     = low / MA20c        (weight -0.14, deeper touch better)
  vol_dry       = vol / MA20(vol)    (weight -0.11, drier better)
  ret52         = close / close[250] (weight -0.10, smaller better)
  trend_age     = consecutive days close > MA20c (weight +0.09)
  ma_align      = MA10c / MA20c      (weight -0.08, less divergence better)
Score = sum(w_i * pct_rank_i) within each signal day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.slot_portfolio import slot_compound

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"
W = {"close_recover": -0.23, "rps120": -0.21, "low_touch": -0.14,
     "vol_dry": -0.11, "ret52": -0.10, "trend_age": 0.09, "ma_align": -0.08}
RAND_MEDIAN = {"fixed5": 2.1, "fixed10": 5.1, "ma20_break": 2.9,
               "trail_s04_t08": 1.9}


def main() -> int:
    z = np.load(SNAPSHOT / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    codes = [str(c) for c in z["codes"]]
    n_days = len(dates)
    w0 = int(dates.searchsorted(pd.Timestamp("2024-09-01")))
    rps = np.load(SNAPSHOT / "rps.npz", allow_pickle=False)["RPS120"]

    close = pd.DataFrame(z["close"].astype(np.float32), index=codes)
    low = pd.DataFrame(z["low"].astype(np.float32), index=codes)
    vol = pd.DataFrame(z["vol"].astype(np.float32), index=codes)
    print("[FEAT] rolling features ...", flush=True)
    ma20 = close.T.rolling(20).mean().T
    ma10 = close.T.rolling(10).mean().T
    vma20 = vol.T.rolling(20).mean().T
    feats = {
        "close_recover": close / ma20,
        "low_touch": low / ma20,
        "ma_align": ma10 / ma20,
        "vol_dry": vol / vma20,
        "ret52": (close.T / close.T.shift(250)).T,
        "rps120": pd.DataFrame(rps, index=codes),
    }
    above = (close > ma20)
    trend_age = above.T.apply(lambda s: s.groupby((~s).cumsum()).cumsum(),
                              ).T.astype(np.float32)
    feats["trend_age"] = trend_age
    print("[FEAT] done", flush=True)

    sim = pd.read_csv(ROOT / "output/breakout_sleeve_20261004/breakout_trades.csv",
                      encoding="utf-8-sig")
    sim = sim[sim.entry_cal >= w0]

    # attach features at (code, entry_cal) via direct numpy indexing (low memory)
    print("[JOIN] features -> trades ...", flush=True)
    row_of = {c: i for i, c in enumerate(codes)}
    ridx = sim["code"].map(row_of).to_numpy()
    cidx = sim["entry_cal"].to_numpy()
    for name, f in feats.items():
        mat = f.to_numpy(dtype=np.float32, copy=False)
        sim[name] = mat[ridx, cidx]
        feats[name] = None  # free
    del close, low, vol, ma20, ma10, vma20, trend_age, above
    sim = sim.dropna(subset=list(W))

    # score within (entry day, exit variant-independent): score per (code, day)
    key = sim[["code", "entry_cal", *W]].drop_duplicates(["code", "entry_cal"])
    for name, wt in W.items():
        key[f"r_{name}"] = key.groupby("entry_cal")[name].rank(pct=True) * wt
    key["score"] = key[[f"r_{k}" for k in W]].sum(axis=1)
    sim = sim.merge(key[["code", "entry_cal", "score"]],
                    on=["code", "entry_cal"], how="left")

    out = ROOT / "output/breakout_score_20261004"
    out.mkdir(parents=True, exist_ok=True)
    sim.to_csv(out / "scored_trades.csv", index=False, encoding="utf-8-sig")

    # per-trade evidence: top5 by score per day vs rest (fixed10 exit)
    print("\n=== 每笔证据（fixed10 退出，按 score 日内分层）===")
    g10 = sim[sim.variant == "fixed10"].copy()
    g10["rank"] = g10.groupby("entry_cal")["score"].rank(ascending=False)
    for label, sub in (("top3", g10[g10["rank"] <= 3]),
                       ("top5", g10[g10["rank"] <= 5]),
                       ("top8", g10[g10["rank"] <= 8]),
                       ("其余", g10[g10["rank"] > 8])):
        print(f"  {label:<5} n={len(sub):>4}  均值 {sub.ret.mean() * 100:+6.2f}%  "
              f"胜率 {(sub.ret > 0).mean() * 100:5.1f}%")
    g10["inv_rank"] = g10.groupby("entry_cal")["score"].rank(ascending=True)
    inv = g10[g10["inv_rank"] <= 5]
    print(f"  反号对照(bottom5) n={len(inv):>4}  均值 {inv.ret.mean() * 100:+6.2f}%")

    # combo: fill top-N per day, 10 slots, per exit variant
    print("\n=== 组合口径（10 槽，日内打分前 N 填充）===")
    print(f"{'variant':<14}{'N':>4}{'total%':>9}{'mdd%':>8}   随机中位参照")
    for v in ("fixed5", "fixed10", "ma20_break", "trail_s04_t08"):
        g = sim[sim.variant == v].copy()
        g["rank"] = g.groupby("entry_cal")["score"].rank(ascending=False)
        for N in (3, 5, 8):
            t = g[g["rank"] <= N].rename(columns={"entry_cal": "sig_cal"})
            r = slot_compound(t[["sig_cal", "code", "ret", "hold"]],
                              slots=10, cal_end=n_days - 1)
            eq = pd.Series(r.equity_by_day).sort_index()
            mdd = float((eq / eq.cummax() - 1).min()) * 100 if len(eq) else 0.0
            print(f"{v:<14}{N:>4}{r.total_ret * 100:>9.1f}{mdd:>8.1f}   "
                  f"随机中位 {RAND_MEDIAN[v]:+.1f}%")

    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
