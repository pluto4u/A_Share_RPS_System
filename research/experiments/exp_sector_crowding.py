"""Sector crowding from local TDX industry classification (research only).

Maps each panel stock to its TDX level-2 industry (tdxhy.cfg + incon.dat,
GBK), aggregates daily amount by sector, computes:
  amount_share   = sector amount / total market amount
  share_pct250   = percentile of today's share vs trailing 250 days
  ret5 / ret20   = sector equal-weight return proxies (member median)
Caveat: classification is as-of tdxhy.cfg (current membership applied to
history); acceptable for monitoring, mild look-ahead for backtests.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from run_pattern_regime_backtest import ROOT
from trend_start.panel import load_panel

TDX_HY = Path(r"C:/new_tdx/T0002/hq_cache/tdxhy.cfg")
TDX_NAMES = Path(r"C:/new_tdx/incon.dat")


def load_industry() -> tuple[dict[str, str], dict[str, str]]:
    """Return (code -> T-level2 code, T-code -> name)."""
    names: dict[str, str] = {}
    for line in TDX_NAMES.read_bytes().decode("gbk", errors="ignore").splitlines():
        if "|" in line and line.startswith("T"):
            k, _, v = line.partition("|")
            names[k.strip()] = v.strip()
    code2hy: dict[str, str] = {}
    for line in TDX_HY.read_text(encoding="gbk", errors="ignore").splitlines():
        p = line.split("|")
        if len(p) >= 3 and p[2].startswith("T") and len(p[2]) >= 5:
            code2hy[p[1]] = p[2][:5]  # level-2, e.g. T1102
    return code2hy, names


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; default latest")
    args = ap.parse_args()

    panel = load_panel(args.snapshot)
    dates = pd.DatetimeIndex(panel.dates)
    close = panel.field("close").astype(float)
    amount = panel.field("amount").astype(float)
    codes = [str(c) for c in panel.codes]

    code2hy, names = load_industry()
    hy = pd.Series([code2hy.get(c.split(".")[0]) for c in codes], index=codes)
    hy = hy[hy.notna()]
    print(f"[MAP] {len(hy)}/{len(codes)} stocks mapped to "
          f"{hy.nunique()} level-2 sectors")

    amt = pd.DataFrame(amount, index=codes)
    sec_amt = amt.loc[hy.index].groupby(hy.values).sum()
    share = sec_amt.div(sec_amt.sum(axis=0), axis=1)
    pct = share.T.rolling(250, min_periods=60).apply(
        lambda x: (x.iloc[-1] >= x).mean(), raw=False).T

    ret5 = pd.DataFrame(close, index=codes).loc[hy.index].pct_change(
        5, axis=1)
    sec_ret5 = ret5.groupby(hy.values).median()

    d = pd.Timestamp(args.date) if args.date else dates[-1]
    di = int(dates.searchsorted(d))
    print(f"[DATE] {dates[di].date()}")

    tab = pd.DataFrame({
        "sector": [names.get(s, s) for s in share.index],
        "amt_share_pct": share.iloc[:, di] * 100,
        "share_pct250": pct.iloc[:, di] * 100,
        "ret5_pct": sec_ret5.iloc[:, di] * 100,
    }).dropna(subset=["share_pct250"])
    tab = tab.sort_values("share_pct250", ascending=False)
    out = ROOT / "output/final_combo_20261001/sector_crowding_latest.csv"
    tab.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n=== crowding top10 (share percentile vs 250d) ===")
    print(tab.head(10).round(2).to_string(index=False))
    print("\n=== most-abandoned bottom5 ===")
    print(tab.tail(5).round(2).to_string(index=False))

    # sanity: where was 微盘/题材 crowding around 2024-01 crash and now?
    for probe in ("2024-01-25", "2024-02-05", "2025-08-29"):
        pi = int(dates.searchsorted(pd.Timestamp(probe)))
        top3 = pct.iloc[:, pi].dropna().sort_values(ascending=False).head(3)
        lab = [names.get(s, s) for s in top3.index]
        print(f"[probe {probe}] top crowded: "
              + ", ".join(f"{a}({b:.0f}%)" for a, b in zip(lab, top3 * 100,
                                                          strict=True)))


if __name__ == "__main__":
    main()
