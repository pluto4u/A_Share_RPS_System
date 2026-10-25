"""
================================================================================
宏观仪表盘 — 纯 SVG 离线 HTML 报告
================================================================================
刻意不使用任何 JS 图表库 (Chart.js / ECharts 等), 全部用 Python 直接生成
内联 SVG。好处:
    - 生成的 HTML 单文件、零依赖, 断网也能打开, 可以直接发微信/邮件
    - 不受 CDN 可用性影响
    - 可以精确控制状态色带这类非标准图元

包含 6 张图:
    1. 等权指数 + 状态色带    <- 最重要, 一眼看出状态机在历史上对不对
    2. 趋势轴 / 温度轴 双线
    3. 目标仓位阶梯
    4. 九宫格当前位置
    5. 板块强度热力图 (周 x 行业)
    6. 板块四象限散点
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .regime import REGIME_GRID, RegimeConfig
from .sector import QUADRANT_LABEL


# =============================================================================
# SVG 基础件
# =============================================================================

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',"
        "'PingFang SC',sans-serif")

REGIME_COLORS = {c["code"]: c["color"] for c in REGIME_GRID.values()}
REGIME_NAMES = {c["code"]: c["name"] for c in REGIME_GRID.values()}


def _esc(s) -> str:
    return html.escape(str(s))


def _scale(vals, lo, hi, out_lo, out_hi):
    """线性映射, hi==lo 时返回中点"""
    if hi == lo:
        return np.full(len(vals), (out_lo + out_hi) / 2)
    return out_lo + (np.asarray(vals, dtype=float) - lo) / (hi - lo) * (out_hi - out_lo)


def _path(xs, ys) -> str:
    pts = [f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys) if np.isfinite(y)]
    return "M" + "L".join(pts) if pts else ""


def _date_ticks(idx: pd.DatetimeIndex, n: int = 6) -> List[Tuple[int, str]]:
    if len(idx) == 0:
        return []
    step = max(1, len(idx) // n)
    return [(i, pd.Timestamp(idx[i]).strftime("%y/%m"))
            for i in range(0, len(idx), step)]


# =============================================================================
# 图 1: 等权指数 + 状态色带
# =============================================================================

def chart_index_with_regime(regime_df: pd.DataFrame, factors: pd.DataFrame,
                            width: int = 1080, height: int = 340) -> str:
    df = regime_df.dropna(subset=["trend_score"]).copy()
    if df.empty or "_ewi" not in factors.columns:
        return "<p>数据不足</p>"
    ewi = factors["_ewi"].reindex(df.index)
    ma = factors["_ewi_ma200"].reindex(df.index) if "_ewi_ma200" in factors else None

    pad_l, pad_r, pad_t, pad_b = 62, 16, 16, 30
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    vals = pd.concat([ewi, ma]).dropna() if ma is not None else ewi.dropna()
    lo, hi = float(vals.min()), float(vals.max())
    pad_v = (hi - lo) * 0.06
    lo, hi = lo - pad_v, hi + pad_v

    n = len(df)
    xs = np.linspace(pad_l, pad_l + pw, n)
    ys = _scale(ewi.values, lo, hi, pad_t + ph, pad_t)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']

    # 状态色带 (底层)
    codes = df["regime"].values
    start = 0
    for i in range(1, n + 1):
        if i == n or codes[i] != codes[start]:
            c = REGIME_COLORS.get(codes[start], "#888")
            x0, x1 = xs[start], xs[min(i, n - 1)]
            parts.append(f'<rect x="{x0:.1f}" y="{pad_t}" '
                         f'width="{max(0.6, x1-x0):.1f}" height="{ph}" '
                         f'fill="{c}" opacity="0.20"/>')
            start = i

    # 网格 + y 轴
    for f in (0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + ph * f
        v = hi - (hi - lo) * f
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" '
                     f'y2="{y:.1f}" stroke="#d8dee9" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#64748b">{v:,.0f}</text>')

    if ma is not None:
        ys_ma = _scale(ma.values, lo, hi, pad_t + ph, pad_t)
        parts.append(f'<path d="{_path(xs, ys_ma)}" fill="none" '
                     f'stroke="#94a3b8" stroke-width="1.6" stroke-dasharray="5,4"/>')

    parts.append(f'<path d="{_path(xs, ys)}" fill="none" stroke="#0f172a" '
                 f'stroke-width="1.9"/>')

    for i, label in _date_ticks(df.index):
        parts.append(f'<text x="{xs[i]:.1f}" y="{height-10}" '
                     f'text-anchor="middle" font-size="11" fill="#64748b">{label}</text>')

    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# 图 2: 双轴分数
# =============================================================================

def chart_scores(regime_df: pd.DataFrame, config: Optional[RegimeConfig] = None,
                 width: int = 1080, height: int = 260) -> str:
    cfg = config or RegimeConfig()
    df = regime_df.dropna(subset=["trend_score"])
    if df.empty:
        return "<p>数据不足</p>"

    pad_l, pad_r, pad_t, pad_b = 62, 16, 14, 30
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(df)
    xs = np.linspace(pad_l, pad_l + pw, n)

    def yy(v):
        return _scale(v, 0, 100, pad_t + ph, pad_t)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']

    # 阈值带
    for y0, y1, c in [
        (cfg.trend_strong_above, 100, "#dcfce7"),
        (0, cfg.trend_weak_below, "#fee2e2"),
    ]:
        a, b = yy(y1), yy(y0)
        parts.append(f'<rect x="{pad_l}" y="{a:.1f}" width="{pw}" '
                     f'height="{b-a:.1f}" fill="{c}" opacity="0.55"/>')

    for v in (0, 20, 40, 60, 80, 100):
        y = yy(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" '
                     f'y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#64748b">{v}</text>')

    parts.append(f'<path d="{_path(xs, yy(df["trend_score"].values))}" '
                 f'fill="none" stroke="#1d4ed8" stroke-width="2"/>')
    parts.append(f'<path d="{_path(xs, yy(df["heat_score"].values))}" '
                 f'fill="none" stroke="#ea580c" stroke-width="2"/>')
    if "stress_score" in df.columns and df["stress_score"].notna().any():
        parts.append(f'<path d="{_path(xs, yy(df["stress_score"].values))}" '
                     f'fill="none" stroke="#7c3aed" stroke-width="1.3" '
                     f'stroke-dasharray="3,3" opacity="0.75"/>')

    for i, label in _date_ticks(df.index):
        parts.append(f'<text x="{xs[i]:.1f}" y="{height-10}" '
                     f'text-anchor="middle" font-size="11" fill="#64748b">{label}</text>')

    # 图例
    lx = pad_l + 8
    for name, col in [("趋势轴", "#1d4ed8"), ("温度轴", "#ea580c"), ("应激度", "#7c3aed")]:
        parts.append(f'<rect x="{lx}" y="{pad_t+4}" width="14" height="3" fill="{col}"/>')
        parts.append(f'<text x="{lx+19}" y="{pad_t+10}" font-size="11" '
                     f'fill="#334155">{name}</text>')
        lx += 74

    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# 图 3: 目标仓位阶梯
# =============================================================================

def chart_position(regime_df: pd.DataFrame, width: int = 1080,
                   height: int = 170) -> str:
    df = regime_df.dropna(subset=["target_position"])
    if df.empty:
        return "<p>数据不足</p>"
    pad_l, pad_r, pad_t, pad_b = 62, 16, 12, 30
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(df)
    xs = np.linspace(pad_l, pad_l + pw, n)
    ys = _scale(df["target_position"].values, 0, 1, pad_t + ph, pad_t)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        y = _scale([v], 0, 1, pad_t + ph, pad_t)[0]
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" '
                     f'y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#64748b">{v:.0%}</text>')

    # 阶梯填充
    pts = [f"{pad_l:.1f},{pad_t+ph:.1f}"]
    for i in range(n):
        if np.isfinite(ys[i]):
            pts.append(f"{xs[i]:.1f},{ys[i]:.1f}")
            if i + 1 < n:
                pts.append(f"{xs[i+1]:.1f},{ys[i]:.1f}")
    pts.append(f"{pad_l+pw:.1f},{pad_t+ph:.1f}")
    parts.append(f'<polygon points="{" ".join(pts)}" fill="#0ea5e9" opacity="0.28"/>')
    parts.append(f'<polyline points="{" ".join(pts[1:-1])}" fill="none" '
                 f'stroke="#0369a1" stroke-width="1.6"/>')

    for i, label in _date_ticks(df.index):
        parts.append(f'<text x="{xs[i]:.1f}" y="{height-10}" '
                     f'text-anchor="middle" font-size="11" fill="#64748b">{label}</text>')
    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# 图 4: 九宫格
# =============================================================================

def chart_grid(regime_df: pd.DataFrame, config: Optional[RegimeConfig] = None,
               width: int = 460, height: int = 400, trail: int = 40) -> str:
    cfg = config or RegimeConfig()
    df = regime_df.dropna(subset=["trend_score"])
    if df.empty:
        return "<p>数据不足</p>"

    pad = 52
    pw, ph = width - pad - 18, height - pad - 34
    x0, y0 = pad, 18

    def px(trend):
        return x0 + trend / 100.0 * pw

    def py(heat):
        return y0 + ph - heat / 100.0 * ph

    tb = [0, cfg.trend_weak_below, cfg.trend_strong_above, 100]
    hb = [0, cfg.heat_cold_below, cfg.heat_hot_above, 100]
    tkeys = ["weak", "neutral", "strong"]
    hkeys = ["cold", "warm", "hot"]

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']

    for ti in range(3):
        for hi_ in range(3):
            cell = REGIME_GRID[(tkeys[ti], hkeys[hi_])]
            xa, xb = px(tb[ti]), px(tb[ti + 1])
            ya, yb = py(hb[hi_ + 1]), py(hb[hi_])
            parts.append(f'<rect x="{xa:.1f}" y="{ya:.1f}" '
                         f'width="{xb-xa:.1f}" height="{yb-ya:.1f}" '
                         f'fill="{cell["color"]}" opacity="0.18" '
                         f'stroke="#cbd5e1"/>')
            parts.append(f'<text x="{(xa+xb)/2:.1f}" y="{(ya+yb)/2-4:.1f}" '
                         f'text-anchor="middle" font-size="12.5" '
                         f'fill="#1e293b" font-weight="600">{cell["name"]}</text>')
            parts.append(f'<text x="{(xa+xb)/2:.1f}" y="{(ya+yb)/2+13:.1f}" '
                         f'text-anchor="middle" font-size="11" '
                         f'fill="#64748b">{cell["position"]:.0%}</text>')

    # 近期轨迹
    tail = df.tail(trail)
    tx = [px(v) for v in tail["trend_score"]]
    ty = [py(v) for v in tail["heat_score"]]
    parts.append(f'<path d="{_path(tx, ty)}" fill="none" stroke="#0f172a" '
                 f'stroke-width="1.2" opacity="0.45"/>')
    for i, (a, b) in enumerate(zip(tx, ty)):
        r = 2.0 + 3.4 * (i / max(1, len(tx) - 1))
        op = 0.25 + 0.7 * (i / max(1, len(tx) - 1))
        parts.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{r:.1f}" '
                     f'fill="#0f172a" opacity="{op:.2f}"/>')
    if tx:
        parts.append(f'<circle cx="{tx[-1]:.1f}" cy="{ty[-1]:.1f}" r="7" '
                     f'fill="none" stroke="#dc2626" stroke-width="2.4"/>')

    parts.append(f'<text x="{x0+pw/2:.1f}" y="{height-8}" text-anchor="middle" '
                 f'font-size="12" fill="#475569">趋势轴 →</text>')
    parts.append(f'<text x="16" y="{y0+ph/2:.1f}" text-anchor="middle" '
                 f'font-size="12" fill="#475569" '
                 f'transform="rotate(-90 16 {y0+ph/2:.1f})">温度轴 →</text>')
    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# 图 5: 板块热力图
# =============================================================================

def _heat_color(v: float) -> str:
    """0-100 分 -> 蓝(冷) 到 红(热) 的连续色"""
    if not np.isfinite(v):
        return "#f1f5f9"
    t = max(0.0, min(1.0, v / 100.0))
    if t < 0.5:
        k = t / 0.5
        r = int(59 + (241 - 59) * k)
        g = int(130 + (245 - 130) * k)
        b = int(246 + (249 - 246) * k)
    else:
        k = (t - 0.5) / 0.5
        r = int(241 + (185 - 241) * k)
        g = int(245 + (28 - 245) * k)
        b = int(249 + (28 - 249) * k)
    return f"rgb({r},{g},{b})"


def chart_sector_heatmap(history: pd.DataFrame, latest: pd.DataFrame,
                         top_n: int = 22, width: int = 1080) -> str:
    if history is None or history.empty:
        return "<p>数据不足</p>"

    order = latest.head(top_n).index.tolist()
    order = [c for c in order if c in history.columns]
    if not order:
        return "<p>数据不足</p>"

    hist = history[order]
    n_row, n_col = len(order), len(hist)
    cell_h = 20
    pad_l, pad_t, pad_b = 92, 30, 18
    cell_w = max(9.0, (width - pad_l - 16) / max(1, n_col))
    height = pad_t + n_row * cell_h + pad_b

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']

    name_of = latest["name"].to_dict() if "name" in latest.columns else {}
    for r, code in enumerate(order):
        y = pad_t + r * cell_h
        label = str(name_of.get(code, code))[:6]
        parts.append(f'<text x="{pad_l-8}" y="{y+14}" text-anchor="end" '
                     f'font-size="11.5" fill="#334155">{_esc(label)}</text>')
        for c in range(n_col):
            v = hist[code].iloc[c]
            x = pad_l + c * cell_w
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{cell_w-0.8:.1f}" '
                         f'height="{cell_h-1.6}" fill="{_heat_color(v)}"/>')

    step = max(1, n_col // 12)
    for c in range(0, n_col, step):
        x = pad_l + c * cell_w + cell_w / 2
        lab = pd.Timestamp(hist.index[c]).strftime("%m/%d")
        parts.append(f'<text x="{x:.1f}" y="{pad_t-8}" text-anchor="middle" '
                     f'font-size="10" fill="#64748b">{lab}</text>')

    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# 图 6: 板块四象限
# =============================================================================

def chart_sector_quadrant(latest: pd.DataFrame, width: int = 560,
                          height: int = 440, top_n: int = 30) -> str:
    if latest is None or latest.empty:
        return "<p>数据不足</p>"
    d = latest.head(top_n)

    pad_l, pad_r, pad_t, pad_b = 52, 18, 18, 40
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    ax = np.clip(d["S3_accel"].values.astype(float), -45, 45)
    lv = np.clip(d["S1_rps_mid"].values.astype(float), 0, 100)

    def px(v):
        return pad_l + (v + 45) / 90.0 * pw

    def py(v):
        return pad_t + ph - v / 100.0 * ph

    x_mid, y_mid = px(0), py(60)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']
    quads = [
        (pad_l, pad_t, x_mid - pad_l, y_mid - pad_t, "#fb923c", "退潮 FADING"),
        (x_mid, pad_t, pad_l + pw - x_mid, y_mid - pad_t, "#22c55e", "主升 LEADING"),
        (pad_l, y_mid, x_mid - pad_l, pad_t + ph - y_mid, "#94a3b8", "沉寂 LAGGING"),
        (x_mid, y_mid, pad_l + pw - x_mid, pad_t + ph - y_mid, "#38bdf8", "启动 EMERGING"),
    ]
    for x, y, w, h, c, lab in quads:
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                     f'height="{h:.1f}" fill="{c}" opacity="0.11"/>')
        parts.append(f'<text x="{x+w/2:.1f}" y="{y+16:.1f}" '
                     f'text-anchor="middle" font-size="11" fill="#64748b" '
                     f'opacity="0.85">{lab}</text>')

    parts.append(f'<line x1="{x_mid:.1f}" y1="{pad_t}" x2="{x_mid:.1f}" '
                 f'y2="{pad_t+ph}" stroke="#94a3b8" stroke-dasharray="4,4"/>')
    parts.append(f'<line x1="{pad_l}" y1="{y_mid:.1f}" x2="{pad_l+pw}" '
                 f'y2="{y_mid:.1f}" stroke="#94a3b8" stroke-dasharray="4,4"/>')

    names = d["name"].tolist() if "name" in d.columns else list(d.index)
    scores = d["score"].values.astype(float)
    for i in range(len(d)):
        x, y = px(ax[i]), py(lv[i])
        r = 3.5 + 4.5 * max(0.0, min(1.0, (scores[i] - 40) / 45))
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" '
                     f'fill="#0f172a" opacity="0.62"/>')
        if scores[i] >= 60 or i < 12:
            parts.append(f'<text x="{x+r+3:.1f}" y="{y+4:.1f}" font-size="10.5" '
                         f'fill="#1e293b">{_esc(str(names[i])[:5])}</text>')

    parts.append(f'<text x="{pad_l+pw/2:.1f}" y="{height-12}" '
                 f'text-anchor="middle" font-size="12" fill="#475569">'
                 f'加速度 (短期RPS - 中期RPS) →</text>')
    parts.append(f'<text x="15" y="{pad_t+ph/2:.1f}" text-anchor="middle" '
                 f'font-size="12" fill="#475569" '
                 f'transform="rotate(-90 15 {pad_t+ph/2:.1f})">中期强度 →</text>')
    parts.append('</svg>')
    return "".join(parts)


# =============================================================================
# HTML 组装
# =============================================================================

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:26px 30px 60px;background:#f8fafc;color:#0f172a;
     font-family:%s;font-size:14px;line-height:1.6}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:30px 0 10px;padding-bottom:7px;
   border-bottom:2px solid #e2e8f0;color:#1e293b}
.sub{color:#64748b;font-size:12.5px;margin-bottom:22px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;
      padding:16px 18px;margin-bottom:16px}
.hero{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:6px}
.kpi{flex:1;min-width:150px;background:#fff;border:1px solid #e2e8f0;
     border-radius:10px;padding:14px 16px}
.kpi .k{font-size:11.5px;color:#64748b;letter-spacing:.4px}
.kpi .v{font-size:27px;font-weight:650;margin-top:3px;line-height:1.15}
.kpi .n{font-size:11.5px;color:#94a3b8;margin-top:2px}
.banner{border-radius:10px;padding:16px 20px;margin-bottom:16px;color:#fff}
.banner .t{font-size:20px;font-weight:650}
.banner .a{font-size:13.5px;opacity:.94;margin-top:5px}
.two{display:flex;gap:16px;flex-wrap:wrap}
.two>div{flex:1;min-width:330px}
table{width:100%%;border-collapse:collapse;font-size:12.5px}
th{background:#f1f5f9;text-align:right;padding:7px 9px;font-weight:600;
   color:#475569;border-bottom:1px solid #e2e8f0;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:6px 9px;border-bottom:1px solid #f1f5f9;text-align:right;
   font-variant-numeric:tabular-nums}
tr:hover td{background:#f8fafc}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;
     font-weight:600}
.q-LEADING{background:#dcfce7;color:#15803d}
.q-EMERGING{background:#e0f2fe;color:#0369a1}
.q-FADING{background:#ffedd5;color:#c2410c}
.q-LAGGING{background:#f1f5f9;color:#64748b}
.warn{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;
      padding:11px 15px;border-radius:8px;margin-bottom:14px;font-size:13px}
.note{color:#64748b;font-size:12px;margin-top:8px}
""" % FONT


