"""顺向火车轨 (train-track) trend-template signals, replicated from the TDX
formula the owner provided. Pure numpy/pandas over the frozen panel.

TDX -> panel mapping:
  EXTDATA_USER(6/7) RPS120/RPS250 -> in-panel cross-sectional percentile rank
    of trailing 120/250-day return * 100 (equivalent definition).
  回撤幅度 block -> (HHV(H,20) - LLV(L,20)) / HHV(H,20) <= 0.25
    (close approximation of the TDX HHVBARS/LLVBARS chain; slightly stricter).
  VOL/CAPITAL*100 < 10 (turnover) -> OMITTED: the panel has no float-share
    data. Signals are therefore slightly broader than the TDX original.
  PERIOD=5 -> panel is daily only; trivially satisfied.

Research only; signals, no buy/sell instructions.
"""

from __future__ import annotations

import pandas as pd

RPS_SUM_MIN = 185.0      # adjustable per formula note (180~185)
RANGE20_MAX = 0.25       # 20d high-low range cap
NEAR_HIGH250 = 0.80      # close >= 80% of 250d high (0.75~0.85 adjustable)
DEDUP_BARS = 8           # first occurrence within 8 bars (8~15 adjustable)


def _r(df: pd.DataFrame, w: int, fn: str) -> pd.DataFrame:
    """Chunked rolling over columns (days) to bound memory. df: stocks x days."""
    parts = []
    step = 500
    for s in range(0, len(df), step):
        blk = df.iloc[s:s + step].T  # days x stocks
        parts.append(getattr(blk.rolling(w, min_periods=w), fn)().T)
    return pd.concat(parts)


def rps_cross_sectional(c: pd.DataFrame, lb: int) -> pd.DataFrame:
    """Cross-sectional percentile rank (0-100) of trailing lb-day return."""
    ret = c / c.shift(lb, axis=1) - 1
    return ret.rank(axis=0, pct=True) * 100


def train_track_signals(o: pd.DataFrame, h: pd.DataFrame, lo: pd.DataFrame,
                        c: pd.DataFrame,
                        rps_sum_min: float = RPS_SUM_MIN,
                        range20_max: float = RANGE20_MAX,
                        near_high: float = NEAR_HIGH250,
                        dedup: int = DEDUP_BARS) -> pd.DataFrame:
    """Boolean (stocks x days): first-in-8-bars train-track signal day.

    Frames are stocks x days (index=code, columns=calendar position).
    """
    ma10 = _r(c, 10, "mean")
    ma20 = _r(c, 20, "mean")
    ma200 = _r(c, 200, "mean")
    ma250 = _r(c, 250, "mean")

    # G1: RPS120 + RPS250 > threshold
    g1 = (rps_cross_sectional(c, 120) + rps_cross_sectional(c, 250)) > rps_sum_min

    # G2: trend alive across horizons
    above20 = c > ma20
    above250_30 = _r((c > ma250).astype(float), 30, "sum") >= 25
    above200_30 = _r((c > ma200).astype(float), 30, "sum") >= 25
    a23 = _r(above20.astype(float), 10, "sum") >= 9
    a24 = ((_r((c > ma10).astype(float), 4, "sum") >= 3)
           & (_r(above20.astype(float), 4, "sum") >= 3))
    g2 = above20 & above250_30 & above200_30 & (a23 | a24)

    # G3: orderly consolidation + near 250d high
    hhv20 = _r(h, 20, "max")
    llv20 = _r(lo, 20, "min")
    range20 = (hhv20 - llv20) / hhv20
    g3 = (range20 <= range20_max) & (c / _r(c, 250, "max") > near_high)

    # G4: train-track MAs (strict 5d version OR loose today version)
    ma20_up = ma20 >= ma20.shift(1, axis=1)
    ma10_up = ma10 >= ma10.shift(1, axis=1)
    ma10_above = ma10 >= ma20
    g41 = (_r(ma20_up.astype(float), 5, "min") == 1) & \
          (_r(ma10_above.astype(float), 5, "min") == 1)
    g42 = ma20_up & ma10_up & ma10_above
    g4 = g41 | g42

    sig = (g1 & g2 & g3 & g4).fillna(False)

    # BARSSINCEN(sig, dedup)=0: first True within the last `dedup` bars
    recent = _r(sig.astype(float), dedup, "sum").shift(1, axis=1)
    first = sig & (recent.fillna(0) == 0)
    return first
