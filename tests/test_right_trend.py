"""right_trend 向量化实现与逐只股票的 pandas 参考实现（通达信公式字面翻译）逐点比对。"""
import unittest

import numpy as np
import pandas as pd

from trend_start.panel import FIELDS, Panel
from trend_start.right_trend import bars_since_extreme, compute


def make_panel(n=6, m=420, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, .025, (n, m))
    r[:, 150:190] -= .012     # 下跌段
    r[:, 190:260] += .004     # 修复段，制造 DD/LOWNEAR 成立的样本
    c = 20 * np.cumprod(1 + r, axis=1)
    h = c * (1 + rng.uniform(0, .02, (n, m)))
    lo = c * (1 - rng.uniform(0, .02, (n, m)))
    o = (h + lo) / 2
    v = rng.uniform(1e6, 3e6, (n, m)) * (1 + (rng.uniform(size=(n, m)) < .08) * 2)
    data = {"open": o, "high": h, "low": lo, "close": c, "vol": v, "amount": v * c}
    dates = pd.bdate_range("2024-01-01", periods=m).to_numpy().astype("datetime64[D]")
    return Panel(dates=dates, codes=np.array([f"{600000 + i}.SH" for i in range(n)]),
                 data={f: data[f].astype(np.float32) for f in FIELDS}, index_close=np.ones(m))


def tdx_reference(g: pd.DataFrame) -> pd.DataFrame:
    """通达信公式字面翻译（EMA 用 adjust=False；LLVBARS/HHVBARS 并列取最近一根）。"""
    c, h, lo, v = g.close.astype(np.float32), g.high.astype(np.float32), g.low.astype(np.float32), g.vol.astype(np.float32)
    out = pd.DataFrame(index=g.index)
    M = {k: c.rolling(k).mean() for k in (5, 10, 20, 60, 120)}
    vm20 = v.rolling(20).mean()
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    mh = 2 * (dif - dif.ewm(span=9, adjust=False).mean())
    dd = (h.rolling(60).max() - lo.rolling(30).min()) / h.rolling(60).max() >= .28

    lowage = lo.rolling(30).apply(lambda w: np.argmin(w[::-1]), raw=True)
    peakage = h.rolling(60).apply(lambda w: np.argmax(w[::-1]), raw=True)
    lownear = lowage <= 15
    hh = lambda k: h.rolling(k).max().shift(1)  # noqa: E731
    yj = (dd & lownear & (c > M[20]) & (M[5] > M[10]) & (M[5] > M[5].shift(2)) & (c > hh(5)) & (mh > 0) & (mh > mh.shift(1)) & (c / M[20] <= 1.12))
    qd = (dd & lownear & (c > M[20]) & (M[5] > M[10]) & (c > M[120]) & (c > hh(10)) & (v >= vm20 * 1.1) & (mh > 0))
    qr = dd & (c > M[60]) & (M[10] > M[20]) & (c > hh(20)) & (mh > 0)
    qqr = qr & (v >= vm20 * 1.8) & (dif > 0)
    out["yj1"], out["qd1"], out["qr1"], out["qqr1"] = yj, qd, qr, qqr
    out["lowage"], out["peakage"] = lowage, peakage
    return out


class RightTrendTests(unittest.TestCase):
    def test_bars_since_extreme_ties_pick_most_recent(self):
        a = np.array([[5, 3, 4, 3, 6, 7]], dtype=np.float32)
        self.assertEqual(bars_since_extreme(a, 5, "min")[0, 4], 1)    # 窗口[5,3,4,3,6]，最低 3 出现在 idx1 与 idx3，取最近 → 距今 1
        self.assertEqual(bars_since_extreme(a, 5, "max")[0, 5], 0)

    def test_matches_per_stock_reference(self):
        p = make_panel()
        rt = compute(p)
        hits = 0
        for i, code in enumerate(p.codes):
            g = pd.DataFrame({f: p.field(f)[i] for f in ("open", "high", "low", "close", "vol")})
            ref = tdx_reference(g)
            valid = np.arange(len(g)) >= 251  # BARSCOUNT(C)>250
            for name in ("yj1", "qd1", "qr1", "qqr1"):
                mine = getattr(rt, name)[i]
                np.testing.assert_array_equal(mine[valid], ref[name].fillna(False).to_numpy(bool)[valid], err_msg=f"{code} {name}")
                hits += int(mine[valid].sum())
            ok = ~np.isnan(ref.lowage.to_numpy()) & (np.arange(len(g)) >= 59)
            np.testing.assert_allclose(rt.aux["lowage"][i][ok], ref.lowage.to_numpy()[ok])
            np.testing.assert_allclose(rt.aux["peakage"][i][ok], ref.peakage.to_numpy()[ok])
        self.assertGreater(hits, 0, "合成数据没有触发任何信号，比对没有意义")

    def test_signals_are_causal(self):
        p = make_panel(n=3)
        full = compute(p)
        cut = Panel(dates=p.dates[:330], codes=p.codes, data={f: v[:, :330] for f, v in p.data.items()}, index_close=p.index_close[:330])
        part = compute(cut)
        for name in ("yj1", "qd1", "yj2", "qqr2"):
            np.testing.assert_array_equal(getattr(full, name)[:, :330], getattr(part, name))
        for k in full.vf:
            np.testing.assert_array_equal(full.vf[k][:, :330], part.vf[k])

    def test_st_mask_and_suspension_gate(self):
        p = make_panel(n=2)
        p.data["close"][1, 300] = np.nan
        rt = compute(p, st=np.array([True, False]))
        self.assertFalse(rt.yj1[0].any() or rt.qd2[0].any())
        self.assertFalse(rt.yj1[1, 300] or rt.qr1[1, 300])


if __name__ == "__main__":
    unittest.main()