def _kpi(k: str, v: str, n: str = "", color: str = "#0f172a") -> str:
    return (f'<div class="kpi"><div class="k">{_esc(k)}</div>'
            f'<div class="v" style="color:{color}">{v}</div>'
            f'<div class="n">{_esc(n)}</div></div>')


def _sector_table(latest: pd.DataFrame, whitelist: pd.DataFrame,
                  top_n: int = 25) -> str:
    wl = set(whitelist.index) if whitelist is not None else set()
    rows = []
    for code, r in latest.head(top_n).iterrows():
        star = "★ " if code in wl else ""
        mf = r.get("S6_moneyflow")
        mf_txt = f"{mf*100:.2f}%" if pd.notna(mf) else "—"
        rows.append(
            f'<tr><td>{star}{_esc(r["name"])}</td>'
            f'<td><b>{r["score"]:.1f}</b></td>'
            f'<td>{r["S1_rps_mid"]:.0f}</td>'
            f'<td>{r["S3_accel"]:+.1f}</td>'
            f'<td>{(r["S2_breadth"] or 0)*100:.0f}%</td>'
            f'<td>{(r["S4_leader"] or 0)*100:.0f}%</td>'
            f'<td>{r["S5_vol_ratio"]:.2f}</td>'
            f'<td>{r["ret_short"]*100:+.1f}%</td>'
            f'<td>{mf_txt}</td>'
            f'<td><span class="tag q-{r["quadrant"]}">{r["quadrant_cn"]}</span></td>'
            f'<td style="text-align:left">{int(r["n_stocks"])}</td></tr>'
        )
    return (
        '<table><thead><tr><th>行业</th><th>总分</th><th>中期RPS</th>'
        '<th>加速</th><th>宽度</th><th>龙头</th><th>量比</th><th>20日</th>'
        '<th>资金流</th><th>象限</th><th>只数</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table>'
        '<div class="note">★ = 白名单。资金流 = 5 日主力净流入 / 行业成交额。</div>'
    )


