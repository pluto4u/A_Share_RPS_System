"""Direction 3b: score-ranked compression inside weak-env weeks (research only).

Finding 2026-10-04: weekly pullback candidates in daily-weak weeks (CSI300
close < MA60) earn fwd8w +15.2%/70% (n=484, 10 weeks, 9/10 positive) vs
-0.6%/40% in strong weeks. But weak weeks emit 14~104 candidates — beyond
manual capacity. Test: does the frozen weekly score compress each weak week
to top3/top5 without losing the edge? Statistics only; polluted window.
"""

from __future__ import annotations

import pandas as pd
import weekly_pullback_scan as wps
from run_pattern_regime_backtest import ROOT

SNAPSHOT = ROOT / "output/daily_research_cache_20260930"
wps.LOOKBACK_DAYS = 100000  # full history: match the original signal generation
WEAK_WEEKS = ["2025-01-03", "2025-01-17", "2025-01-24", "2025-11-21",
              "2025-11-28", "2025-12-12", "2025-12-19", "2026-03-06",
              "2026-03-13", "2026-04-10"]


def main() -> int:
    sig = pd.read_csv(ROOT / "output/monitor/pullback_signals.csv",
                      encoding="utf-8-sig")
    sig["sig_week"] = pd.to_datetime(sig["sig_week"]).dt.date.astype(str)
    weak = sig[sig.sig_week.isin(WEAK_WEEKS)].dropna(subset=["fwd8w"])
    print(f"弱周候选 {len(weak)} 行 / {weak.sig_week.nunique()} 周; "
          f"全池 fwd8w {weak.fwd8w.mean() * 100:+.2f}%")

    scored = []
    for wk in WEAK_WEEKS:
        df = wps.scan(SNAPSHOT, as_of=pd.Timestamp(wk))
        if df.empty:
            print(f"  [{wk}] 扫描为空（600日切片覆盖不足？）")
            continue
        df["sig_week"] = wk
        scored.append(df[["code", "sig_week", "score", "rps120", "amt20_亿"]])
        print(f"  [{wk}] 扫描 {len(df)} 只", flush=True)
    sc = pd.concat(scored, ignore_index=True)

    m = weak.merge(sc, on=["code", "sig_week"], how="left", suffixes=("", "_s"))
    cov = m.score.notna().mean()
    print(f"\n打分覆盖率 {cov * 100:.0f}%（历史信号与扫描重建的交集）")

    m["rank"] = m.groupby("sig_week")["score"].rank(ascending=False)
    print("\n=== 弱周内打分压缩（fwd8w）===")
    for N in (3, 5, 8):
        g = m[m["rank"] <= N]
        print(f"  top{N}: n={len(g):>3}  均值 {g.fwd8w.mean() * 100:+6.2f}%  "
              f"胜率 {(g.fwd8w > 0).mean() * 100:5.1f}%  "
              f"中位 {g.fwd8w.median() * 100:+6.2f}%")
    g = m[m["rank"] > 8]
    print(f"  其余 : n={len(g):>3}  均值 {g.fwd8w.mean() * 100:+6.2f}%  "
          f"胜率 {(g.fwd8w > 0).mean() * 100:5.1f}%")

    # per-week top5 detail
    print("\n=== 逐周 top5 fwd8w% ===")
    t = (m[m["rank"] <= 5].groupby("sig_week").fwd8w
         .agg(["count", "mean", "median"]))
    print(t.assign(mean=(t["mean"] * 100).round(1),
                   median=(t["median"] * 100).round(1)).to_string())

    out = ROOT / "output/weak_env_score_20261004"
    out.mkdir(parents=True, exist_ok=True)
    m.to_csv(out / "weak_week_scored.csv", index=False, encoding="utf-8-sig")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
