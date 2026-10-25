"""KaiPanLa-style sentiment ice-point (冰点) and rebound/reversal resonance, from daily bars.

All indicators are computed from the stock x session daily panel only
(OHLCV + amount), approximating the 开盘啦/情绪周期 toolkit:

  advance_share   上涨家数占比 (close > prev close)
  limit_up/down   涨停/跌停家数 (board-level 10/20%; historical ST 5% bands unknown)
  max_streak      连板高度 = max consecutive sealed limit-ups across stocks
  broken_rate     炸板率 ≈ (盘中触板但未封死) / 触板家数, via high vs close
  premium         昨日涨停今日开盘溢价均值
  amount_ratio    全市场成交额 5 日 / 60 日 (资金面冰点 proxy; 融券余额不可得)

Ice levels (thresholds from published practitioner heuristics, adapted to a
~5000-stock market; first-version settings, not optimized):
  L1 市场冰点:  advance_share <= 22%  (≈1100 家; 极端 <= 13% ≈ 700 家)
  L2 双冰点:    L1 + 情绪冰点 (连板高度 <= 3 且涨停家数 <= 60 日中位的一半)
  L3 三冰点:    L2 + 资金面冰点 (amount_ratio <= 0.8)
  由弱转更弱:    前一日 advance_share <= 35% 才能升到 L2/L3
                (从强转弱打出的低位有效性差, 只能算 L1)

Repair / resonance (次日确认, 区分反弹与反转):
  repair    前一日是冰点, 当日 >=2 项: 上涨占比 >=40%; 跌停锐减 (<=10 或较昨日减半);
            涨跌比 > 1; 涨停家数较昨日翻倍
  reversal  repair + 资金指数共振: 当日成交额/前 5 日均 >= 1.2
            且 (breadth >= 50% 或指数收盘站上 MA20) 且涨停 >= 30 且连板高度 > 3
  rebound_only  repair 成立但共振不全 -> 只按 1-2 天反弹对待

Causality: every value at t uses bars <= t. Research only; no trade instructions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trend_start.engine import LIMIT_TOL

GEM_20PCT = np.datetime64("2020-08-24")

ICE_ADVANCE_STD = 0.22      # 标准短线冰点 (≈700-1200 家 / 5300)
ICE_ADVANCE_EXTREME = 0.13  # 极端大冰点 (<700 家)
WEAKENING_PREV = 0.35       # 由弱转更弱: 前日上涨占比已在低位
ICE_STREAK_MAX = 3          # 短线情绪冰点: 连板高度 <= 3
MONEY_SHRINK = 0.8          # 资金面冰点: 5/60 成交额比
REPAIR_ADVANCE = 0.40       # 修复: 上涨家数回升 (≈2000+ 家)
REPAIR_DT_ABS = 10          # 修复: 跌停锐减绝对线
REVERSAL_AMOUNT = 1.2       # 反转共振: 当日成交额 / 前 5 日均
REVERSAL_BREADTH = 0.50
REVERSAL_ZT = 30
REVERSAL_STREAK = 3


@dataclass(frozen=True)
class IceConfig:
    breadth_ma: int = 20
    vol_short: int = 5
    vol_long: int = 60
    zt_baseline_window: int = 60


DEFAULT_ICE = IceConfig()  # frozen dataclass; safe shared default


def limit_matrix(codes: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Board-level daily limit ratio per stock/session (ST 5% history unknown)."""
    n, m = len(codes), len(dates)
    lim = np.full((n, m), 0.10)
    gem = np.array([str(x).startswith(("300", "301")) for x in codes])
    if gem.any():
        lim[gem] = np.where(dates >= GEM_20PCT, 0.20, 0.10)[None, :]
    kc = np.array([str(x).startswith(("688", "689")) for x in codes])
    if kc.any():
        lim[kc] = 0.20
    return lim