def chart_geo(geo_df: pd.DataFrame, width: int = 1080, height: int = 200) -> str:
    """对抗分历史 + 分档带"""
    df = geo_df.dropna(subset=["geo_score"])
    if df.empty:
        return "<p>数据不足</p>"
    pad_l, pad_r, pad_t, pad_b = 58, 16, 14, 28
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(df)
    xs = np.linspace(pad_l, pad_l + pw, n)

    def yy(v):
        return _scale(v, 0, 100, pad_t + ph, pad_t)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'style="font-family:{FONT}">']
    for lo, hi, c in [(65, 100, "#fee2e2"), (35, 65, "#fef3c7"), (0, 35, "#dcfce7")]:
        a, b = yy(hi), yy(lo)
        parts.append(f'<rect x="{pad_l}" y="{a:.1f}" width="{pw}" '
                     f'height="{b-a:.1f}" fill="{c}" opacity="0.6"/>')
    for v in (0, 35, 65, 100):
        y = yy(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" y2="{y:.1f}" '
                     f'stroke="#e2e8f0"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#64748b">{v}</text>')

    parts.append(f'<path d="{_path(xs, yy(df["geo_score"].values))}" fill="none" '
                 f'stroke="#b91c1c" stroke-width="2.1"/>')
    if "g1_score" in df.columns:
        parts.append(f'<path d="{_path(xs, yy(df["g1_score"].values))}" fill="none" '
                     f'stroke="#7c2d12" stroke-width="1.2" stroke-dasharray="4,3" '
                     f'opacity="0.7"/>')
    if "g2_score" in df.columns and df["g2_score"].notna().any():
        parts.append(f'<path d="{_path(xs, yy(df["g2_score"].values))}" fill="none" '
                     f'stroke="#0369a1" stroke-width="1.2" stroke-dasharray="2,3" '
                     f'opacity="0.8"/>')

    for i, label in _date_ticks(df.index):
        parts.append(f'<text x="{xs[i]:.1f}" y="{height-9}" text-anchor="middle" '
                     f'font-size="11" fill="#64748b">{label}</text>')
    lx = pad_l + 8
    for name, col in [("综合对抗分", "#b91c1c"), ("G1 事件强度", "#7c2d12"),
                      ("G2 市场溢价", "#0369a1")]:
        parts.append(f'<rect x="{lx}" y="{pad_t+3}" width="13" height="3" fill="{col}"/>')
        parts.append(f'<text x="{lx+17}" y="{pad_t+9}" font-size="11" '
                     f'fill="#334155">{name}</text>')
        lx += 96
    parts.append('</svg>')
    return "".join(parts)


