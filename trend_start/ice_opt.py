"""Ice-point entry parameter grids and exit-variant simulation (research only).

Grid evaluation reuses the frozen trade ledger; exit variants re-simulate only
the selected entries. All rules are causal; selection is pre-declared:
choose on IS, validate on OOS, report the full grid.

Exit variants for an ice-day tail entry (buy at signal close, T+1):
  fixed_h      sell at close of entry+h (limit-down locked close rolls)
  invalidation frozen floor / two closes below pivot*0.98 -> next open
  target_p     first close >= entry*(1+p) -> sell at that close; else invalidation/time
  repair_day   first market repair day after entry -> sell at that close
  climax_day   first overheat day (advance_share high) after entry -> sell at that close
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trend_start.icepoint import (
    ICE_ADVANCE_EXTREME,
    ICE_STREAK_MAX,
    MONEY_SHRINK,
    WEAKENING_PREV,
)
from trend_start.pattern_backtest import _sell_close, _sell_next_open, exec_flags


@dataclass(frozen=True)
class IceGateParams:
    advance_thr: float = 0.22      # 市场冰点: 上涨家数占比上限
    min_streak: int = 1            # 连续冰点日数要求
    require_weakening: bool = True  # 由弱转更弱 (前日已在低位)
    resonance: str = "none"        # none | any | money | sentiment


def ice_gate_mask(sent: pd.DataFrame, p: IceGateParams) -> np.ndarray:
    """Boolean per session: parameterized ice-day gate. Causal (<=t data only)."""
    adv = sent.advance_share.to_numpy()
    zt = sent.zt_count.to_numpy()
    prev_adv = np.r_[np.nan, adv[:-1]]
    ice = adv <= p.advance_thr
    streak = np.zeros(len(adv), dtype=np.int64)
    for t in range(1, len(adv)):
        streak[t] = streak[t - 1] + 1 if ice[t] else 0
    if ice[0]:
        streak[0] = 1
    ok = streak >= p.min_streak
    if p.require_weakening:
        ok &= prev_adv <= WEAKENING_PREV
    sentiment = (sent.max_streak.to_numpy() <= ICE_STREAK_MAX) & (
        zt <= sent.zt_median60.to_numpy() / 2
    )
    money = sent.amount_ratio.to_numpy() <= MONEY_SHRINK
    if p.resonance == "any":
        ok &= sentiment | money
    elif p.resonance == "money":
        ok &= money
    elif p.resonance == "sentiment":
        ok &= sentiment
    return np.asarray(ok, dtype=bool)


def extreme_mask(sent: pd.DataFrame) -> np.ndarray:
    return np.asarray(sent.advance_share.to_numpy() <= ICE_ADVANCE_EXTREME)


EXIT_VARIANTS = ("fixed1", "fixed2", "fixed3", "fixed5", "fixed10",
                 "invalidation", "target5", "target8", "repair_day", "climax_day")


def simulate_exit_variants(
    code: str,
    e: int,
    pivot: float,
    floor: float,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cal: np.ndarray,
    dates: np.ndarray,
    repair_days: np.ndarray,
    climax_days: np.ndarray,
    max_hold: int = 10,
    close_below: float = 0.02,
) -> dict[str, tuple[int, float, str, bool]]:
    """All exit variants for a tail entry at bar e.

    Returns variant -> (bar, px, reason, at_close).
    """
    n = len(c)
    fl = exec_flags(code, o, h, lo, c, dates)
    down_open, locked = fl["down_open"], fl["locked"]
    out: dict[str, tuple[int, float, str, bool]] = {}
    entry_px = float(c[e])

    def finish(bar: int, px: float, reason: str, at_close: bool) -> tuple[int, float, str, bool]:
        return bar, px, reason, at_close

    below = 0
    inv_bar = n  # invalidation trigger bar
    for j in range(e, n):
        below = below + 1 if c[j] < pivot * (1 - close_below) else 0
        if c[j] < floor or below >= 2:
            inv_bar = j
            break
    # invalidation exit: sell next open after trigger
    if inv_bar < n:
        k, px, unf, ac = _sell_next_open(inv_bar, n, down_open, locked, o, c)
        out["invalidation"] = finish(k, px, "invalidation" if not unf else "unfinished", ac)
    else:
        out["invalidation"] = finish(n - 1, float(c[-1]), "unfinished", True)

    for hz in (1, 2, 3, 5, 10):
        j = min(e + hz, n - 1)
        k, px, unf, ac = _sell_close(j, n, locked, down_open, o, c)
        out[f"fixed{hz}"] = finish(k, px, "time_stop" if not unf else "unfinished", ac)

    for pct, name in ((0.05, "target5"), (0.08, "target8")):
        hit = n
        for j in range(e + 1, n):  # T+1: earliest exit day is e+1
            if c[j] >= entry_px * (1 + pct):
                hit = j
                break
            if j - e >= max_hold:
                break
        cand = min(hit, min(e + max_hold, n - 1))
        if inv_bar < cand:  # invalidation first
            k, px, unf, ac = _sell_next_open(inv_bar, n, down_open, locked, o, c)
            out[name] = finish(k, px, "invalidation" if not unf else "unfinished", ac)
        else:
            k, px, unf, ac = _sell_close(cand, n, locked, down_open, o, c)
            reason = ("target" if hit <= cand and hit < n
                      else ("time_stop" if not unf else "unfinished"))
            out[name] = finish(k, px, reason, ac)

    for name, mask in (("repair_day", repair_days), ("climax_day", climax_days)):
        trig = n
        for j in range(e + 1, n):
            if mask[int(cal[j])]:
                trig = j
                break
            if j - e >= max_hold:
                break
        cand = min(trig, min(e + max_hold, n - 1))
        if inv_bar < cand:
            k, px, unf, ac = _sell_next_open(inv_bar, n, down_open, locked, o, c)
            out[name] = finish(k, px, "invalidation" if not unf else "unfinished", ac)
        else:
            k, px, unf, ac = _sell_close(cand, n, locked, down_open, o, c)
            reason = (name if trig <= cand and trig < n
                      else ("time_stop" if not unf else "unfinished"))
            out[name] = finish(k, px, reason, ac)
    return out


def simulate_trail_exit(
    code: str,
    e: int,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    dates: np.ndarray,
    hard_stop: float = 0.04,
    trail_pct: float = 0.08,
    max_hold: int = 20,
) -> tuple[int, float, str, bool]:
    """Trailing-stop exit for a tail entry at bar e (research only).

    Rules, all evaluated on closes from bar e+1 (T+1):
      - hard stop: close < entry*(1-hard_stop) -> sell next open;
      - trailing:  close <= peak_close*(1-trail_pct) -> sell next open;
      - time stop: e+max_hold -> sell that close.
    Limit-down handling follows _sell_next_open/_sell_close (locked days roll
    forward). Returns (bar, px, reason, at_close).
    """
    n = len(c)
    fl = exec_flags(code, o, h, lo, c, dates)
    down_open, locked = fl["down_open"], fl["locked"]
    entry_px = float(c[e])
    peak = entry_px  # max close over bars e..j-1 (prior-day peak)
    last = min(e + max_hold, n - 1)
    for j in range(e + 1, last + 1):
        if c[j] < entry_px * (1 - hard_stop):
            bar, px, unf, ac = _sell_next_open(j, n, down_open, locked, o, c)
            return bar, px, ("unfinished" if unf else "hard_stop"), ac
        if c[j] <= peak * (1 - trail_pct):
            bar, px, unf, ac = _sell_next_open(j, n, down_open, locked, o, c)
            return bar, px, ("unfinished" if unf else "trail"), ac
        peak = max(peak, float(c[j]))
    bar, px, unf, ac = _sell_close(last, n, locked, down_open, o, c)
    return bar, px, ("unfinished" if unf else "time_stop"), ac


def load_tdx_industry(tdx_root: str, level: int = 2) -> tuple[dict[str, str], dict[str, str]]:
    """TDX industry mapping: code -> level-N name, plus code->T-code.

    Sources: T0002/hq_cache/tdxhy.cfg (stock -> T code), incon.dat #TDXNHY
    section (T code -> name). GBK encoded.
    """
    from pathlib import Path

    root = Path(tdx_root)
    names: dict[str, str] = {}
    in_section = False
    for raw in (root / "incon.dat").read_bytes().decode("gbk", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            in_section = line == "#TDXNHY"
            continue
        if in_section and "|" in line:
            tcode, name = line.split("|", 1)
            names[tcode.strip()] = name.strip()
    mapping: dict[str, str] = {}
    for raw in (root / "T0002/hq_cache/tdxhy.cfg").read_bytes().decode(
            "gbk", errors="replace").splitlines():
        parts = raw.strip().split("|")
        if len(parts) >= 3 and parts[1].isdigit():
            tcode = parts[2].strip()
            if tcode:
                mapping[parts[1]] = tcode
    depth = {1: 3, 2: 5, 3: 7}[level]
    result: dict[str, str] = {}
    for code, tcode in mapping.items():
        key = tcode[:depth]
        result[code] = names.get(key, names.get(tcode, "未知"))
    return result, names
