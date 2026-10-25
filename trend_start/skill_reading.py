"""把 Mistery 技能（stock-type-classifier / trend-judgment / warrior-520 / smart-money-detector）的执行步骤
写成可复现的计算：输入一只股票截至 t 日的日线，输出结构化“读数”。

只是把技能里定性的步骤落成明确规则，阈值取自技能原文（如波动>5%、量比、20日线有效跌破=连续2日）；技能没给数字的地方
在这里写明所用的数字。技能自己声明没有严格回测，本模块的读数是分析框架，不是买卖信号，也没有被证明有超额收益。
不可用的数据（换手率、机构持仓、流通市值）不会被猜测，只在结果里标明“不可用”。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_BARS = 250
UNAVAILABLE = ["换手率", "机构持仓比例", "流通市值"]


def sma(a: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a.astype(float), 0, 0.0))
        out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def ema(a: np.ndarray, span: int) -> np.ndarray:
    out = np.empty(len(a))
    k = 2 / (span + 1)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


@dataclass
class Bars:
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    limit: float          # 该板块涨停幅度，如 0.10 / 0.20

    def __post_init__(self):
        c = self.c.astype(float)
        self.m = {k: sma(c, k) for k in (5, 10, 20, 60, 250)}
        self.vm20 = sma(self.v.astype(float), 20)
        self.dif = ema(c, 12) - ema(c, 26)
        self.dea = ema(self.dif, 9)
        self.mh = 2 * (self.dif - self.dea)
        self.n = len(c)

    def ret1(self) -> float:
        return float(self.c[-1] / self.c[-2] - 1)


def classify_type(b: Bars, theme_hot: bool) -> dict:
    """个股分类：情绪票 vs 趋势票（技能判据：波动率、题材、均线斜率；涨停活跃度作为情绪佐证）。"""
    c, h, l = b.c.astype(float), b.h.astype(float), b.l.astype(float)
    prev = c[-6:-1]
    amp5 = float(np.mean((h[-5:] - l[-5:]) / prev) * 100)
    lim = b.limit - 0.003
    pct = c[1:] / c[:-1] - 1
    zt20 = int((pct[-20:] >= lim).sum())
    slope5 = float((b.m[20][-1] / b.m[20][-6] - 1) * 100) if np.isfinite(b.m[20][-6]) else float("nan")
    votes = {"近5日平均振幅>5%": amp5 > 5, "近20日涨停≥2次": zt20 >= 2, "属于当前热点题材": bool(theme_hot), "MA20 5日斜率>3%": slope5 > 3}
    n = sum(votes.values())
    label = "偏情绪" if n >= 3 else "偏趋势" if n <= 1 else "混合"
    return {"type": label, "emotion_votes": n, "amp5_pct": amp5, "zt20": zt20, "ma20_slope5_pct": slope5,
            "hit": [k for k, v in votes.items() if v], "unavailable": UNAVAILABLE}


def judge_trend(b: Bars) -> dict:
    """趋势判断：均线排列、量价、20日线、MACD 四个信号，加综合置信度。上市不足 250 根降级。"""
    if b.n < MIN_BARS:
        return {"direction": "数据不足", "confidence": "降级", "note": f"仅 {b.n} 根日线，不足 {MIN_BARS}"}
    c = b.c.astype(float)
    m5, m10, m20, m60, m250 = (b.m[k][-1] for k in (5, 10, 20, 60, 250))
    if m5 > m10 > m20 > m60 and c[-1] > m250:
        ma_state = "多头排列"
    elif m5 < m10 < m20 < m60 and c[-1] < m250:
        ma_state = "空头排列"
    else:
        ma_state = "均线混乱/转换中"
    pct = np.diff(c) / c[:-1]
    v = b.v.astype(float)
    up10, dn10 = v[-10:][pct[-10:] > 0].sum(), v[-10:][pct[-10:] < 0].sum()
    ratio = float(up10 / dn10) if dn10 > 0 else float("inf")
    stall = bool(v[-1] > 1.5 * b.vm20[-1] and abs(pct[-1]) < 0.01)
    vp = "危险（放量滞涨）" if stall else "健康（上涨量占优）" if ratio >= 1.2 else "危险（下跌量占优）" if ratio < 0.8 else "中性"
    below2 = bool((c[-2:] < b.m[20][-2:]).all())
    ma20_state = "有效跌破20日线" if below2 else "站上20日线" if c[-1] > m20 else "20日线下方"
    cross = [i for i in range(1, 6) if b.dif[-i] > b.dea[-i] and b.dif[-i - 1] <= b.dea[-i - 1]]
    macd_bull = bool(b.dif[-1] > b.dea[-1] and b.mh[-1] > b.mh[-2])
    macd = ("零轴上" if b.dif[-1] > 0 else "零轴下") + ("，金叉" + f"（{cross[0]}日前）" if cross else "") + ("，红柱放大" if macd_bull else "，动能不足")
    signals = {"均线多头": ma_state == "多头排列", "量价健康": vp.startswith("健康"), "站上20日线": ma20_state == "站上20日线", "MACD偏多": macd_bull}
    n = sum(signals.values())
    direction = ("上升" if ma_state == "多头排列" and ma20_state == "站上20日线"
                 else "下降" if ma_state == "空头排列" or (ma20_state != "站上20日线" and m60 < b.m[60][-6])
                 else "震荡")
    conf = "高" if n == 4 else "中" if n == 3 else "低"
    prior_high = float(np.max(b.h[-61:-1]))
    return {"direction": direction, "confidence": conf, "agree": n, "ma_state": ma_state, "volume_price": vp, "up_down_vol": ratio,
            "ma20_state": ma20_state, "dev_ma20_pct": float((c[-1] / m20 - 1) * 100), "macd": macd,
            "support_ma20": float(m20), "support_ma60": float(m60), "resistance_prior60_high": prior_high,
            "note": "启动初期均线常未排齐，‘混乱/转换中’不等于走坏" if ma_state.startswith("均线混乱") else ""}


def read_520(b: Bars, trend_direction: str) -> dict:
    """520 战法四象限（MA5×MA20）。技能前提：趋势不是下降。"""
    if trend_direction == "下降":
        return {"state": "不适用", "note": "趋势向下，技能规则不适用"}
    m5, m20 = b.m[5], b.m[20]
    c, lo = b.c.astype(float), b.l.astype(float)
    gold = [k for k in range(0, 4) if m5[-1 - k] > m20[-1 - k] and m5[-2 - k] <= m20[-2 - k]]
    dead = [k for k in range(0, 4) if m5[-1 - k] < m20[-1 - k] and m5[-2 - k] >= m20[-2 - k]]
    rising5 = bool(m5[-1] > m5[-2])
    if dead:
        state = f"死叉（{dead[0]}日前）"
    elif gold and c[-1] > m20[-1]:
        state = f"金叉（{gold[0]}日前）"
    elif c[-1] > m20[-1] and lo[-1] <= m20[-1] * 1.02 and rising5:
        state = "回踩20日线、MA5拐头向上"
    elif m5[-1] < m20[-1] and c[-1] < m20[-1]:
        state = "趋势走坏（MA5<MA20且价在20日线下）"
    elif m5[-1] > m20[-1]:
        state = "MA5在MA20上方运行"
    else:
        state = "MA5在MA20下方，价格在20日线上方"
    vol_ok = bool(b.v[-1] >= 1.2 * b.vm20[-1])
    macd_gold = any(b.dif[-i] > b.dea[-i] and b.dif[-i - 1] <= b.dea[-i - 1] for i in range(1, 4))
    strength = "强" if vol_ok and macd_gold else "中" if vol_ok or macd_gold else "弱"
    return {"state": state, "volume_confirm": vol_ok, "macd_cross_3d": macd_gold, "strength": strength,
            "stop_ref_ma20": float(m20[-1]), "stop_ref_low10": float(np.min(lo[-10:])), "note": ""}


def read_smart_money(b: Bars, regime_name: str) -> dict:
    """主力行为（牛熊不对称模型）。必须先判断牛熊；这里以宏观模块的九宫格状态为准。"""
    c = b.c.astype(float)
    bear = "熊" in regime_name
    r1 = b.ret1()
    vr = float(b.v[-1] / b.vm20[-1])
    llv30 = float(np.min(b.l[-30:]))
    off_low = float(c[-1] / llv30 - 1)
    hhv60 = float(np.max(b.h[-60:]))
    near_high = c[-1] >= 0.95 * hhv60
    dry = float(np.mean(b.v[-5:]) / b.vm20[-1])
    if near_high and vr >= 1.5 and abs(r1) < 0.01:
        label = "高位放量滞涨：出货风险"
    elif bear and r1 >= 0.05:
        label = "熊市急涨：诱多风险（技能模型：熊市反弹多为出货陷阱）"
    elif bear and r1 > 0 and vr >= 1.5:
        label = "熊市放量上涨：需防诱多，等回踩确认"
    elif off_low <= 0.20 and dry < 0.8:
        label = "低位缩量：吸筹倾向（需价格止跌确认）"
    elif not bear and r1 <= -0.05 and vr < 1.0:
        label = "牛市急跌缩量：洗盘倾向"
    else:
        label = "无明显主力行为信号"
    return {"label": label, "regime": regime_name, "bear": bear, "ret1_pct": r1 * 100, "vol_ratio20": vr,
            "off_low30_pct": off_low * 100, "dry5_ratio": dry, "note": "技能自述：把所有放量/急跌都当信号是失败模式，仅作旁证"}
