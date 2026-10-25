"""Direction 2: forward-walk ledger integrity check + settlement (research only).

weekly_forward.csv is the ONLY clean data source: rules frozen, signals
registered after Friday close, settled 4/8/12 weeks later against refreshed
TDX panel. Today (2026-10-04) the first registered week (2026-09-30) has 0
settleable rows — this script validates ledger integrity now and computes
settlements whenever enough future data exists. Statistics only.
"""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from run_pattern_regime_backtest import ROOT

LEDGER = ROOT / "output/monitor/weekly_forward.csv"
SNAPSHOT = ROOT / "output/daily_research_cache_20260930"
SETTLE_WEEKS = (4, 8, 12)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    ap.add_argument("--ledger", type=Path, default=LEDGER,
                    help="override for end-to-end tests")
    args = ap.parse_args()

    led = pd.read_csv(args.ledger, encoding="utf-8-sig")
    print(f"台账: {len(led)} 行, {led.sig_week.nunique()} 周 "
          f"({', '.join(sorted(led.sig_week.unique()))})")

    # integrity checks
    dup = led.duplicated(subset=["code", "sig_week"]).sum()
    null_score = led.score.isna().sum()
    bad_rps = ((led.rps120 < 0) | (led.rps120 > 100)).sum()
    print(f"完整性: 重复(code,week)={dup}  score空值={null_score}  "
          f"rps越界={bad_rps}")
    print(f"打分范围 [{led.score.min():.3f}, {led.score.max():.3f}], "
          f"amt20 范围 [{led['amt20_亿'].min():.1f}, {led['amt20_亿'].max():.1f}] 亿")

    # settlement against current snapshot
    z = np.load(args.snapshot / "panel.npz", allow_pickle=False)
    dates = pd.DatetimeIndex(z["dates"])
    codes = [str(c) for c in z["codes"]]
    close = pd.DataFrame(z["close"].astype(float), index=codes)
    close.columns = dates
    last = dates[-1]
    print(f"\n面板数据截至 {last.date()}（国庆休市，下个交易日 2026-10-09）")

    rows = []
    for (code, week), g in led.groupby(["code", "sig_week"]):
        wk = pd.Timestamp(week)
        # entry = next trading day open proxy: first session AFTER sig week
        fut = dates[dates > wk]
        if len(fut) < SETTLE_WEEKS[0] * 5:
            rows.append({"code": code, "sig_week": week, "status": "未满4周"})
            continue
        entry_day = fut[0]
        px0 = close.loc[code, entry_day]
        rec = {"code": code, "sig_week": week,
               "entry_date": str(entry_day.date())}
        if not np.isfinite(px0):
            rec["status"] = "入场日无价格"
            rows.append(rec)
            continue
        for w in SETTLE_WEEKS:
            tgt = w * 5
            if len(fut) > tgt:
                px = close.loc[code, fut[tgt]]
                rec[f"fwd{w}w_pct"] = round(
                    (px / px0 - 1) * 100, 2) if np.isfinite(px) else None
            else:
                rec[f"fwd{w}w_pct"] = None
        rec["status"] = "部分结算" if rec["fwd12w_pct"] is None else "已结算"
        rows.append(rec)

    res = pd.DataFrame(rows)
    out = ROOT / "output/monitor/weekly_forward_settlement.csv"
    res.to_csv(out, index=False, encoding="utf-8-sig")
    done = res[res.status == "已结算"]
    print(f"\n结算状态: {res.status.value_counts().to_dict()}")
    if len(done):
        print(f"已结算 {len(done)} 行: fwd8w 均值 {done.fwd8w_pct.mean():+.2f}%  "
              f"胜率 {(done.fwd8w_pct > 0).mean() * 100:.0f}%")
    else:
        print("结算批次数 = 0 —— 首期（2026-09-30 周）4 周结算日约 2026-10-30，"
              "8 周约 2026-11-25。今天没有可结算数据，这是正常的。")

    # ---- Gate-1 verdict (signed criteria, spec.md 前向验收标准) ----
    if "env_weak" not in led.columns:
        led["env_weak"] = None
    m = res.merge(led[["code", "sig_week", "score", "env_weak"]],
                  on=["code", "sig_week"], how="left")
    wk = m[m.env_weak.astype(str) == "True"].dropna(subset=["score"])
    wk = wk[wk.status.isin(["已结算", "部分结算"])]
    if len(wk):
        wk["basis"] = "fwd8w_pct"
        need4 = wk.fwd8w_pct.isna() & wk.fwd4w_pct.notna()
        wk.loc[need4, "basis"] = "fwd4w_pct(替代)"
        wk["ret"] = wk.fwd8w_pct.fillna(wk.fwd4w_pct)
        wk["rank"] = wk.groupby("sig_week")["score"].rank(ascending=False)
        top = wk[wk["rank"] <= 5].dropna(subset=["ret"])
        by_week = top.groupby("sig_week").ret.agg(["count", "mean"])
        print("\n=== 判决门 1 口径（env_weak 周 × 打分 top5）===")
        print(by_week.assign(mean=by_week["mean"].round(2)).to_string())
        if len(by_week):
            mean_all = by_week["mean"].mean()
            worst = by_week["mean"].min()
            ok = (mean_all > 0) and (worst >= -15)
            print(f"\n周均值 {mean_all:+.2f}%  最差周 {worst:+.2f}%  "
                  f"结算周数 {len(by_week)}")
            print(f"判决门 1: {'达标 → 下一弱周可 10~20% 仓位试点'
                  if ok else '不达标 → 继续纸上跟踪，门顺延 4 周'}")
            print("（最终判决须满 4~6 个结算周，2026-12-04 生效；"
                  "以上为中间读数）" if len(by_week) < 4 else "")
    else:
        print("\n判决门 1: 尚无 env_weak=True 的已结算周。")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
