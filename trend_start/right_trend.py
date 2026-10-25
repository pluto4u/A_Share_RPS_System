"""思瑞浦同形右侧趋势发现器（YJ 预警 / QD 启动 / QR 确认 / QQR 强确认）的向量化实现，外加放量因子候选。

对象是 trend_start.panel.Panel（股票 x 交易日矩阵）。公式逐条对应用户提供的通达信公式；V2 在其上叠加仓库里已有的
结构与容量过滤（高点在低点之前、20日均成交额≥1亿、不追高、不过度爆量），与 backtest_siruipu_right_side.py 的 V2 一致。
停牌：收盘价最多向前填充 10 个交易日（成交量记 0），保持均线连续；当天没有真实成交的股票不会出信号。
全部信号只用 t 日及以前的数据。仅为研究/观察，不产生交易指令。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from trend_start.panel import Panel

ADV_MIN = 1.0e8
VOLUME_FACTORS = ("vf1_expand", "vf2_moderate", "vf3_dry_then_surge", "vf4_updown", "vf5_pocket", "vf6_dry_pocket")
VF_LABELS = {
    "vf1_expand": "放量：当日量≥20日均量×1.2",
    "vf2_moderate": "温和放量：1.2×≤当日量/20日均量≤3×",
    "vf3_dry_then_surge": "缩量后放量：前10日均量≤60日均量×0.85，且当日量≥前5日均量×1.3",
    "vf4_updown": "上涨量占优：近10日上涨日成交量合计≥下跌日合计×1.5",
    "vf5_pocket": "口袋支点：上涨且当日量>前10日内最大的下跌日成交量",
    "vf6_dry_pocket": "缩量后放量 + 口袋支点",
}


def roll(a: np.ndarray, w: int, fn: str, min_periods: int | None = None) -> np.ndarray:
    return getattr(pd.DataFrame(a).T.rolling(w, min_periods=w if min_periods is None else min_periods), fn)().T.to_numpy(np.float32)


def ema(a: np.ndarray, span: int) -> np.ndarray:
    return pd.DataFrame(a).T.ewm(span=span, adjust=False).mean().T.to_numpy(np.float32)


def shift(a: np.ndarray, k: int, fill=np.nan) -> np.ndarray:
    out = np.full_like(a, fill)
    if k > 0:
        out[:, k:] = a[:, :-k]
    elif k == 0:
        out[:] = a
    return out


def bars_since_extreme(a: np.ndarray, n: int, mode: str, block: int = 80) -> np.ndarray:
    """距窗口内极值的根数（0=当根）；并列取最近一根，与通达信 LLVBARS/HHVBARS 及仓库研究实现一致。窗口含 NaN 则为 NaN。"""
    out = np.full(a.shape, np.nan, dtype=np.float32)
    pick = np.argmin if mode == "min" else np.argmax
    for s in range(0, a.shape[0], block):
        w = sliding_window_view(a[s:s + block], n, axis=1)
        bad = np.isnan(w).any(axis=-1)
        age = pick(np.where(np.isnan(w), np.inf if mode == "min" else -np.inf, w)[..., ::-1], axis=-1).astype(np.float32)
        age[bad] = np.nan
        out[s:s + block, n - 1:] = age
    return out


@dataclass
class RightTrend:
    codes: np.ndarray
    dates: np.ndarray
    yj1: np.ndarray
    qd1: np.ndarray
    qr1: np.ndarray
    qqr1: np.ndarray
    yj2: np.ndarray
    qd2: np.ndarray
    qr2: np.ndarray
    qqr2: np.ndarray
    vf: dict[str, np.ndarray]
    aux: dict[str, np.ndarray] = field(default_factory=dict)

    def confirm(self) -> np.ndarray:
        return self.qd2 | self.qr2 | self.qqr2


def fill_suspensions(panel: Panel, limit: int = 10) -> dict[str, np.ndarray]:
    c = panel.field("close").astype(np.float32)
    cf = pd.DataFrame(c).T.ffill(limit=limit).T.to_numpy(np.float32)
    traded = np.isfinite(c)
    out = {"close": cf, "traded": traded}
    for f in ("open", "high", "low"):
        a = panel.field(f).astype(np.float32)
        out[f] = np.where(np.isfinite(a), a, cf)
    v = panel.field("vol").astype(np.float32)
    out["vol"] = np.where(np.isfinite(v), v, 0.0).astype(np.float32)
    amt = panel.field("amount").astype(np.float32)
    out["amount"] = np.where(np.isfinite(amt), amt, 0.0).astype(np.float32)
    return out


def compute(panel: Panel, st: np.ndarray | None = None) -> RightTrend:
    """st：形状 (n,) 的布尔数组，True 表示剔除（ST/退市）。"""
    d = fill_suspensions(panel)
    c, h, lo, v, amt, traded = d["close"], d["high"], d["low"], d["vol"], d["amount"], d["traded"]
    n, m = c.shape
    ma = {k: roll(c, k, "mean") for k in (5, 10, 20, 60, 120)}
    vm20, vm60 = roll(v, 20, "mean"), roll(v, 60, "mean")
    adv20 = roll(amt, 20, "mean")
    dif = ema(c, 12) - ema(c, 26)
    mh = 2 * (dif - ema(dif, 9))
    hhv60 = roll(h, 60, "max")
    llv30 = roll(lo, 30, "min")
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = (hhv60 - llv30) / hhv60 >= 0.28
    lowage = bars_since_extreme(lo, 30, "min")
    peakage = bars_since_extreme(h, 60, "max")
    lownear = lowage <= 15
    order_ok = peakage > lowage
    hh5, hh10, hh20 = (shift(roll(h, k, "max"), 1) for k in (5, 10, 20))
    m5_ref2, mh_ref1 = shift(ma[5], 2), shift(mh, 1)
    bars = np.cumsum(traded, axis=1)
    base = traded & (bars > 250)
    if st is not None:
        base &= ~st[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        c_m20 = c / ma[20]
        yj1 = dd & lownear & (c > ma[20]) & (ma[5] > ma[10]) & (ma[5] > m5_ref2) & (c > hh5) & (mh > 0) & (mh > mh_ref1) & (c_m20 <= 1.12)
        qd1 = dd & lownear & (c > ma[20]) & (ma[5] > ma[10]) & (c > ma[120]) & (c > hh10) & (v >= vm20 * 1.10) & (mh > 0)
        qr1 = dd & (c > ma[60]) & (ma[10] > ma[20]) & (c > hh20) & (mh > 0)
        qqr1 = qr1 & (v >= vm20 * 1.80) & (dif > 0)
        structural = order_ok & lownear
        liquid = adv20 >= ADV_MIN
        yj2 = yj1 & structural & liquid
        qd2 = qd1 & structural & (c_m20 <= 1.15) & (v <= vm20 * 3.0) & liquid
        qr2 = qr1 & structural & (c_m20 <= 1.18) & liquid
        qqr2 = qr2 & (v >= vm20 * 1.80) & (dif > 0)

        prev_c = shift(c, 1)
        up = c > prev_c
        vup, vdn = np.where(up, v, 0.0).astype(np.float32), np.where(c < prev_c, v, 0.0).astype(np.float32)
        v_prev10 = shift(roll(v, 10, "mean"), 1)
        v_prev5 = shift(roll(v, 5, "mean"), 1)
        up10, dn10 = roll(vup, 10, "sum"), roll(vdn, 10, "sum")
        max_dn10_prev = shift(roll(vdn, 10, "max"), 1)
        vf1 = v >= vm20 * 1.2
        vf2 = vf1 & (v <= vm20 * 3.0)
        vf3 = (v_prev10 <= vm60 * 0.85) & (v >= v_prev5 * 1.3)
        vf4 = (dn10 > 0) & (up10 >= dn10 * 1.5)
        vf5 = up & (v > max_dn10_prev)
        vf = {"vf1_expand": vf1, "vf2_moderate": vf2, "vf3_dry_then_surge": vf3, "vf4_updown": vf4, "vf5_pocket": vf5,
              "vf6_dry_pocket": vf3 & vf5}
    sig = {k: np.nan_to_num(x, nan=0).astype(bool) & base for k, x in
           dict(yj1=yj1, qd1=qd1, qr1=qr1, qqr1=qqr1, yj2=yj2, qd2=qd2, qr2=qr2, qqr2=qqr2).items()}
    vf = {k: np.nan_to_num(x, nan=0).astype(bool) for k, x in vf.items()}
    aux = {"ma5": ma[5], "ma10": ma[10], "ma20": ma[20], "ma60": ma[60], "ma120": ma[120], "vm20": vm20, "adv20": adv20,
           "dif": dif, "mh": mh, "lowage": lowage, "peakage": peakage, "base": base, "close": c}
    return RightTrend(panel.codes.astype(str), panel.dates, vf=vf, aux=aux, **sig)