def daily_sentiment(codes: np.ndarray, dates: np.ndarray,
                    o: np.ndarray, h: np.ndarray, c: np.ndarray,
                    amount: np.ndarray, cfg: IceConfig = DEFAULT_ICE) -> pd.DataFrame:
    """Per-session sentiment indicators from panel matrices (float, NaN=missing)."""
    c = c.astype(np.float64)
    o = o.astype(np.float64)
    h = h.astype(np.float64)
    amount = amount.astype(np.float64)
    n, m = c.shape

    prev = np.full_like(c, np.nan)
    prev[:, 1:] = c[:, :-1]
    lim = limit_matrix(codes, dates) - LIMIT_TOL

    with np.errstate(invalid="ignore", divide="ignore"):
        chg = c / prev - 1
        traded = np.isfinite(chg)
        up = traded & (chg > 0)
        down = traded & (chg < 0)
        sealed_up = traded & (chg >= lim)
        sealed_dn = traded & (chg <= -lim)
        touched = traded & (h / prev - 1 >= lim)
    n_traded = traded.sum(axis=0)
    advance_share = np.where(n_traded > 0, up.sum(axis=0) / np.maximum(n_traded, 1), np.nan)
    decline_share = np.where(n_traded > 0, down.sum(axis=0) / np.maximum(n_traded, 1), np.nan)
    zt = sealed_up.sum(axis=0).astype(np.float64)
    dt = sealed_dn.sum(axis=0).astype(np.float64)
    tch = touched.sum(axis=0).astype(np.float64)
    broken_rate = np.where(tch > 0, (tch - zt) / np.maximum(tch, 1), np.nan)

    # 连板高度: current run length of sealed limit-ups, max across stocks
    streak = np.zeros((n, m))
    for t in range(1, m):
        streak[:, t] = np.where(sealed_up[:, t], streak[:, t - 1] + 1, 0.0)
    max_streak = streak.max(axis=0)

    # 昨日涨停今日开盘溢价
    prev_sealed = np.zeros_like(sealed_up)
    prev_sealed[:, 1:] = sealed_up[:, :-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = np.where(prev_sealed, o / prev - 1, np.nan)
    cnt = prev_sealed.sum(axis=0)
    premium = np.where(cnt > 0, np.nansum(gap, axis=0) / np.maximum(cnt, 1), np.nan)

    total_amount = np.where(np.isfinite(amount).any(axis=0), np.nansum(amount, axis=0), np.nan)
    s = pd.Series(total_amount)
    amount_ratio = (s.rolling(cfg.vol_short, min_periods=cfg.vol_short).mean()
                    / s.rolling(cfg.vol_long, min_periods=cfg.vol_long).mean()).to_numpy()
    prev5_amount = s.shift(1).rolling(cfg.vol_short, min_periods=cfg.vol_short).mean().to_numpy()

    ma = pd.DataFrame(c).T.rolling(cfg.breadth_ma, min_periods=cfg.breadth_ma).mean().T.to_numpy()
    valid = np.isfinite(c) & np.isfinite(ma)
    breadth = np.where(valid.sum(axis=0) > 0,
                       ((c > ma) & valid).sum(axis=0) / np.maximum(valid.sum(axis=0), 1),
                       np.nan)

    zt_median = pd.Series(zt).rolling(cfg.zt_baseline_window,
                                      min_periods=cfg.zt_baseline_window).median().to_numpy()

    return pd.DataFrame(
        {
            "advance_share": advance_share,
            "decline_share": decline_share,
            "zt_count": zt,
            "dt_count": dt,
            "broken_rate": broken_rate,
            "max_streak": max_streak,
            "premium": premium,
            "total_amount": total_amount,
            "amount_ratio": amount_ratio,
            "prev5_amount": prev5_amount,
            "breadth": breadth,
            "zt_median60": zt_median,
        },
        index=pd.DatetimeIndex(dates),
    )


def classify_ice(sent: pd.DataFrame,
                 index_close: np.ndarray | None = None) -> pd.DataFrame:
    """Ice levels, weakening-path flag, repair and rebound/reversal resonance."""
    adv = sent.advance_share.to_numpy()
    zt = sent.zt_count.to_numpy()
    dt = sent.dt_count.to_numpy()
    m = len(sent)
    prev_adv = np.r_[np.nan, adv[:-1]]
    prev_zt = np.r_[np.nan, zt[:-1]]
    prev_dt = np.r_[np.nan, dt[:-1]]

    ice_market = adv <= ICE_ADVANCE_STD
    ice_extreme = adv <= ICE_ADVANCE_EXTREME
    ice_sentiment = (sent.max_streak.to_numpy() <= ICE_STREAK_MAX) & (
        zt <= sent.zt_median60.to_numpy() / 2
    )
    ice_money = sent.amount_ratio.to_numpy() <= MONEY_SHRINK
    weakening = prev_adv <= WEAKENING_PREV

    level = np.zeros(m, dtype=np.int64)
    level[ice_market] = 1
    level[ice_market & weakening & (ice_sentiment | ice_money)] = 2
    level[ice_market & weakening & ice_sentiment & ice_money] = 3

    prev_ice = np.r_[False, level[:-1] >= 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        ad_ratio = adv / np.maximum(sent.decline_share.to_numpy(), 1e-9)
    cond = np.zeros((m, 4), dtype=bool)
    cond[:, 0] = adv >= REPAIR_ADVANCE
    cond[:, 1] = (dt <= REPAIR_DT_ABS) | (dt <= prev_dt / 2)
    cond[:, 2] = ad_ratio > 1
    cond[:, 3] = zt >= prev_zt * 2
    repair = prev_ice & (cond.sum(axis=1) >= 2)

    idx_ma20 = np.full(m, np.nan)
    idx_c = np.full(m, np.nan)
    if index_close is not None:
        idx_c = index_close.astype(np.float64)
        idx_ma20 = pd.Series(idx_c).rolling(20, min_periods=20).mean().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        amount_surge = sent.total_amount.to_numpy() / sent.prev5_amount.to_numpy()
    reversal = (
        repair
        & (amount_surge >= REVERSAL_AMOUNT)
        & ((sent.breadth.to_numpy() >= REVERSAL_BREADTH) | (idx_c > idx_ma20))
        & (zt >= REVERSAL_ZT)
        & (sent.max_streak.to_numpy() > REVERSAL_STREAK)
    )

    return pd.DataFrame(
        {
            "ice_level": level,
            "ice_extreme": ice_extreme,
            "ice_market": ice_market,
            "ice_sentiment": ice_sentiment,
            "ice_money": ice_money,
            "weakening_path": weakening,
            "repair": repair,
            "reversal": reversal,
            "rebound_only": repair & ~reversal,
            "amount_surge": amount_surge,
        },
        index=sent.index,
    )


def from_panel(panel: object, cfg: IceConfig = DEFAULT_ICE) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(sentiment, ice classification) from a trend_start.panel.Panel."""
    sent = daily_sentiment(
        panel.codes, panel.dates,  # type: ignore[attr-defined]
        panel.field("open"), panel.field("high"), panel.field("close"),  # type: ignore[attr-defined]
        panel.field("amount"), cfg,  # type: ignore[attr-defined]
    )
    ice = classify_ice(sent, panel.index_close)  # type: ignore[attr-defined]
    return sent, ice
