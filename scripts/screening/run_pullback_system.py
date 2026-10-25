"""四段式回踩策略：下跌 -> 横盘缩量 -> 放量 -> 回踩买入。网格写死，先IS选参冻结，再OOS评估一次。

结构（顺序固定，不可合并）：
  1) 下跌：decline_bars 根内跌幅 >= decline_min，收盘高点在前半段、低点在后半段，
     且横盘开始时仍比高点低至少 10%（沿用 pattern.py 的固定结构）。
  2) 横盘缩量：随后 base_bars 根振幅 (最高-最低)/末收 <= base_range_max，
     漂移 <= 8%、不破下跌段最低（沿用 pattern.py 固定结构），
     横盘均量 / 下跌均量 <= vol_contract。
  3) 放量：阳线、收盘站上 MA20、量 >= vol_expand * 横盘均量（沿用 pattern.py 的 right 段）。
  4) 回踩买入：放量后 pullback_bars 根内，每根收盘仍 >= MA20；首根收盘
     <= MA20*(1+pullback_tol) 且 t0+1..j 均量 < 放量日量 -> 信号（收盘确认），
     下一交易日开盘买入。开盘涨停不买（engine buyable）；停牌隔日不买（gap_ok）。
     同一只未卖出前不重复买（sig_bar >= 前一笔离场 bar；未完结交易永久阻断）。
卖出：收盘 < MA20 或收盘 < 该次回踩低点（t0+1..j 的最低价），先到先卖，下一交易日开盘卖；
     跌停/一字跌停顺延规则全部走 trend_start/engine.py。
对照：同一天 收盘>MA20 且 20 日均成交额 >= 1 亿元 的股票，同一买卖时点。
挑选规则（写死）：样本内成交 >= 300 笔，按 mean(策略收益 - 同日对照均值) 降序，并列取笔数多者；
     区间 = 月度区块自助 2000 次、种子 7 的 95% CI（engine.block_bootstrap_mean）。
基线：同一形态参数、放量当天收盘为信号、次日开盘买、收盘破 MA20 次日开盘卖，同一引擎。

用法（网格重，分片跑，再汇总）：
  python run_pullback_system.py --grid-part 0 1200
  ...
  python run_pullback_system.py --assemble
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
import pickle
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from trend_start.engine import (
    StockBars,
    block_bootstrap_mean,
    curve_stats,
    execution_flags,
    first_close_below,
    first_true_from,
    limit_pct,
    outcomes,
    portfolio,
    sell_next_open,
)
from trend_start.panel import Panel, load_panel

ROOT = PROJECT_ROOT
CACHE = ROOT / "output" / "trend_start_cache_20260930"
OUTDIR = ROOT / "output" / "daily_system"
PARTS = OUTDIR / "_parts"

# ---- 网格常量（跑之前写死，禁止事后加参数）----
DECLINE_BARS = (20, 40, 60)
DECLINE_MIN = (0.10, 0.15, 0.20)
BASE_BARS = (10, 20, 40)
BASE_RANGE_MAX = (0.08, 0.12, 0.15)
VOL_CONTRACT = (0.6, 0.8)
VOL_EXPAND = (1.5, 2.0)
PULLBACK_BARS = (3, 5)
PULLBACK_TOL = (0.03, 0.05)

# ---- 固定结构常量（非搜索参数，沿用 pattern.py）----
REMAINING_DROP = 0.10
BASE_DRIFT_MAX = 0.08
LIQ_AMOUNT = 1e8
MIN_STOCK_BARS = 260
SLOTS = 20
IS_START, IS_END = pd.Timestamp("2016-09-01"), pd.Timestamp("2021-12-31")
OOS_START = pd.Timestamp("2022-01-01")
MIN_IS_TRADES = 300
BOOT_N, BOOT_SEED = 2000, 7
BIG = 10**9

PATTERN_GRID = list(product(DECLINE_BARS, DECLINE_MIN, BASE_BARS, BASE_RANGE_MAX,
                            VOL_CONTRACT, VOL_EXPAND))
N_PB = len(PULLBACK_BARS) * len(PULLBACK_TOL)  # 4
N_COMBOS = len(PATTERN_GRID) * N_PB            # 1296


def combo_cid(pid: int, ki: int, ti: int) -> int:
    return pid * N_PB + ki * len(PULLBACK_TOL) + ti


def combo_desc(cid: int) -> str:
    pid, sub = divmod(cid, N_PB)
    K = PULLBACK_BARS[sub // len(PULLBACK_TOL)]
    tol = PULLBACK_TOL[sub % len(PULLBACK_TOL)]
    db, dmin, bb, brmax, vc, ve = PATTERN_GRID[pid]
    return (f"下跌{db}根/跌{int(dmin*100)}% + 横盘{bb}根/振幅{int(brmax*100)}% + "
            f"缩量<={vc} + 放量>={ve}x + 回踩{K}根/MA20+{int(tol*100)}%")


def _window(x: np.ndarray, n: int, start: np.ndarray) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(x, n)[start]


def _roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    cs = np.cumsum(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    out[n - 1 :] = (cs[n - 1 :] - np.concatenate(([0.0], cs[:-n]))) / n
    return out


def layout_arrays(o, h, lo, c, v, ma20, db: int, bb: int) -> dict | None:
    """Per (decline_bars, base_bars): threshold-independent arrays aligned to right-day bar t."""
    n = len(c)
    need = db + bb
    if n <= need:
        return None
    s = np.arange(n - need)
    t = s + need
    dec = _window(c, db, s)
    dec_lo = _window(lo, db, s)
    dec_v = _window(v, db, s)
    base = _window(c, bb, s + db)
    base_h = _window(h, bb, s + db)
    base_l = _window(lo, bb, s + db)
    base_v = _window(v, bb, s + db)
    pos = np.arange(len(s))
    peak_at = dec.argmax(axis=1)
    trough_at = dec.argmin(axis=1)
    peak = dec[pos, peak_at]
    with np.errstate(invalid="ignore", divide="ignore"):
        drop = 1 - dec.min(axis=1) / dec.max(axis=1)
        remain = dec[:, -1] / peak - 1
        base_range = (base_h.max(axis=1) - base_l.min(axis=1)) / base[:, -1]
        drift = np.abs(base[:, -1] / base[:, 0] - 1)
        base_mean = base_v.mean(axis=1)
        dec_mean = dec_v.mean(axis=1)
        volr = v[t] / base_mean
    dec_base = (peak_at < db // 2) & (trough_at >= db // 2) & (remain <= -REMAINING_DROP)
    base_common = ((drift <= BASE_DRIFT_MAX) & (base_l.min(axis=1) >= dec_lo.min(axis=1))
                   & np.isfinite(base_range))
    med = np.median(base, axis=1)
    right_common = ((c[t] > o[t]) & (c[t] > c[t - 1]) & np.isfinite(ma20[t])
                    & (c[t] > ma20[t]) & (c[t] > med))
    return {
        "t": t,
        "dec": {dm: dec_base & (drop >= dm) for dm in DECLINE_MIN},
        "base": {br: base_common & (base_range <= br) for br in BASE_RANGE_MAX},
        "dry": {vc: np.isfinite(dec_mean) & (dec_mean > 0) & (base_mean <= vc * dec_mean)
                for vc in VOL_CONTRACT},
        "volup": {ve: volr >= ve for ve in VOL_EXPAND},
        "right_common": right_common,
        "base_low": base_l.min(axis=1),
    }


def pullback_all(c, lo, v, ma20, t0: int) -> dict[tuple[int, float], tuple[int, float]]:
    """Single walk over t0+1..t0+max(K). Returns {(K, tol): (j, pullback_low)}.

    j is the first bar with c[j] <= MA20*(1+tol) whose pullback mean volume (t0+1..j)
    is below the volume day's; every close in t0+1..j must stay >= MA20, else no signal.
    """
    n = len(c)
    res: dict[tuple[int, float], tuple[int, float]] = {}
    found: dict[float, tuple[int, float]] = {}
    lo_run, vsum, cnt = np.inf, 0.0, 0
    kmax = max(PULLBACK_BARS)
    for j in range(t0 + 1, min(t0 + kmax, n - 1) + 1):
        if not np.isfinite(ma20[j]) or c[j] < ma20[j]:
            break
        lo_run = min(lo_run, lo[j])
        vsum += v[j]
        cnt += 1
        for tol in PULLBACK_TOL:
            if tol not in found and c[j] <= ma20[j] * (1 + tol) and vsum / cnt < v[t0]:
                found[tol] = (j, float(lo_run))
        if len(found) == len(PULLBACK_TOL):
            break
    for tol, (j, pbl) in found.items():
        for K in PULLBACK_BARS:
            if j - t0 <= K:
                res[(K, tol)] = (j, pbl)
    return res


def chain_mask(sig_bar: np.ndarray, exit_bar: np.ndarray, unfinished: np.ndarray) -> np.ndarray:
    """Non-overlapping trades per stock: sig_bar >= previous exit bar; unfinished blocks the rest."""
    keep = np.zeros(len(sig_bar), dtype=bool)
    last_exit = -1
    for i in range(len(sig_bar)):
        if sig_bar[i] < last_exit:
            continue
        keep[i] = True
        last_exit = BIG if unfinished[i] else int(exit_bar[i])
    return keep


PB_FIELDS = ("cid", "sig_bar", "sig_cal", "entry_cal", "entry_px", "exit_bar", "exit_cal",
             "exit_px", "exit_at_close", "unfinished", "ret", "base_low", "pullback_low",
             "sig_close", "rps120", "d1", "d5")


def scan_stock(code: str, cols: np.ndarray, dates: np.ndarray,
               row_fields: dict[str, np.ndarray], r120: np.ndarray,
               want_pb: bool, baseline_pids: set[int]):
    """One stock on traded bars.

    Returns (pb, base_rows, live_rows, ctrl):
      pb: dict field -> np.ndarray of chained pullback events (all combos), or None.
      base_rows: list of baseline trade tuples (chained per pid).
      live_rows: (cid, sig_bar, pullback_low, base_low, rps120) with signal on last session.
      ctrl: (ret_cal, ret, d1_cal, d1, d5_cal, d5) control accumulators.
    """
    o = row_fields["open"][cols].astype(np.float64)
    h = row_fields["high"][cols].astype(np.float64)
    lo = row_fields["low"][cols].astype(np.float64)
    c = row_fields["close"][cols].astype(np.float64)
    v = row_fields["vol"][cols].astype(np.float64)
    amt = row_fields["amount"][cols].astype(np.float64)
    n = len(c)
    if n < MIN_STOCK_BARS:
        return None, [], [], None
    ma20 = _roll_mean(c, 20)
    amt20 = _roll_mean(amt, 20)
    b = StockBars(code, cols, dates[cols], o, h, lo, c, ma20)
    out = outcomes(b)
    flags = execution_flags(b)
    below = c < ma20
    last_cal = len(dates) - 1

    # control cohort: close > MA20 and 20d mean amount >= 1e8, buy next open, MA20 exit
    elig = (c > ma20) & (amt20 >= LIQ_AMOUNT)
    s = np.flatnonzero(elig)
    s = s[s + 1 < n]
    r_ctl = out.ret["open"][s + 1]
    ok_ctl = np.isfinite(r_ctl)
    m5 = s + 5 < n
    ctrl = (cols[s[ok_ctl]], r_ctl[ok_ctl],
            cols[s], c[s + 1] / c[s] - 1,
            cols[s[m5]], c[s[m5] + 5] / c[s[m5]] - 1)

    layouts = {}
    for db, bb in product(DECLINE_BARS, BASE_BARS):
        la = layout_arrays(o, h, lo, c, v, ma20, db, bb)
        if la is not None:
            layouts[(db, bb)] = la

    ev_cid: list[int] = []
    ev_j: list[int] = []
    ev_pbl: list[float] = []
    ev_bl: list[float] = []
    base_rows: list[tuple] = []
    live_rows: list[tuple] = []
    for pid, (db, dmin, bb, brmax, vc, ve) in enumerate(PATTERN_GRID):
        la = layouts.get((db, bb))
        if la is None:
            continue
        mask = (la["dec"][dmin] & la["base"][brmax] & la["dry"][vc]
                & la["volup"][ve] & la["right_common"])
        if not mask.any():
            continue
        pos = np.flatnonzero(mask)
        tdays = la["t"][pos]
        base_lows = la["base_low"][pos]

        if pid in baseline_pids:
            brows = []
            for t0, bl0 in zip(tdays, base_lows):
                e0 = int(t0) + 1
                if e0 >= n or not out.buyable[e0]:
                    continue
                k0 = int(out.exit_bar["open"][e0])
                brows.append((int(t0), e0, k0, float(out.exit_px["open"][e0]),
                              bool(out.exit_at_close["open"][e0]),
                              bool(out.unfinished["open"][e0]),
                              float(out.ret["open"][e0]), float(bl0)))
            keep = chain_mask(np.array([x[0] for x in brows]),
                              np.array([x[2] for x in brows]),
                              np.array([x[5] for x in brows]))
            for (t0, e0, k0, xpx, xac, uf, rt, bl0), kp in zip(brows, keep):
                if kp:
                    base_rows.append((pid, t0, int(cols[t0]), int(cols[e0]), float(o[e0]),
                                      k0, int(cols[k0]), xpx, xac, uf, rt, bl0,
                                      float(c[t0]), float(r120[cols[t0]])))
        if not want_pb:
            continue
        for t0, bl0 in zip(tdays, base_lows):
            t0 = int(t0)
            for (K, tol), (j, pbl) in pullback_all(c, lo, v, ma20, t0).items():
                ki = PULLBACK_BARS.index(K)
                ti = PULLBACK_TOL.index(tol)
                cid = combo_cid(pid, ki, ti)
                if j == n - 1:
                    if cols[j] == last_cal:
                        live_rows.append((cid, j, float(pbl), float(bl0), float(r120[cols[j]])))
                    continue
                ev_cid.append(cid)
                ev_j.append(j)
                ev_pbl.append(pbl)
                ev_bl.append(float(bl0))

    pb: dict[str, np.ndarray] | None = None
    if ev_cid:
        cid_a = np.asarray(ev_cid, dtype=np.int32)
        j_a = np.asarray(ev_j, dtype=np.int64)
        pbl_a = np.asarray(ev_pbl, dtype=np.float64)
        bl_a = np.asarray(ev_bl, dtype=np.float64)
        ee = j_a + 1
        buyable = out.buyable[ee]
        j_ma = first_true_from(below, ee)
        j_low = first_close_below(c, ee, pbl_a)
        trig = np.minimum(j_ma, j_low)
        k, px, at_close, unf, ret = sell_next_open(b, trig, flags, o[ee])
        ok = buyable & np.isfinite(ret)
        keep_all = np.zeros(len(j_a), dtype=bool)
        for cid in np.unique(cid_a[ok]):
            m = ok & (cid_a == cid)
            idx = np.flatnonzero(m)
            order = idx[np.argsort(j_a[idx], kind="stable")]
            keep = chain_mask(j_a[order], k[order], unf[order])
            keep_all[order[keep]] = True
        w = np.flatnonzero(keep_all)
        jj = j_a[w]
        d1 = c[jj + 1] / c[jj] - 1  # live case (j == n-1) never reaches events
        d5 = np.where(jj + 5 < n, c[np.minimum(jj + 5, n - 1)] / c[jj] - 1, np.nan)
        pb = {
            "cid": cid_a[w], "sig_bar": jj, "sig_cal": cols[jj].astype(np.int32),
            "entry_cal": cols[jj + 1].astype(np.int32), "entry_px": o[jj + 1].astype(np.float32),
            "exit_bar": k[w].astype(np.int32), "exit_cal": cols[k[w]].astype(np.int32),
            "exit_px": px[w].astype(np.float32), "exit_at_close": at_close[w],
            "unfinished": unf[w], "ret": ret[w].astype(np.float32),
            "base_low": bl_a[w].astype(np.float32), "pullback_low": pbl_a[w].astype(np.float32),
            "sig_close": c[jj].astype(np.float32), "rps120": r120[cols[jj]].astype(np.float32),
            "d1": d1.astype(np.float32), "d5": d5.astype(np.float32),
        }
    return pb, base_rows, live_rows, ctrl


def run_grid_part(panel: Panel, r120_mat: np.ndarray, start: int, end: int) -> None:
    """Minimal chained rows (combo, sig_cal, ret) + control accumulators for codes[start:end]."""
    dates = panel.dates
    close = panel.field("close")
    rf_all = {f: panel.field(f) for f in ("open", "high", "low", "close", "vol", "amount")}
    m = len(dates)
    acc = {k: np.zeros(m) for k in ("ret_sum", "ret_cnt", "d1_sum", "d1_cnt", "d5_sum", "d5_cnt")}
    out_combo: list[np.ndarray] = []
    out_scal: list[np.ndarray] = []
    out_ret: list[np.ndarray] = []
    for i in range(start, min(end, len(panel.codes))):
        if i % 200 == 0:
            print(f"[GRID {start}:{end}] {i}", flush=True)
        cols = np.flatnonzero(~np.isnan(close[i]))
        if len(cols) < MIN_STOCK_BARS:
            continue
        rf = {f: rf_all[f][i] for f in rf_all}
        pb, _, _, ctrl = scan_stock(str(panel.codes[i]), cols, dates, rf, r120_mat[i], True, set())
        if ctrl is not None:
            c_ret, r_ret, c_d1, d_d1, c_d5, d_d5 = ctrl
            np.add.at(acc["ret_sum"], c_ret, r_ret)
            np.add.at(acc["ret_cnt"], c_ret, 1)
            np.add.at(acc["d1_sum"], c_d1, d_d1)
            np.add.at(acc["d1_cnt"], c_d1, 1)
            np.add.at(acc["d5_sum"], c_d5, d_d5)
            np.add.at(acc["d5_cnt"], c_d5, 1)
        if pb is not None:
            out_combo.append(pb["cid"])
            out_scal.append(pb["sig_cal"])
            out_ret.append(pb["ret"])
    df = pd.DataFrame({
        "combo": np.concatenate(out_combo) if out_combo else np.array([], dtype=np.int32),
        "sig_cal": np.concatenate(out_scal) if out_scal else np.array([], dtype=np.int32),
        "ret": np.concatenate(out_ret) if out_ret else np.array([], dtype=np.float32),
    })
    PARTS.mkdir(parents=True, exist_ok=True)
    with open(PARTS / f"grid_{start}_{end}.pkl", "wb") as fh:
        pickle.dump({"rows": df, "acc": acc}, fh)
    print(f"[GRID {start}:{end}] rows={len(df)} saved", flush=True)


def run_detail(panel: Panel, r120_mat: np.ndarray, win_cid: int):
    """Full-column chained events for the frozen combo and its baseline, plus live candidates."""
    win_pid, sub = divmod(win_cid, N_PB)
    dates = panel.dates
    close = panel.field("close")
    rf_all = {f: panel.field(f) for f in ("open", "high", "low", "close", "vol", "amount")}
    rows: list[dict] = []
    live: list[dict] = []
    for i, code in enumerate(panel.codes):
        if i % 500 == 0:
            print(f"[DETAIL] {i}/{len(panel.codes)} rows={len(rows)}", flush=True)
        cols = np.flatnonzero(~np.isnan(close[i]))
        if len(cols) < MIN_STOCK_BARS:
            continue
        rf = {f: rf_all[f][i] for f in rf_all}
        pb, base_rows, live_rows, _ = scan_stock(str(code), cols, dates, rf, r120_mat[i],
                                                 True, {win_pid})
        if pb is not None:
            m = pb["cid"] == win_cid
            if m.any():
                for j in np.flatnonzero(m):
                    rows.append({"combo": win_cid, "code": str(code), "code_row": i,
                                 "sig_bar": int(pb["sig_bar"][j]), "sig_cal": int(pb["sig_cal"][j]),
                                 "entry_cal": int(pb["entry_cal"][j]),
                                 "entry_px": float(pb["entry_px"][j]),
                                 "exit_bar_open": int(pb["exit_bar"][j]),
                                 "exit_cal_open": int(pb["exit_cal"][j]),
                                 "exit_px_open": float(pb["exit_px"][j]),
                                 "exit_at_close_open": bool(pb["exit_at_close"][j]),
                                 "unfinished_open": bool(pb["unfinished"][j]),
                                 "ret_open": float(pb["ret"][j]),
                                 "base_low": float(pb["base_low"][j]),
                                 "pullback_low": float(pb["pullback_low"][j]),
                                 "sig_close": float(pb["sig_close"][j]),
                                 "rps120": float(pb["rps120"][j]),
                                 "d1": float(pb["d1"][j]), "d5": float(pb["d5"][j])})
        for (pid, t0, scal, ecal, epx, k0, xcal, xpx, xac, uf, rt, bl0, sc_, r_) in base_rows:
            rows.append({"combo": f"B{pid}", "code": str(code), "code_row": i,
                         "sig_bar": t0, "sig_cal": scal, "entry_cal": ecal, "entry_px": epx,
                         "exit_bar_open": k0, "exit_cal_open": xcal, "exit_px_open": xpx,
                         "exit_at_close_open": xac, "unfinished_open": uf, "ret_open": rt,
                         "base_low": bl0, "pullback_low": np.nan, "sig_close": sc_,
                         "rps120": r_, "d1": np.nan, "d5": np.nan})
        for cid, j, pbl, bl, r_ in live_rows:
            if cid == win_cid:
                live.append({"combo": cid, "code": str(code), "code_row": i, "sig_bar": j,
                             "pullback_low": pbl, "base_low": bl, "rps120": r_})
    return pd.DataFrame(rows), pd.DataFrame(live)


def period_stats(t: pd.DataFrame, ctrl_mean: pd.Series, dates: np.ndarray,
                 start: pd.Timestamp, end: pd.Timestamp) -> dict:
    d = pd.DatetimeIndex(dates[t.sig_cal.to_numpy()])
    m = np.asarray((d >= start) & (d <= end))
    tt = t[m]
    if tt.empty:
        return {"trades": 0}
    ctl = ctrl_mean.reindex(tt.sig_cal.to_numpy()).to_numpy()
    x = tt.ret_open.to_numpy() - ctl
    month = pd.Series(pd.DatetimeIndex(dates[tt.sig_cal.to_numpy()]).to_period("M").astype(str))
    lo, hi = block_bootstrap_mean(pd.Series(x), month, BOOT_N, BOOT_SEED)
    return {
        "trades": int(len(tt)),
        "mean_ret_pct": float(np.nanmean(tt.ret_open.to_numpy()) * 100),
        "control_mean_pct": float(np.nanmean(ctl) * 100),
        "excess_mean_pct": float(np.nanmean(x) * 100),
        "excess_ci_lo_pct": lo * 100,
        "excess_ci_hi_pct": hi * 100,
        "win_rate_pct": float((tt.ret_open > 0).mean() * 100),
        "median_hold_bars": float((tt.exit_bar_open - tt.sig_bar - 1).median()),
    }


def assemble() -> int:
    panel = load_panel(CACHE)
    paudit = json.loads((CACHE / "panel_audit.json").read_text(encoding="utf-8"))
    dates = panel.dates
    as_of = pd.Timestamp(dates[-1])
    print(f"[PANEL] {len(panel.codes)} stocks x {len(dates)} sessions, last {as_of.date()}", flush=True)
    z = np.load(CACHE / "rps.npz")
    r120_mat = z["RPS120"]
    cal = pd.DatetimeIndex(dates)
    is_mask = np.asarray((cal >= IS_START) & (cal <= IS_END))

    # ---- merge grid parts ----
    parts = sorted(PARTS.glob("grid_*.pkl"))
    if not parts:
        raise SystemExit("no grid parts found")
    frames, accs = [], []
    for p in parts:
        with open(p, "rb") as fh:
            d = pickle.load(fh)
        frames.append(d["rows"])
        accs.append(d["acc"])
    grid = pd.concat(frames, ignore_index=True)
    acc = {k: np.sum([a[k] for a in accs], axis=0) for k in accs[0]}
    print(f"[GRID] merged {len(parts)} parts, chained rows {len(grid)}", flush=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        ctrl_mean = pd.Series(acc["ret_sum"] / np.where(acc["ret_cnt"] > 0, acc["ret_cnt"], np.nan))
        ctrl_d1 = pd.Series(acc["d1_sum"] / np.where(acc["d1_cnt"] > 0, acc["d1_cnt"], np.nan))
        ctrl_d5 = pd.Series(acc["d5_sum"] / np.where(acc["d5_cnt"] > 0, acc["d5_cnt"], np.nan))

    grid["x"] = grid.ret.to_numpy() - ctrl_mean.reindex(grid.sig_cal.to_numpy()).to_numpy()
    isg = grid[is_mask[grid.sig_cal.to_numpy()]]
    summ = isg.groupby("combo").agg(trades=("ret", "size"), mean_ex=("x", "mean"),
                                    mean_ret=("ret", "mean")).reset_index()
    summ["desc"] = summ.combo.map(combo_desc)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    summ = summ.sort_values(["mean_ex", "trades"], ascending=False)
    summ.to_csv(OUTDIR / "grid_is_summary.csv", index=False, encoding="utf-8-sig")
    elig = summ.loc[summ.trades >= MIN_IS_TRADES]
    if elig.empty:
        raise SystemExit("no combo reaches 300 IS trades")
    win = elig.iloc[0]
    win_cid = int(win.combo)
    win_pid, sub = divmod(win_cid, N_PB)
    win_K = PULLBACK_BARS[sub // len(PULLBACK_TOL)]
    win_tol = PULLBACK_TOL[sub % len(PULLBACK_TOL)]
    print(f"[FREEZE] combo {win_cid}: {combo_desc(win_cid)} "
          f"(IS trades={int(win.trades)}, mean_ex={win.mean_ex*100:.3f}%)", flush=True)
    del grid, isg

    # ---- detail pass: frozen combo + baseline ----
    det, live = run_detail(panel, r120_mat, win_cid)
    with open(PARTS / "detail.pkl", "wb") as fh:
        pickle.dump({"det": det, "live": live, "acc": acc, "win_cid": win_cid,
                     "win_pid": win_pid, "win_K": win_K, "win_tol": win_tol}, fh)
    print(f"[DETAIL] saved bundle ({len(det)} rows); run --report next", flush=True)
    return 0


def report() -> int:
    panel = load_panel(CACHE)
    paudit = json.loads((CACHE / "panel_audit.json").read_text(encoding="utf-8"))
    dates = panel.dates
    as_of = pd.Timestamp(dates[-1])
    z = np.load(CACHE / "rps.npz")
    r120_mat = z["RPS120"]
    cal = pd.DatetimeIndex(dates)
    with open(PARTS / "detail.pkl", "rb") as fh:
        bundle = pickle.load(fh)
    det, live, acc = bundle["det"], bundle["live"], bundle["acc"]
    win_cid, win_pid = bundle["win_cid"], bundle["win_pid"]
    win_K, win_tol = bundle["win_K"], bundle["win_tol"]
    print(f"[FREEZE] combo {win_cid}: {combo_desc(win_cid)}", flush=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        ctrl_mean = pd.Series(acc["ret_sum"] / np.where(acc["ret_cnt"] > 0, acc["ret_cnt"], np.nan))
        ctrl_d1 = pd.Series(acc["d1_sum"] / np.where(acc["d1_cnt"] > 0, acc["d1_cnt"], np.nan))
        ctrl_d5 = pd.Series(acc["d5_sum"] / np.where(acc["d5_cnt"] > 0, acc["d5_cnt"], np.nan))
    strat = det.loc[det.combo == win_cid].copy()
    base = det.loc[det.combo == f"B{win_pid}"].copy()
    print(f"[DETAIL] strategy {len(strat)} trades, baseline {len(base)} trades", flush=True)
    strat.to_csv(OUTDIR / "trades_strategy.csv", index=False, encoding="utf-8-sig")
    base.to_csv(OUTDIR / "trades_baseline.csv", index=False, encoding="utf-8-sig")

    periods = {"IS": (IS_START, IS_END), "OOS": (OOS_START, as_of)}
    stats = {}
    for p, (a, bnd) in periods.items():
        stats[f"strategy_{p}"] = period_stats(strat, ctrl_mean, dates, a, bnd)
        stats[f"baseline_{p}"] = period_stats(base, ctrl_mean, dates, a, bnd)

    # ---- 20-slot portfolios, RPS120 priority (engine.portfolio) ----
    close_ff = pd.DataFrame(panel.field("close")).T.ffill().T.to_numpy(dtype=np.float32)
    code_row = {str(cd): i for i, cd in enumerate(panel.codes)}
    windows = {"IS": (int(cal.searchsorted(IS_START)), int(cal.searchsorted(IS_END, side="right") - 1)),
               "OOS": (int(cal.searchsorted(OOS_START)), len(cal) - 1),
               "FULL": (int(cal.searchsorted(IS_START)), len(cal) - 1)}
    pf_stats, pf_trades_full = {}, {}
    for name, df in (("strategy", strat), ("baseline", base)):
        for w, (a, bnd) in windows.items():
            if w == "FULL" and name == "baseline":
                continue
            curve, trades = portfolio(df, "open", close_ff, code_row, dates, a, bnd, SLOTS)
            pf_stats[f"{name}_{w}"] = curve_stats(curve)
            if w == "FULL":
                pf_trades_full[name] = trades
        print(f"[PORTFOLIO] {name} done", flush=True)

    # ---- execution sheet at the last session ----
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from run_daily_rps import names_from_tnf
        names = names_from_tnf(Path("C:/new_tdx/T0002/hq_cache"))
    except Exception as exc:
        print(f"[WARN] names unavailable: {exc}", flush=True)
        names = {}
    last_date = dates[-1]

    def live_fields(code: str) -> dict:
        i = code_row[code]
        cols = np.flatnonzero(~np.isnan(panel.field("close")[i]))
        cx = panel.field("close")[i, cols].astype(np.float64)
        ma20 = cx[-20:].mean()
        lim = float(limit_pct(code, np.array([last_date]))[0])
        return {"名称": names.get(code, "缺"), "最新收盘": round(float(cx[-1]), 3),
                "MA20": round(float(ma20), 3),
                "涨停价": round(float(cx[-1]) * (1 + lim) + 1e-9, 2),
                "跌破MA20临界收盘": round(float(cx[-19:].sum() / 19) + 1e-9, 3),
                "RPS120": round(float(r120_mat[i, cols[-1]]), 1)}

    trades_full = pf_trades_full.get("strategy", pd.DataFrame())
    held = trades_full.loc[trades_full.pf_unfinished] if len(trades_full) else trades_full
    sell_rows, hold_rows = [], []
    for r in held.itertuples():
        i = code_row[r.code]
        cols = np.flatnonzero(~np.isnan(panel.field("close")[i]))
        cx = panel.field("close")[i, cols].astype(np.float64)
        ma20_last = cx[-20:].mean()
        sell = bool((cx[-1] < ma20_last) or (cx[-1] < r.pullback_low))
        row = {"代码": r.code, **live_fields(r.code),
               "信号日": str(pd.Timestamp(dates[r.sig_cal]).date()),
               "横盘低点": round(float(r.base_low), 3),
               "回踩低点": round(float(r.pullback_low), 3),
               "动作": "下一开盘卖出" if sell else "继续持有"}
        (sell_rows if sell else hold_rows).append(row)
    room = max(0, SLOTS - len(hold_rows))
    buy_rows = []
    if len(live):
        cand = live.loc[live.combo == win_cid].sort_values("rps120", ascending=False)
        for rank, r in enumerate(cand.itertuples()):
            buy_rows.append({"代码": r.code, **live_fields(r.code),
                             "横盘低点": round(float(r.base_low), 3),
                             "回踩低点": round(float(r.pullback_low), 3),
                             "RPS120": round(float(r.rps120), 1),
                             "动作": "下一开盘买入" if rank < room else "名额外候选"})
    exec_df = pd.DataFrame([{**r, "方向": "持仓"} for r in sell_rows + hold_rows]
                           + [{**r, "方向": "买入"} for r in buy_rows])
    exec_df.to_csv(OUTDIR / "执行单.csv", index=False, encoding="utf-8-sig")

    # ---- historical next-day / next-week comparison (frozen strategy signals) ----
    hist_out = pd.DataFrame({
        "信号日": pd.DatetimeIndex(dates[strat.sig_cal.to_numpy()]).date,
        "代码": strat.code.to_numpy(),
        "信号收盘": strat.sig_close.to_numpy(),
        "隔日收益%": strat.d1.to_numpy() * 100,
        "隔周收益%": strat.d5.to_numpy() * 100,
        "对照隔日均值%": ctrl_d1.reindex(strat.sig_cal.to_numpy()).to_numpy() * 100,
        "对照隔周均值%": ctrl_d5.reindex(strat.sig_cal.to_numpy()).to_numpy() * 100,
    })
    hist_out["隔日超额%"] = hist_out["隔日收益%"] - hist_out["对照隔日均值%"]
    hist_out["隔周超额%"] = hist_out["隔周收益%"] - hist_out["对照隔周均值%"]
    hist_out.to_csv(OUTDIR / "历史对照.csv", index=False, encoding="utf-8-sig")

    # ---- verdict ----
    oos, boos = stats["strategy_OOS"], stats["baseline_OOS"]
    ci_cross = oos.get("excess_ci_lo_pct", -1) <= 0 <= oos.get("excess_ci_hi_pct", 1)
    below_base = oos.get("excess_mean_pct", -1) < boos.get("excess_mean_pct", 0)
    verdict = "没有提升" if (ci_cross or below_base) else "样本外有提升"

    payload = {
        "as_of": str(as_of.date()),
        "generated_at": datetime.now().isoformat(),
        "frozen": {"combo_id": win_cid, "desc": combo_desc(win_cid),
                   "pattern": dict(zip(("decline_bars", "decline_min", "base_bars",
                                        "base_range_max", "vol_contract", "vol_expand"),
                                       PATTERN_GRID[win_pid])),
                   "pullback_bars": win_K, "pullback_tol": win_tol},
        "stats": stats,
        "portfolio": pf_stats,
        "oos_excess_diff_vs_baseline_pct": (oos.get("excess_mean_pct", np.nan)
                                            - boos.get("excess_mean_pct", np.nan)),
        "verdict": verdict,
        "panel": {k: v for k, v in paudit.items() if k != "excluded_detail"},
        "grid_size": N_COMBOS,
        "selection": f"IS trades>={MIN_IS_TRADES}, rank by IS excess mean, tie -> more trades",
    }
    (OUTDIR / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                       encoding="utf-8")

    # ---- 复盘.md ----
    def f(x, nd=2):
        return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"

    def srow(label, s, pfkey):
        p = pf_stats.get(pfkey, {})
        if not s or s.get("trades", 0) == 0:
            return f"| {label} | 0 | — | — | — | — | — | — |"
        return (f"| {label} | {s['trades']} | {f(s['mean_ret_pct'])} | {f(s['control_mean_pct'])} | "
                f"{f(s['excess_mean_pct'])} | [{f(s['excess_ci_lo_pct'])}, {f(s['excess_ci_hi_pct'])}] | "
                f"{f(p.get('cagr_pct'))} | {f(p.get('max_drawdown_pct'))} |")

    db, dmin, bb, brmax, vc, ve = PATTERN_GRID[win_pid]
    lines = [
        "# 四段式回踩策略复盘",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M}；面板最后交易日：{as_of.date()}；"
        f"样本内 2016-09-01 ~ 2021-12-31，样本外 2022-01-01 ~ {as_of.date()}。",
        "",
        f"## 1. 冻结参数（样本内网格 {N_COMBOS} 组，成交 ≥ {MIN_IS_TRADES} 笔，"
        "按样本内超额均值排序，并列取笔数多者）",
        "",
        f"- 下跌：{db} 根，跌幅 ≥ {int(dmin*100)}%（高点在前半段、低点在后半段，横盘起点仍低于高点 10%）",
        f"- 横盘：{bb} 根，振幅 ≤ {int(brmax*100)}%（漂移 ≤ 8%，不破下跌段低点）",
        f"- 缩量：横盘均量 / 下跌均量 ≤ {vc}",
        f"- 放量：阳线收盘站上 MA20，量 ≥ 横盘均量 × {ve}",
        f"- 回踩：放量后 {win_K} 根内每根收盘 ≥ MA20，首根收盘 ≤ MA20×(1+{int(win_tol*100)}%) "
        "且回踩均量 < 放量日量；信号收盘确认，下一交易日开盘买入",
        "",
        "## 2. 样本内 / 样本外（成本：买入万2.5+滑点0.1%；卖出同上加印花税；20 只组合按 RPS120 优先占名额）",
        "",
        "| 区间 | 笔数 | 平均收益% | 同日对照均值% | 超额% | 超额95%区间 | 组合年化% | 组合最大回撤% |",
        "|---|---|---|---|---|---|---|---|",
        srow("样本内", stats["strategy_IS"], "strategy_IS"),
        srow("样本外", stats["strategy_OOS"], "strategy_OOS"),
        "",
        "## 3. 基线（放量当天买入、不回踩，同形态参数，同一引擎重算）样本外对比",
        "",
        "| 口径 | 笔数 | 平均收益% | 同日对照均值% | 超额% | 超额95%区间 | 组合年化% | 组合最大回撤% |",
        "|---|---|---|---|---|---|---|---|",
        srow("基线 样本外", stats["baseline_OOS"], "baseline_OOS"),
        srow("本策略 样本外", stats["strategy_OOS"], "strategy_OOS"),
        "",
        f"样本外超额差额（本策略 − 基线）："
        f"{f(oos.get('excess_mean_pct', np.nan) - boos.get('excess_mean_pct', np.nan))} 个百分点；"
        f"平均收益差额：{f(oos.get('mean_ret_pct', np.nan) - boos.get('mean_ret_pct', np.nan))} 个百分点。",
        "",
        f"**结论：{verdict}**" + ("（样本外超额区间跨过 0 或低于基线，按既定规则不另挑赢家。）"
                                 if verdict == "没有提升" else "（样本外超额区间整体在 0 以上且高于基线。）"),
        "",
        f"## 4. 最新交易日（{as_of.date()}）执行单",
        "",
        f"当前持有 {len(sell_rows) + len(hold_rows)} 只，下一开盘卖出 {len(sell_rows)} 只；"
        f"空余名额 {room} 个，今日收盘新买入信号 {len(buy_rows)} 个（按 RPS120 从高到低，开盘涨停不买）。",
        "",
    ]
    if sell_rows:
        lines += ["### 下一开盘卖出", "",
                  "| 代码 | 名称 | 最新收盘 | 涨停价 | MA20 | 跌破MA20临界收盘 | 回踩低点 | 横盘低点 | 信号日 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in sell_rows:
            lines.append(f"| {r['代码']} | {r['名称']} | {r['最新收盘']} | {r['涨停价']} | {r['MA20']} | "
                         f"{r['跌破MA20临界收盘']} | {r['回踩低点']} | {r['横盘低点']} | {r['信号日']} |")
        lines.append("")
    if buy_rows:
        lines += ["### 下一开盘买入（信号已在今日收盘确认）", "",
                  "| 代码 | 名称 | 最新收盘 | 涨停价 | 横盘低点 | 回踩低点 | MA20 | 跌破MA20临界收盘 | RPS120 | 动作 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in buy_rows:
            lines.append(f"| {r['代码']} | {r['名称']} | {r['最新收盘']} | {r['涨停价']} | {r['横盘低点']} | "
                         f"{r['回踩低点']} | {r['MA20']} | {r['跌破MA20临界收盘']} | {r['RPS120']} | {r['动作']} |")
        lines.append("")
    if hold_rows:
        lines += ["### 继续持有（未触发卖出）", "",
                  "| 代码 | 名称 | 最新收盘 | 涨停价 | MA20 | 跌破MA20临界收盘 | 回踩低点 | 横盘低点 | 信号日 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in hold_rows:
            lines.append(f"| {r['代码']} | {r['名称']} | {r['最新收盘']} | {r['涨停价']} | {r['MA20']} | "
                         f"{r['跌破MA20临界收盘']} | {r['回踩低点']} | {r['横盘低点']} | {r['信号日']} |")
        lines.append("")
    lines += [
        "## 5. 口径备注",
        "",
        "- 对照组：同一天收盘 > MA20 且 20 日均成交额 ≥ 1 亿元的股票，同一买卖时点（信号收盘 → 次日开盘买，收盘破 MA20 → 次日开盘卖）。",
        "- 区间：月度区块自助抽样 2000 次、种子 7 的 95% 区间。",
        "- 卖出：收盘跌破 MA20 或收盘跌破该次回踩低点，先到先卖，下一交易日开盘执行；跌停/一字跌停按 engine 规则顺延。",
        "- 涨跌停幅度：科创板 20%，创业板 2020-08-24 起 20%，其余 10%；印花税 2023-08-28 前千 1、后万 5。",
        "- 停牌导致信号日与次日不相邻的不买；同一只未卖出前不重复买；最多 20 只，同日按 RPS120 从高到低占名额。",
        "- 隔日/隔周对照见 `历史对照.csv`：隔日 = 信号日后第 1 个交易日收盘 / 信号收盘 − 1；隔周 = 第 5 个交易日。",
    ]
    (OUTDIR / "复盘.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[DONE] {OUTDIR}", flush=True)
    print(json.dumps({"frozen": payload["frozen"], "stats": stats, "portfolio": pf_stats,
                      "verdict": verdict, "sells": len(sell_rows), "holds": len(hold_rows),
                      "buys": len(buy_rows)}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid-part", nargs=2, type=int, metavar=("START", "END"))
    ap.add_argument("--assemble", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.grid_part:
        panel = load_panel(CACHE)
        z = np.load(CACHE / "rps.npz")
        run_grid_part(panel, z["RPS120"], args.grid_part[0], args.grid_part[1])
        return 0
    if args.assemble:
        return assemble()
    if args.report:
        return report()
    ap.error("need --grid-part START END or --assemble or --report")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