def build_dashboard(
    regime_df: pd.DataFrame,
    factors: pd.DataFrame,
    sector: Optional[Dict[str, pd.DataFrame]] = None,
    whitelist: Optional[pd.DataFrame] = None,
    config: Optional[RegimeConfig] = None,
    geo_df: Optional[pd.DataFrame] = None,
    title: str = "A股宏观状态与板块强度",
) -> str:
    """生成完整的单文件 HTML 仪表盘"""
    cfg = config or RegimeConfig()
    valid = regime_df.dropna(subset=["trend_score"])
    if valid.empty:
        return "<html><body>数据不足, 无法生成仪表盘</body></html>"

    last = valid.iloc[-1]
    as_of = pd.Timestamp(valid.index[-1]).date()
    cell_color = REGIME_COLORS.get(last["regime"], "#334155")

    def f(col, fmt="{:.1%}"):
        v = factors[col].reindex([valid.index[-1]]).iloc[0] if col in factors else np.nan
        return fmt.format(v) if pd.notna(v) else "—"

    warn = ""
    if bool(last.get("divergence", False)):
        warn = ('<div class="warn"><b>顶背离警告</b> — 趋势分仍处强区, '
                '但 RPS≥90 的强势股已大面积跌破 MA20。历史上这个组合领先'
                '中期顶部约 2~4 周, 仓位已被强制封顶。</div>')

    hero = "".join([
        _kpi("趋势轴 TrendScore", f"{last['trend_score']:.1f}",
             f"{'强' if last['trend_score']>cfg.trend_strong_above else ('弱' if last['trend_score']<cfg.trend_weak_below else '中性')}"
             f" · 阈值 {cfg.trend_weak_below:.0f}/{cfg.trend_strong_above:.0f}",
             "#1d4ed8"),
        _kpi("温度轴 HeatScore", f"{last['heat_score']:.1f}",
             f"{'过热' if last['heat_score']>cfg.heat_hot_above else ('偏冷' if last['heat_score']<cfg.heat_cold_below else '正常')}"
             f" · 阈值 {cfg.heat_cold_below:.0f}/{cfg.heat_hot_above:.0f}",
             "#ea580c"),
        _kpi("应激度 Stress", f"{last.get('stress_score', float('nan')):.1f}",
             f"波动 {f('H5_vol_annual')} · 回撤 {last.get('ewi_drawdown_pct', 0):.1f}%",
             "#7c3aed"),
        _kpi("目标仓位", f"{last['target_position']:.0%}",
             f"基准 {last['base_position']:.0%} × 波动 {last['vol_mult']:.2f}"
             f" × 回撤 {last['dd_mult']:.2f}", "#0f766e"),
    ])

    banner = (f'<div class="banner" style="background:{cell_color}">'
              f'<div class="t">【{_esc(last["regime_name"])}】</div>'
              f'<div class="a">{_esc(last["action"])}</div></div>')

    key_factors = "".join([
        f'<tr><td>{k}</td><td>{v}</td><td style="text-align:left;color:#64748b">{d}</td></tr>'
        for k, v, d in [
            ("T1 站上 MA200 占比", f("T1_breadth_ma200"), "牛熊分水岭, >60% 牛 / <30% 熊"),
            ("T2 站上 MA50 占比", f("T2_breadth_ma50"), "中期趋势, 领先 T1 约 1-2 月"),
            ("T4 MA200 斜率(年化)", f("T4_ewi_ma200_slope", "{:+.1%}"), "方向比位置更稳"),
            ("B1 新高新低差", f("B1_nhnl", "{:+.2%}"), "转负而指数仍涨 = 顶背离"),
            ("B4 龙头健康度", f("B4_leader_health"), "强势股站上 MA20 比例, <50% 撤退"),
            ("H1 量能脉冲", f("H1_turnover_pulse", "{:.2f}x"), "5日均额/60日均额, >1.5 放量"),
            ("H2 整体偏离 MA60", f("H2_extension", "{:+.1%}"), "全市场中位数, >15% 偏高"),
            ("H3 高位股占比", f("H3_high_zone"), "距 250 日高点 10% 以内"),
            ("H4 收益离散度", f("H4_dispersion"), "P90-P50, 飙升 = 主升末期"),
            ("H6 涨停温度", f("H6_blowoff", "{:.2%}"), "日涨>9.5% 占比 5 日均"),
        ]
    ])

    geo_html = ""
    if geo_df is not None and len(geo_df.dropna(subset=["geo_score"])):
        from .geopolitics import GEO_LEVELS
        gl = geo_df.dropna(subset=["geo_score"]).iloc[-1]
        lv = GEO_LEVELS[gl["geo_level"]]
        prem = gl.get("G2_market_premium")
        prem_txt = f"{prem:+.2%}" if pd.notna(prem) else "—"
        div = ""
        if pd.notna(gl.get("g2_score")):
            d = gl["g1_score"] - gl["g2_score"]
            if d > 25:
                div = ('<div class="warn"><b>G1/G2 背离</b> — 事件强度高但市场溢价低，'
                       '冲击尚未被充分定价，受损板块的利空可能还没走完。</div>')
            elif d < -25:
                div = ('<div class="warn"><b>G1/G2 背离</b> — 市场溢价高但事件强度低，'
                       '已过度定价对抗，若出现缓和则受损板块存在修复弹性。</div>')
        nxt = ""
        if pd.notna(gl.get("days_to_geo")):
            cap = "　<b>已进入窗口，仓位已封顶</b>" if gl.get("G3_in_window") else ""
            nxt = (f'<div class="note">下一个地缘事件：<b>{_esc(gl.get("next_geo_event",""))}</b>'
                   f'，还有 {int(gl["days_to_geo"])} 天{cap}</div>')
        geo_html = f"""
<h2>中美对抗因子</h2>
{div}
<div class="hero">
  {_kpi("综合对抗分 GeoScore", f"{gl['geo_score']:.1f}", lv["name"], lv["color"])}
  {_kpi("G1 事件强度", f"{gl['g1_score']:.1f}", f"原始强度 {gl['G1_event_intensity']:.2f}", "#7c2d12")}
  {_kpi("G2 市场隐含溢价", prem_txt, "自主可控 − 出口链，20日收益差", "#0369a1")}
  {_kpi("G3 事件窗口", f"{gl['g3_score']:.1f}", "距下一地缘事件的临近度", "#475569")}
</div>
<div class="card">{chart_geo(geo_df)}
<div class="note">{_esc(lv["note"])} G1 来自人工维护的事件日志（<code>macro/geo_events.json</code>），
G2 完全由价格算出。<b>两者背离本身就是信号</b>：G1 高而 G2 低意味着冲击还没被定价。</div>
{nxt}</div>"""

    sector_html = ""
    if sector and not sector["latest"].empty:
        sector_html = f"""
<h2>周度板块强度</h2>
<div class="two">
  <div class="card">{chart_sector_quadrant(sector["latest"])}
    <div class="note">圆点大小 = 综合分。只在<b>主升</b>与<b>启动</b>两个象限选股;
    <b>退潮</b>象限是"看起来强但资金在撤"的陷阱。</div></div>
  <div class="card" style="min-width:420px">{_sector_table(sector["latest"], whitelist)}</div>
</div>
<div class="card"><b style="font-size:13px">板块强度演化 (近 {len(sector["history"])} 周)</b>
{chart_sector_heatmap(sector["history"], sector["latest"])}
<div class="note">蓝 = 弱, 红 = 强。横向连续变红 = 主线在形成; 红转蓝 = 退潮。</div></div>
"""

    wl_html = ""
    if whitelist is not None:
        if whitelist.empty:
            wl_html = ('<div class="warn"><b>白名单为空</b> — 当前没有任何板块'
                       '同时满足"强度达标 + 有量 + 未退潮"。这本身就是一个减仓信号, '
                       '不要在这种环境下强行选股。</div>')
        else:
            items = "".join(
                f'<tr><td>{_esc(r["name"])}</td>'
                f'<td><span class="tag q-{r["quadrant"]}">{r["quadrant_cn"]}</span></td>'
                f'<td>{r["score"]:.1f}</td>'
                f'<td style="text-align:left;color:#475569">{_esc(r["note"])}</td></tr>'
                for _, r in whitelist.iterrows()
            )
            wl_html = (f'<h2>板块白名单 ({len(whitelist)} 个)</h2><div class="card">'
                       f'<table><thead><tr><th>行业</th><th>象限</th><th>分数</th>'
                       f'<th style="text-align:left">操作</th></tr></thead>'
                       f'<tbody>{items}</tbody></table>'
                       f'<div class="note">用法: '
                       f'<code>python run_tdx_screener.py</code> 的 L1/L2 结果'
                       f'只保留落在上述行业内的个股。</div></div>')

    n_switch = int((valid["regime"] != valid["regime"].shift()).sum())
    yrs = max(1e-9, len(valid) / 244)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} · {as_of}</title><style>{CSS}</style></head><body>
