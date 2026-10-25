"""
macro 模块自测 — 用合成数据验证整条流水线可跑通且方向正确。

合成数据刻意植入三段行情 (牛 -> 熊 -> 震荡 -> 牛),
断言状态机能把牛段判成趋势强、熊段判成趋势弱。

运行:
    pytest tests/test_macro.py -v
    python tests/test_macro.py          # 直接跑, 会打印诊断信息
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macro.factors import compute_all_factors, build_matrices, equal_weight_index
from macro.regime import (
    RegimeConfig, build_regime_series, compute_scores, classify_regime,
    _apply_confirmation,
)
from macro.sector import (
    SectorConfig, compute_sector_weekly, build_sector_whitelist,
    filter_stocks_by_whitelist, resolve_industry_name,
)
from macro.dashboard import build_dashboard


# =============================================================================
# 合成数据
# =============================================================================

N_STOCKS = 400
N_INDUSTRIES = 20


def _regime_drift(n_days: int) -> np.ndarray:
    """构造 牛(500) -> 熊(350) -> 震荡(300) -> 牛(剩余) 的日漂移序列"""
    segs = []
    segs.append(np.full(500, 0.0016))     # 牛: 年化 ~48%
    segs.append(np.full(350, -0.0020))    # 熊: 年化 ~-40%
    segs.append(np.full(300, 0.0001))     # 震荡
    drift = np.concatenate(segs)
    if len(drift) < n_days:
        drift = np.concatenate([drift, np.full(n_days - len(drift), 0.0018)])
    return drift[:n_days]


def make_synthetic(n_days: int = 1500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-02", periods=n_days)
    drift = _regime_drift(n_days)

    # 行业层因子, 让板块之间产生真实的强弱分化
    ind_of = np.array([f"T{i%N_INDUSTRIES:02d}" for i in range(N_STOCKS)])
    ind_beta = rng.uniform(0.6, 1.5, N_INDUSTRIES)
    ind_shock = rng.normal(0, 0.006, (n_days, N_INDUSTRIES)).cumsum(axis=0) * 0.02

    market = rng.normal(0, 0.011, n_days) + drift
    rows = []
    for j in range(N_STOCKS):
        k = j % N_INDUSTRIES
        beta = ind_beta[k] * rng.uniform(0.7, 1.3)
        idio = rng.normal(0, 0.017, n_days)
        r = beta * market + idio + np.diff(ind_shock[:, k], prepend=0)
        r = np.clip(r, -0.10, 0.10)
        close = 10.0 * np.exp(np.cumsum(r))
        noise = rng.uniform(0.002, 0.02, n_days)
        rows.append(pd.DataFrame({
            "trade_date": dates,
            "ts_code": f"{600000+j:06d}.SH",
            "close": close,
            "high": close * (1 + noise),
            "low": close * (1 - noise),
            "open": close * (1 + rng.normal(0, 0.004, n_days)),
            "vol": rng.lognormal(14, 0.45, n_days),
            "amount": rng.lognormal(18.4, 0.55, n_days),
        }))
    df = pd.concat(rows, ignore_index=True)

    # RPS_120
    df = df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    df["_roc"] = df.groupby("ts_code")["close"].pct_change(120)
    df["RPS_120"] = df.groupby("trade_date")["_roc"].rank(pct=True) * 100
    df = df.drop(columns="_roc")

    df.attrs["industry_map"] = pd.DataFrame({
        "ts_code": [f"{600000+j:06d}.SH" for j in range(N_STOCKS)],
        "industry": ind_of,
    })
    df.attrs["seg_bounds"] = (500, 850, 1150)
    return df


# =============================================================================
# 测试
# =============================================================================

def test_factors_shape_and_range():
    df = make_synthetic()
    f = compute_all_factors(df, verbose=False)

    for col in ["T1_breadth_ma200", "T2_breadth_ma50", "T5_ma_stack",
                "H3_high_zone", "B4_leader_health"]:
        v = f[col].dropna()
        assert len(v) > 0, f"{col} 全为空"
        assert v.between(0, 1).all(), f"{col} 越界: {v.min()}~{v.max()}"

    assert f["B2_adv_ratio"].dropna().between(0, 1).all()
    assert f["H1_turnover_pulse"].dropna().gt(0).all()
    assert f["H5_vol_annual"].dropna().gt(0).all()
    assert f["B1_nhnl"].dropna().between(-1, 1).all()


def test_regime_direction():
    """核心断言: 牛段趋势分应显著高于熊段"""
    df = make_synthetic()
    f = compute_all_factors(df, verbose=False)
    r = build_regime_series(f, verbose=False)

    dates = f.index
    b1, b2, b3 = df.attrs["seg_bounds"]
    bull = r.loc[dates[300:b1], "trend_score"].dropna()
    bear = r.loc[dates[b1 + 60:b2], "trend_score"].dropna()

    assert len(bull) > 50 and len(bear) > 50
    assert bull.mean() > bear.mean() + 20, (
        f"牛段趋势分 {bull.mean():.1f} 未显著高于熊段 {bear.mean():.1f}")
    assert bull.mean() > 55, f"牛段趋势分偏低: {bull.mean():.1f}"
    assert bear.mean() < 45, f"熊段趋势分偏高: {bear.mean():.1f}"


def test_position_lower_in_bear():
    df = make_synthetic()
    f = compute_all_factors(df, verbose=False)
    r = build_regime_series(f, verbose=False)
    dates = f.index
    b1, b2, _ = df.attrs["seg_bounds"]
    bull_pos = r.loc[dates[300:b1], "target_position"].mean()
    bear_pos = r.loc[dates[b1 + 60:b2], "target_position"].mean()
    assert bull_pos > bear_pos, f"牛段仓位 {bull_pos:.2f} 未高于熊段 {bear_pos:.2f}"


def test_confirmation_reduces_switches():
    """防抖必须显著减少状态切换次数"""
    df = make_synthetic()
    f = compute_all_factors(df, verbose=False)
    raw = build_regime_series(f, RegimeConfig(confirm_days=1), verbose=False)
    smooth = build_regime_series(f, RegimeConfig(confirm_days=5), verbose=False)

    n_raw = (raw["regime"] != raw["regime"].shift()).sum()
    n_smooth = (smooth["regime"] != smooth["regime"].shift()).sum()
    assert n_smooth <= n_raw, f"防抖后切换反而变多: {n_raw} -> {n_smooth}"


def test_confirmation_logic():
    s = pd.Series(["A", "A", "B", "A", "B", "B", "B", "B"])
    out = _apply_confirmation(s, 3)
    # 单点 B 不该切换, 连续 3 个 B 之后才切
    assert out.tolist() == ["A", "A", "A", "A", "A", "A", "B", "B"], out.tolist()


def test_classify_grid_complete():
    for t in (10, 50, 90):
        for h in (10, 50, 90):
            cell = classify_regime(t, h)
            assert {"name", "code", "position", "action"} <= set(cell)
            assert 0.0 <= cell["position"] <= 1.0


def test_missing_factor_degrades():
    """缺因子时应自动重新归一化权重, 而不是报错或产生 NaN"""
    df = make_synthetic()
    f = compute_all_factors(df, verbose=False)
    f2 = f.drop(columns=["B4_leader_health", "H6_blowoff", "T5_ma_stack"])
    s = compute_scores(f2, verbose=False)
    assert s["trend_score"].dropna().between(0, 100).all()
    assert s["heat_score"].dropna().between(0, 100).all()
    assert s["trend_score"].notna().sum() > 100


def test_sector_pipeline():
    df = make_synthetic()
    im = df.attrs["industry_map"]
    res = compute_sector_weekly(df, im, weeks=12, verbose=False)
    latest, history = res["latest"], res["history"]

    assert len(latest) == N_INDUSTRIES
    assert latest["score"].notna().all()
    assert latest["score"].between(0, 100).all()
    assert set(latest["quadrant"]) <= {"LEADING", "EMERGING", "FADING", "LAGGING"}
    assert latest["score"].is_monotonic_decreasing, "结果未按分数降序"
    assert history.shape[1] == N_INDUSTRIES
    assert len(history) <= 12

    wl = build_sector_whitelist(latest, SectorConfig(min_score=50, min_stocks=5))
    assert (wl["quadrant"] != "FADING").all(), "白名单混入了退潮板块"


def test_whitelist_filter():
    df = make_synthetic()
    im = df.attrs["industry_map"]
    res = compute_sector_weekly(df, im, weeks=8, verbose=False)
    wl = build_sector_whitelist(res["latest"], SectorConfig(min_score=40, min_stocks=5))

    picks = pd.DataFrame({"ts_code": [f"{600000+j:06d}.SH" for j in range(60)],
                          "close": np.arange(60) + 5.0})
    kept = filter_stocks_by_whitelist(picks, im, wl)
    assert len(kept) <= len(picks)
    assert kept["in_whitelist"].all()
    assert "industry_name" in kept.columns
    if not wl.empty:
        assert kept["industry"].isin(set(wl.index)).all()


def test_dashboard_renders():
    df = make_synthetic()
    im = df.attrs["industry_map"]
    f = compute_all_factors(df, verbose=False)
    r = build_regime_series(f, verbose=False)
    sec = compute_sector_weekly(df, im, weeks=12, verbose=False)
    wl = build_sector_whitelist(sec["latest"], SectorConfig(min_score=50, min_stocks=5))

    html = build_dashboard(r, f, sec, wl)
    assert html.startswith("<!DOCTYPE html>")
    assert html.count("<svg") >= 5, f"图表数量不足: {html.count('<svg')}"
    assert "NaN" not in html.replace("NaN%", ""), "HTML 中出现 NaN"
    assert len(html) > 20000


# =============================================================================
# 中美对抗因子
# =============================================================================

def _geo_ready_df():
    """把合成数据的日期平移到 2026-08-07 收盘, 让事件日志落在数据范围内"""
    df = make_synthetic()
    df["trade_date"] = df["trade_date"] + (
        pd.Timestamp("2026-08-07") - df["trade_date"].max())
    return df


def test_geo_score_range_and_level():
    from macro.geopolitics import compute_geo_score, GEO_LEVELS
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=df.attrs["industry_map"], verbose=False)

    assert geo["geo_score"].dropna().between(0, 100).all()
    assert geo["g1_score"].between(0, 100).all()
    assert set(geo["geo_level"]) <= set(GEO_LEVELS)
    # 事件日志覆盖 2026-06 之后, 那段应该明显高于之前
    pre = geo.loc[:"2026-05-01", "geo_score"].mean()
    post = geo.loc["2026-07-20":, "geo_score"].mean()
    assert post > pre + 25, f"事件期对抗分 {post:.1f} 未显著高于平静期 {pre:.1f}"


def test_g1_not_saturated():
    """G1 用饱和函数, 密集事件期不应恰好顶到 100 而失去分辨率"""
    from macro.geopolitics import compute_geo_score
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=df.attrs["industry_map"], verbose=False)
    peak = geo["g1_score"].max()
    assert 80 < peak < 99.5, f"G1 峰值 {peak:.1f} 异常（饱和或过低）"


def test_geo_window_not_masked_by_macro_event():
    """临近的货币政策事件不应遮住更远的地缘事件"""
    from macro.geopolitics import compute_geo_score
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=df.attrs["industry_map"], verbose=False)
    last = geo.dropna(subset=["geo_score"]).iloc[-1]
    # 事件日志里下一个 macro 事件(杰克逊霍尔)比 geo 事件(元首会晤)更近
    assert last["event_kind"] == "macro"
    assert pd.notna(last["days_to_geo"]), "地缘事件被货币事件遮住了"
    assert last["g3_score"] > 0, "地缘窗口因子被错误置零"


def test_deescalation_reduces_intensity():
    """缓和事件应把强度往下压"""
    from macro.geopolitics import event_intensity, GeoConfig
    dates = pd.bdate_range("2026-01-01", "2026-04-01")
    esc = [{"date": "2026-02-01", "severity": 4, "direction": "escalate"}]
    both = esc + [{"date": "2026-02-10", "severity": 4, "direction": "deescalate"}]
    a = event_intensity(esc, dates, GeoConfig())
    b = event_intensity(both, dates, GeoConfig())
    assert b.iloc[-1] < a.iloc[-1]


def test_sector_tilt_direction():
    from macro.geopolitics import sector_tilt, GeoConfig
    latest = pd.DataFrame({
        "name": ["国防军工", "纺织服装", "建筑"],
        "score": [60.0, 60.0, 60.0],
    }, index=["T10", "T14", "T07"])

    hot = sector_tilt(latest, geo_score=90.0)
    assert hot.loc["T10", "geo_tilt"] > 0, "对抗升级时军工应加分"
    assert hot.loc["T14", "geo_tilt"] < 0, "对抗升级时纺织服装应减分"
    assert abs(hot.loc["T07", "geo_tilt"]) < 1e-9, "无关行业不应被调整"
    assert hot["score_geo"].between(0, 100).all()

    calm = sector_tilt(latest, geo_score=10.0)
    assert calm.loc["T10", "geo_tilt"] < 0, "缓和时应反向"
    assert calm.loc["T14", "geo_tilt"] > 0

    neutral = sector_tilt(latest, geo_score=50.0)
    assert neutral["geo_tilt"].abs().max() < 1e-9, "geo_score=50 时不应有任何倾斜"


def test_tilt_magnitude_bounded():
    """倾斜幅度不该盖过基本面分数本身"""
    from macro.geopolitics import sector_tilt, GeoConfig
    latest = pd.DataFrame({"name": ["信创"], "score": [50.0]}, index=["T26"])
    t = sector_tilt(latest, geo_score=100.0, config=GeoConfig())
    assert t["geo_tilt"].abs().max() <= GeoConfig().tilt_max + 1e-6


def test_event_cap_applies():
    from macro.geopolitics import compute_geo_score, apply_event_cap, GeoConfig
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=df.attrs["industry_map"], verbose=False)
    f = compute_all_factors(df, verbose=False)
    r = build_regime_series(f, verbose=False)

    # 人为把窗口打开, 验证封顶确实生效
    geo2 = geo.copy()
    geo2["G3_in_window"] = True
    cfg = GeoConfig(event_cap_position=0.5)
    out = apply_event_cap(r, geo2, cfg)
    assert (out["target_position_geo"] <= 0.5 + 1e-9).all()
    assert (out["target_position_geo"] <= out["target_position"] + 1e-9).all()

    # 窗口关闭时不应改变仓位
    geo3 = geo.copy()
    geo3["G3_in_window"] = False
    out2 = apply_event_cap(r, geo3, cfg)
    assert np.allclose(out2["target_position_geo"], out2["target_position"])


def test_geo_degrades_without_industry_map():
    """没有行业映射时 G2 缺失, 体系应仍能算出 geo_score"""
    from macro.geopolitics import compute_geo_score
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=None, verbose=False)
    assert geo["G2_market_premium"].isna().all()
    assert geo["geo_score"].dropna().between(0, 100).all()
    assert geo["geo_score"].notna().sum() > 100


def test_geo_missing_event_file():
    from macro.geopolitics import compute_geo_score
    df = _geo_ready_df()
    geo = compute_geo_score(df, industry_map=df.attrs["industry_map"],
                            events_path=Path("__no_such_file__.json"), verbose=False)
    assert (geo["G1_event_intensity"] == 0).all()


def test_industry_name_fallback():
    assert resolve_industry_name("T22") == "银行"
    assert resolve_industry_name("T2201") == "银行"
    assert resolve_industry_name("ZZZ") == "ZZZ"
    assert resolve_industry_name("T99", {"T99": "自定义"}) == "自定义"


# =============================================================================
# 直接运行时的诊断输出
# =============================================================================

if __name__ == "__main__":
    print("生成合成数据 ...")
    df = make_synthetic()
    im = df.attrs["industry_map"]
    print(f"  {len(df):,} 行, {df.ts_code.nunique()} 只股票, "
          f"{df.trade_date.nunique()} 个交易日")

    print("\n计算因子 ...")
    f = compute_all_factors(df, verbose=True)

    print("\n状态机 ...")
    r = build_regime_series(f, verbose=True)

    b1, b2, b3 = df.attrs["seg_bounds"]
    dates = f.index
    print("\n分段体检 (合成数据植入的真实行情段):")
    for label, lo, hi in [("牛市段", 300, b1), ("熊市段", b1 + 60, b2),
                          ("震荡段", b2 + 60, b3), ("再牛段", b3 + 60, len(dates) - 1)]:
        seg = r.loc[dates[lo]:dates[hi]]
        if seg["trend_score"].notna().sum() == 0:
            continue
        top = seg["regime_name"].value_counts()
        print(f"  {label}: 趋势 {seg['trend_score'].mean():5.1f}  "
              f"温度 {seg['heat_score'].mean():5.1f}  "
              f"仓位 {seg['target_position'].mean():5.1%}  "
              f"主导状态 {top.index[0]} ({top.iloc[0]/len(seg):.0%})")

    n_sw = (r["regime"] != r["regime"].shift()).sum()
    print(f"\n状态切换 {n_sw} 次 / {len(r.dropna(subset=['trend_score']))/244:.1f} 年")

    print("\n板块强度 ...")
    sec = compute_sector_weekly(df, im, weeks=12, verbose=True)
    print(sec["latest"][["name", "score", "S1_rps_mid", "S3_accel",
                         "quadrant_cn"]].head(8).to_string())

    wl = build_sector_whitelist(sec["latest"], SectorConfig(min_score=55, min_stocks=5))
    print(f"\n白名单 {len(wl)} 个: {', '.join(wl['name'].tolist())}")

    print("\n仪表盘 ...")
    html = build_dashboard(r, f, sec, wl)
    out = Path(__file__).resolve().parents[1] / "output" / "macro_dashboard_selftest.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"  已写出 {out}  ({len(html)/1024:.0f} KB, {html.count('<svg')} 张图)")
