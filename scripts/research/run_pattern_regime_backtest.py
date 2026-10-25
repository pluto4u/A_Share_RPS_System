"""Full-history causal backtest of the drop-base-retest pattern + market-regime gates.

Protection-net harness: every future strategy change must pass through here.

Design:
  - Data: frozen TDX snapshot (hash-verified, same guard as run_drop_base_retest).
  - Events: pattern_events with scan_start=0 over each stock's FULL history
    (causal at every bar; no name/ST filter -> no current-name survivorship bias;
    delisted stocks are still absent from the TDX source, disclosed as a limit).
  - Entries: breakout / quiet retest / confirmed / loud retest, each at
    tail (signal-bar close) and next_open, with T+1, costs, limit rules.
  - Exits: pattern invalidation (frozen floor / two closes below pivot band)
    or time stop; two exit sets: max_hold=20 and max_hold=2 (ice-point rule).
  - Regime: trend_start.regime classification attached by signal day; the
    environment only gates WHETHER/HOW MUCH to act — stocks always come from
    the pattern rules.
  - Stats: IS/OOS split, same-day full-market control, monthly block
    bootstrap CIs. Output ends at statistics; no buy/sell instructions.
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
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from trend_start.drop_base_retest import PatternConfig, pattern_entry_events
from trend_start.engine import block_bootstrap_mean, curve_stats
from trend_start.panel import load_panel
from trend_start.pattern_backtest import (
    FWD_HORIZONS,
    TIMINGS,
    ExitConfig,
    control_fwd_returns,
    portfolio_from_trades,
    simulate_entries,
)
from trend_start.regime import RegimeConfig, from_panel
from trend_start.research import sha256, source_manifest, validate_panel

ROOT = PROJECT_ROOT
IS_START, IS_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2021-12-31")
OOS_START = pd.Timestamp("2022-01-01")
MIN_BARS = 260
SLOTS = 20
EXIT_SETS = {"h20": ExitConfig(max_hold=20), "h2": ExitConfig(max_hold=2)}


def verify_snapshot(snapshot: Path,
                    allow_drift: tuple[str, ...] = ()) -> tuple[dict[str, Any], Path]:
    """Same frozen-cache guard as run_drop_base_retest: fail on any change.

    allow_drift: explicit whitelist of source paths whose drift is accepted
    for this run (e.g. a post-freeze TDX data refresh that leaves the frozen
    window untouched). Drift outside the whitelist still raises.
    """
    mp = snapshot / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    previous = json.loads((snapshot / "panel_audit.json").read_text(encoding="utf-8"))
    tdx_root = Path(previous["tdx_root"])
    print("[VERIFY] frozen snapshot and local TDX", flush=True)
    cur_sources = source_manifest(tdx_root)
    old_sources = manifest["signature"]["sources"]
    drift = {k for k in set(old_sources) | set(cur_sources)
             if old_sources.get(k) != cur_sources.get(k)}
    unexpected = drift - set(allow_drift)
    if unexpected:
        raise ValueError(f"Local source changed; rebuild snapshot: {sorted(unexpected)}")
    if drift:
        print(f"[VERIFY] accepted source drift (whitelisted): {sorted(drift)}",
              flush=True)
    for name, digest in manifest["artifacts"].items():
        if sha256(snapshot / name) != digest:
            raise ValueError(f"Changed artifact: {name}")
    for name, digest in manifest["signature"]["code"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError(f"Changed snapshot builder: {name}")
    return manifest, tdx_root


def build_trades(panel: Any, cfg: PatternConfig, ckpt: Path) -> pd.DataFrame:
    """Full-history causal scan with checkpoint/resume (long job, may be re-run)."""
    import pickle

    rows: list[dict] = []
    start_i = 0
    if ckpt.exists():
        state = pickle.loads(ckpt.read_bytes())
        rows, start_i = state["rows"], state["done"]
        print(f"[RESUME] checkpoint at stock {start_i}, trades={len(rows)}", flush=True)
    last_cal = len(panel.dates) - 1
    n_used = 0
    for i, raw_code in enumerate(panel.codes):
        if i < start_i:
            continue
        if i % 250 == 0:
            print(f"[TRADES] {i}/{len(panel.codes)} trades={len(rows)}", flush=True)
            ckpt.write_bytes(pickle.dumps({"rows": rows, "done": i}))
        cols = np.flatnonzero(np.isfinite(panel.field("close")[i]))
        if len(cols) < MIN_BARS:
            continue
        n_used += 1
        d = pd.DataFrame({k: panel.field(k)[i, cols].astype(float) for k in panel.data})
        try:
            entries = pattern_entry_events(d, cfg)
        except ValueError:
            continue  # invalid bars recorded upstream; skip fail-closed
        o = d.open.to_numpy()
        h = d.high.to_numpy()
        lo = d.low.to_numpy()
        c = d.close.to_numpy()
        cal = cols.astype(np.int64)
        dates = panel.dates[cols]
        for tag, ec in EXIT_SETS.items():
            for rec in simulate_entries(str(raw_code), entries, o, h, lo, c, cal, dates,
                                        exit_cfg=ec, timings=TIMINGS):
                rec["exit_set"] = tag
                rec["is_latest_stock"] = bool(cols[-1] == last_cal)
                rows.append(rec)
    print(f"[TRADES] stocks used: {n_used}", flush=True)
    ckpt.unlink(missing_ok=True)
    return pd.DataFrame(rows)


def excess(trades: pd.DataFrame, control: dict[int, np.ndarray], dates: np.ndarray,
           key: str) -> dict[str, float]:
    hz = int(key.removeprefix("fwd"))
    ctl = control[hz][trades.sig_cal.to_numpy()]
    x = trades[key].to_numpy() - ctl
    ok = np.isfinite(x)
    month = pd.Series(
        pd.DatetimeIndex(dates[trades.sig_cal.to_numpy()[ok]]).to_period("M").astype(str)
    )
    lo, hi = block_bootstrap_mean(pd.Series(x[ok]), month)
    return {"excess_mean_pct": float(np.nanmean(x) * 100),
            "excess_ci_lo_pct": lo * 100, "excess_ci_hi_pct": hi * 100}


def trade_row(g: pd.DataFrame) -> dict[str, Any]:
    r = g.ret.dropna()
    row: dict[str, Any] = {"trades": int(len(g))}
    if r.empty:
        return row
    wins = r[r > 0]
    losses = r[r <= 0]
    row.update({
        "win_rate_pct": float((r > 0).mean() * 100),
        "mean_ret_pct": float(r.mean() * 100),
        "median_ret_pct": float(r.median() * 100),
        "avg_win_pct": float(wins.mean() * 100) if len(wins) else np.nan,
        "avg_loss_pct": float(losses.mean() * 100) if len(losses) else np.nan,
        "median_hold": float(g.hold.median()),
        "invalidation_share_pct": float((g.exit_reason == "invalidation").mean() * 100),
        "unfinished_share_pct": float(g.unfinished.mean() * 100),
    })
    for hz in FWD_HORIZONS:
        row[f"fwd{hz}_mean_pct"] = float(g[f"fwd{hz}"].mean() * 100)
    return row


def period_of(dates: np.ndarray, sig_cal: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    d = pd.DatetimeIndex(dates[sig_cal.to_numpy()])
    return pd.Series(np.where(d <= IS_END, "IS", "OOS"), index=sig_cal.index)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path,
                    default=ROOT / "output/daily_research_cache_20260930")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=ROOT / "config/drop_base_retest.json")
    ap.add_argument("--regime-config", type=Path, default=None)
    args = ap.parse_args()
    pcfg = PatternConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    rcfg = RegimeConfig()
    if args.regime_config is not None:
        rcfg = RegimeConfig(**json.loads(args.regime_config.read_text(encoding="utf-8")))
    verify_snapshot(args.snapshot)
    panel = load_panel(args.snapshot)
    validate_panel(panel)
    as_of = pd.Timestamp(panel.dates[-1])
    dates = panel.dates
    print(f"[PANEL] {len(panel.codes)} stocks x {len(dates)} sessions, as_of {as_of.date()}",
          flush=True)

    ckpt = args.output / "_checkpoint.pkl"
    if args.output.exists() and not ckpt.exists():
        raise SystemExit(f"output exists without checkpoint (complete or foreign): {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    regime = from_panel(panel, rcfg)
    trades = build_trades(panel, pcfg, ckpt)
    if trades.empty:
        raise RuntimeError("no trades generated")
    trades["period"] = period_of(dates, trades.sig_cal, as_of)
    gate_by_cal = regime["gate"].to_numpy()
    mult_by_cal = regime["position_mult"].to_numpy()
    volreg_by_cal = regime["volume_regime"].to_numpy()
    trades["gate"] = gate_by_cal[trades.sig_cal.to_numpy()]
    trades["position_mult"] = mult_by_cal[trades.sig_cal.to_numpy()]
    trades["volume_regime"] = volreg_by_cal[trades.sig_cal.to_numpy()]

    regime.to_csv(args.output / "regime_series.csv", encoding="utf-8-sig")
    trades.to_csv(args.output / "trades.csv", index=False, encoding="utf-8-sig")

    control = control_fwd_returns(panel.field("close"), dates)

    # --- Table A: kind x timing x exit_set x period ---
    rows_a: list[dict] = []
    for (kind, timing, es, period), g in trades.groupby(
            ["kind", "timing", "exit_set", "period"]):
        row = {"kind": kind, "timing": timing, "exit_set": es, "period": period,
               **trade_row(g)}
        if timing == "tail":
            for hz in (2, 5, 20):
                row.update({f"{k}_fwd{hz}": v for k, v in
                            excess(g, control, dates, f"fwd{hz}").items()})
        rows_a.append(row)
    table_a = pd.DataFrame(rows_a)
    table_a.to_csv(args.output / "stats_by_kind.csv", index=False, encoding="utf-8-sig")

    # --- Table B: user hypothesis — gate conditioning (tail entries) ---
    rows_b: list[dict] = []
    sub = trades.loc[trades.timing == "tail"]
    for (kind, es, gate, period), g in sub.groupby(["kind", "exit_set", "gate", "period"]):
        rows_b.append({"kind": kind, "exit_set": es, "gate": gate, "period": period,
                       **trade_row(g)})
    table_b = pd.DataFrame(rows_b)
    table_b.to_csv(args.output / "stats_by_gate.csv", index=False, encoding="utf-8-sig")

    # --- Table C: regime-defined action pairs, the user's two rules ---
    rules = {
        "rule_ice_quiet_h2": (trades.kind == "quiet") & (trades.exit_set == "h2")
                             & (trades.timing == "tail") & (trades.gate == "ice_buy"),
        "rule_ice_quiet_h2_ungated": (trades.kind == "quiet") & (trades.exit_set == "h2")
                                     & (trades.timing == "tail"),
        "rule_surge_chase_h20": (trades.kind.isin(["breakout", "confirmed"]))
                                & (trades.exit_set == "h20") & (trades.timing == "tail")
                                & (trades.gate == "surge_chase"),
        "rule_surge_chase_h20_ungated": (trades.kind.isin(["breakout", "confirmed"]))
                                        & (trades.exit_set == "h20")
                                        & (trades.timing == "tail"),
    }
    rows_c: list[dict] = []
    for name, mask in rules.items():
        g = trades.loc[mask]
        for period in ("IS", "OOS"):
            gp = g.loc[g.period == period]
            row = {"rule": name, "period": period, **trade_row(gp)}
            if len(gp):
                row.update({f"{k}_fwd2": v for k, v in
                            excess(gp, control, dates, "fwd2").items()})
            rows_c.append(row)
    table_c = pd.DataFrame(rows_c)
    table_c.to_csv(args.output / "stats_user_rules.csv", index=False, encoding="utf-8-sig")

    # --- Portfolios ---
    close_ff = pd.DataFrame(panel.field("close")).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(c): i for i, c in enumerate(panel.codes)}
    cal = pd.DatetimeIndex(dates)
    windows = {
        "IS": (int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right") - 1)),
        "OOS": (int(cal.searchsorted(OOS_START)), len(cal) - 1),
    }
    windows["FULL"] = (windows["IS"][0], len(cal) - 1)
    pf_specs = {
        "P1_quiet_tail_h20_all": dict(kind=["quiet"], es="h20", gates=None, scale=False),
        "P2_ice_quiet_tail_h2": dict(kind=["quiet"], es="h2", gates=["ice_buy"], scale=True),
        "P3_surge_chase_tail_h20": dict(kind=["breakout", "confirmed"], es="h20",
                                        gates=["surge_chase"], scale=True),
        "P4_user_combined": dict(kind=None, es=None, gates=None, scale=True),
    }
    pf_rows: list[dict] = []
    curves: dict[str, pd.Series] = {}
    for name, spec in pf_specs.items():
        if name == "P4_user_combined":
            t = trades.loc[
                (((trades.kind == "quiet") & (trades.exit_set == "h2")
                  & (trades.gate == "ice_buy"))
                 | ((trades.kind.isin(["breakout", "confirmed"])) & (trades.exit_set == "h20")
                    & (trades.gate == "surge_chase")))
                & (trades.timing == "tail")
            ]
        else:
            t = trades.loc[(trades.timing == "tail") & (trades.exit_set == spec["es"])
                           & (trades.kind.isin(spec["kind"]))]
            if spec["gates"] is not None:
                t = t.loc[t.gate.isin(spec["gates"])]
        gm = {i: float(m) for i, m in enumerate(mult_by_cal)} if spec["scale"] else None
        for w, (a, b) in windows.items():
            curve, pf_trades = portfolio_from_trades(t, close_ff, code_row, dates,
                                                     a, b, SLOTS, gate_mult=gm)
            st = curve_stats(curve)
            st.update({"portfolio": name, "window": w, "pf_trades": len(pf_trades),
                       "pf_win_rate_pct": float((pf_trades.pf_ret > 0).mean() * 100)
                       if len(pf_trades) else np.nan})
            pf_rows.append(st)
            if w == "FULL":
                curves[name] = curve
        print(f"[PORTFOLIO] {name} done", flush=True)
    # benchmarks
    hs = pd.Series(panel.index_close).ffill()
    cmat = panel.field("close").astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        rets = cmat[:, 1:] / cmat[:, :-1] - 1
    ew = pd.Series(np.cumprod(1 + np.r_[0.0, np.nanmean(rets, axis=0)]))
    for w, (a, b) in windows.items():
        for bname, series in (("CSI300", hs), ("EqualWeightA", ew)):
            seg = series.iloc[a: b + 1]
            seg.index = pd.DatetimeIndex(dates[a: b + 1])
            pf_rows.append({"portfolio": bname, "window": w,
                            **curve_stats(seg / seg.iloc[0])})
            if w == "FULL":
                curves[bname] = seg / seg.iloc[0]
    pd.DataFrame(pf_rows).to_csv(args.output / "portfolio_stats.csv",
                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(curves).to_csv(args.output / "equity_curves.csv", encoding="utf-8-sig")

    audit = {
        "as_of": str(as_of.date()),
        "generated_at": datetime.now().isoformat(),
        "snapshot": str(args.snapshot.resolve()),
        "periods": {"IS": [str(IS_START.date()), str(IS_END.date())],
                    "OOS": [str(OOS_START.date()), str(as_of.date())]},
        "min_bars": MIN_BARS,
        "exit_sets": {k: vars(v) for k, v in EXIT_SETS.items()},
        "regime_config": vars(rcfg),
        "pattern_config": pcfg.model_dump(),
        "trades": int(len(trades)),
        "gate_share_pct": regime["gate"].value_counts(normalize=True).mul(100).round(2).to_dict(),
        "limits": [
            "退市股缺失与历史ST状态不全, 幸存者偏差仍在; 回测不按当前名称过滤.",
            "尾盘入场用信号日收盘价近似14:50成交价, 盘中假突破/尾盘拉升未建模.",
            "超额收益对照为同日全市场等权(尾盘口径); next_open口径只报原始统计.",
            "环境闸门阈值是首版设定, 未做参数优化; 结果不构成买卖建议.",
        ],
        "source_code_hashes": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in (Path(__file__).resolve(),
                      ROOT / "trend_start/regime.py",
                      ROOT / "trend_start/pattern_backtest.py")
        },
    }
    (args.output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[DONE] {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