<h1>{_esc(title)}</h1>
<div class="sub">数据截至 {as_of} · 生成于 {datetime.now():%Y-%m-%d %H:%M} ·
样本 {len(valid)} 个交易日 · 历史状态切换 {n_switch} 次 ({n_switch/yrs:.1f} 次/年)</div>

{warn}
{banner}
<div class="hero">{hero}</div>

<h2>等权指数与状态色带</h2>
<div class="card">{chart_index_with_regime(regime_df, factors)}
<div class="note">黑线 = 全市场等权指数 (每日截面收益中位数累乘), 虚线 = 其 MA200。
背景色 = 当日状态。<b>这张图是检验状态机的核心</b> — 红色带应覆盖下跌段,
绿色带应覆盖上涨段。用等权而非上证指数, 是因为上证被银行石油权重扭曲,
多次出现"指数横盘个股腰斩"。</div></div>

<h2>双轴分数与目标仓位</h2>
<div class="two">
  <div class="card">{chart_scores(regime_df, cfg)}
    <div class="note">绿底 = 趋势强区, 红底 = 趋势弱区。</div>
    {chart_position(regime_df)}
    <div class="note">目标仓位 = 九宫格基准 × 波动缩放 × 回撤弹性 × 龙头封顶。</div></div>
  <div class="card" style="max-width:480px">{chart_grid(regime_df, cfg)}
    <div class="note">黑点 = 近 40 个交易日轨迹 (由浅到深), 红圈 = 当前位置。
    轨迹沿逆时针走 (冷→温→热→冷) 是健康的牛市节奏; 顺时针或在右上角滞留是危险信号。</div></div>
</div>

<h2>关键因子明细</h2>
<div class="card"><table><thead><tr><th style="text-align:left">因子</th>
<th>当前值</th><th style="text-align:left">含义</th></tr></thead>
<tbody>{key_factors}</tbody></table></div>

{geo_html}
{sector_html}
{wl_html}

<div class="note" style="margin-top:32px">
体系说明见 <code>docs/strategy_macro_regime.md</code>。
本报告为量化统计结果, 不构成投资建议。
</div>
</body></html>"""


def save_dashboard(html_text: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path
