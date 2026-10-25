"""Decline, dry sideways base, then a right-side volume day.

Windows are trading bars and end at the signal bar. Nothing after the signal
bar is read. Thresholds are fixed; they are not fit to a return sample.

  decline: the 40 bars before the base fell at least 15%, high in the first
           half and low in the second half, and at least 10% of that drop is
           still open when the base starts.
  base:    the next 40 bars stay inside a 15% high-low range, drift at most 8%,
           and do not undercut the decline low.
  dry:     mean volume of the base is at most 70% of mean volume in the decline.
  right:   signal bar is an up close above MA20 and above the base median,
           with volume at least 1.5 times the base mean.
"""

from __future__ import annotations

import numpy as np

DECLINE_BARS = 40
BASE_BARS = 40
DECLINE_MIN = 0.15
REMAINING_DROP = 0.10
BASE_RANGE_MAX = 0.15
BASE_DRIFT_MAX = 0.08
VOL_CONTRACT = 0.70
VOL_EXPAND = 1.5
RPS_MIN = 87.0
MIN_BARS = 251

PHASES = ("decline", "base", "dry", "right", "pattern")


def _roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    cs = np.cumsum(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    out[n - 1 :] = (cs[n - 1 :] - np.concatenate(([0.0], cs[:-n]))) / n
    return out


def _window(x: np.ndarray, n: int, start: np.ndarray) -> np.ndarray:
    view = np.lib.stride_tricks.sliding_window_view(x, n)
    return view[start]


def phase_flags(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
                v: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean phase flags aligned to bars. False until 80 prior bars exist."""
    n = len(c)
    flags = {k: np.zeros(n, dtype=bool) for k in PHASES}
    flags["base_low"] = np.full(n, np.nan)
    flags["ma20"] = np.full(n, np.nan)
    need = DECLINE_BARS + BASE_BARS
    if n <= need:
        return flags
    s = np.arange(n - need)
    t = s + need
    dec = _window(c, DECLINE_BARS, s)
    dec_lo = _window(lo, DECLINE_BARS, s)
    dec_v = _window(v, DECLINE_BARS, s)
    base = _window(c, BASE_BARS, s + DECLINE_BARS)
    base_h = _window(h, BASE_BARS, s + DECLINE_BARS)
    base_l = _window(lo, BASE_BARS, s + DECLINE_BARS)
    base_v = _window(v, BASE_BARS, s + DECLINE_BARS)
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
    decline = (
        (drop >= DECLINE_MIN)
        & (peak_at < DECLINE_BARS // 2)
        & (trough_at >= DECLINE_BARS // 2)
        & (remain <= -REMAINING_DROP)
    )
    base_ok = (
        (base_range <= BASE_RANGE_MAX)
        & (drift <= BASE_DRIFT_MAX)
        & (base_l.min(axis=1) >= dec_lo.min(axis=1))
        & np.isfinite(base_range)
    )
    dry = np.isfinite(dec_mean) & (dec_mean > 0) & (base_mean <= VOL_CONTRACT * dec_mean)
    ma20 = _roll_mean(c, 20)
    med = np.median(base, axis=1)
    right = (
        (c[t] > o[t])
        & (c[t] > c[t - 1])
        & (c[t] > ma20[t])
        & (c[t] > med)
        & (v[t] >= VOL_EXPAND * base_mean)
    )
    flags["decline"][t] = decline
    flags["base"][t] = base_ok
    flags["dry"][t] = dry
    flags["right"][t] = right
    flags["pattern"][t] = decline & base_ok & dry & right
    flags["base_low"][t] = base_l.min(axis=1)
    flags["ma20"] = ma20
    return flags


def a_trend_mask(c: np.ndarray, v: np.ndarray, r120: np.ndarray, r250: np.ndarray) -> np.ndarray:
    """Same gates as stock_signals column A_trend, on one stock's traded bars."""
    n = len(c)
    out = np.zeros(n, dtype=bool)
    if n < MIN_BARS:
        return out
    ma20, ma60, ma120 = _roll_mean(c, 20), _roll_mean(c, 60), _roll_mean(c, 120)
    year = np.lib.stride_tricks.sliding_window_view(c, 250)
    ymax = np.full(n, np.nan)
    ymin = np.full(n, np.nan)
    ymax[249:] = year.max(axis=1)
    ymin[249:] = year.min(axis=1)
    dd = _drawdown20(c)
    delta = np.diff(c, prepend=np.nan)
    down = (delta < 0).astype(float)
    up = (delta > 0).astype(float)
    down_sum = _roll_sum(v * down, 20)
    up_sum = _roll_sum(v * up, 20)
    down_n = _roll_sum(down, 20)
    up_n = _roll_sum(up, 20)
    with np.errstate(invalid="ignore", divide="ignore"):
        down_mean = down_sum / down_n
        up_mean = up_sum / up_n
        off_high = (1 - c / ymax) * 100
        off_low = (c / ymin - 1) * 100
    ma60_prev = np.r_[np.full(5, np.nan), ma60[:-5]]
    out = (
        (np.arange(n) >= MIN_BARS - 1)
        & (c > ma20) & (ma20 > ma60) & (ma60 > ma120)
        & (ma60 > ma60_prev)
        & (off_high <= 25) & (off_low >= 30)
        & (dd <= 15)
        & (down_mean < up_mean)
        & (r120 >= RPS_MIN) & (r250 >= RPS_MIN)
    )
    return np.asarray(out, dtype=bool) & np.isfinite(ma120)


def _roll_sum(x: np.ndarray, n: int) -> np.ndarray:
    cs = np.cumsum(x, dtype=np.float64)
    out = np.full(len(x), np.nan)
    out[n - 1 :] = cs[n - 1 :] - np.concatenate(([0.0], cs[:-n]))
    return out


def _drawdown20(c: np.ndarray) -> np.ndarray:
    res = np.full(len(c), np.nan)
    if len(c) < 20:
        return res
    w = np.lib.stride_tricks.sliding_window_view(c, 20)
    res[19:] = (1 - w / np.maximum.accumulate(w, axis=1)).max(axis=1) * 100
    return res
