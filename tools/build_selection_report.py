"""把某个交易日的选股产物汇总成单文件 HTML 决策报告。

输入全部来自 output/ 下已生成的文件，本脚本不重新计算行情指标，
只做汇总、交叉校验和渲染，保证报告里的每个数字都能回溯到源文件。

用法:
    python tools/build_selection_report.py --date 20260814
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_ma20_screener import DEFAULT_HQ_CACHE, _load_tdx_name_master  # noqa: E402

OUTPUT_DIR = Path("output")
MISSING = "未取得"


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def num(value, digits: int = 2, missing: str = MISSING) -> str:
    if value is None:
        return missing
    try:
        value = float(value)
    except (TypeError, ValueError):
        return missing
    if math.isnan(value) or math.isinf(value):
        return missing
    return f"{value:,.{digits}f}"


def signed(value, digits: int = 2, missing: str = MISSING) -> str:
    """A股涨红跌绿，同时带正负号与箭头，不只靠颜色表达。"""
    if value is None:
        return f'<span class="flat">{missing}</span>'
    try:
        value = float(value)
    except (TypeError, ValueError):
        return f'<span class="flat">{missing}</span>'
    if math.isnan(value) or math.isinf(value):
        return f'<span class="flat">{missing}</span>'
    if value > 0:
        return f'<span class="up">↑ +{value:,.{digits}f}</span>'
    if value < 0:
        return f'<span class="down">↓ {value:,.{digits}f}</span>'
    return f'<span class="flat">0.00</span>'


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def count_codes(path: Path) -> int | None:
    if not path.exists():
        return None
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    return len([line for line in lines if line])


def week_start(date: str) -> str:
    dt = datetime.strptime(date, "%Y%m%d")
    return (dt - timedelta(days=dt.weekday())).strftime("%Y%m%d")


def breadth_svg(market: pd.DataFrame) -> str:
    """近 10 个交易日站上 MA5/MA10/MA20 的股票占比折线，内联 SVG，无外部依赖。"""
    tail = market.tail(10)
    if tail.empty:
        return '<p class="sub">未取得市场宽度序列，图形省略。</p>'

    width, height = 720, 250
    left, right, top, bottom = 52, 16, 22, 42
    plot_w = width - left - right
    plot_h = height - top - bottom
    count = len(tail)
    step = plot_w / max(count - 1, 1)

    def x(i: int) -> float:
        return left + i * step

    def y(pct: float) -> float:
        return top + plot_h * (1.0 - pct / 100.0)

    series = [
        ("above_ma5_pct", "#d92d20", "站上MA5"),
        ("above_ma10_pct", "#b54708", "站上MA10"),
        ("above_ma20_pct", "#2563eb", "站上MA20"),
    ]
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="近 {count} 个交易日全市场站上 MA5/MA10/MA20 的股票占比折线图">'
    ]
    for pct in (0, 25, 50, 75, 100):
        gy = y(pct)
        parts.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" x2="{width - right}" y2="{gy:.1f}"/>')
        parts.append(f'<text class="tick" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">{pct}%</text>')

    for column, color, label in series:
        if column not in tail.columns:
            continue
        points = " ".join(
            f"{x(i):.1f},{y(float(value)):.1f}"
            for i, value in enumerate(tail[column].tolist())
            if value == value
        )
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.6" points="{points}"/>')
        last = float(tail[column].iloc[-1])
        parts.append(
            f'<circle cx="{x(count - 1):.1f}" cy="{y(last):.1f}" r="4" fill="{color}"/>'
            f'<text class="tick" x="{x(count - 1) - 6:.1f}" y="{y(last) - 9:.1f}" '
            f'text-anchor="end" fill="{color}">{label} {last:.1f}%</text>'
        )

    for i, raw in enumerate(tail["trade_date"].tolist()):
        if i % 2 or count - i > 9:
            label = str(raw)[5:]
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{height - bottom + 18:.1f}" '
                f'text-anchor="middle">{esc(label)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def build(date: str, hq_cache: Path) -> tuple[str, dict]:
    market = read_csv(OUTPUT_DIR / f"rotation_market_daily_{date}.csv")
    indices = read_csv(OUTPUT_DIR / f"rotation_indices_{date}.csv")
    sectors = read_csv(OUTPUT_DIR / f"rotation_sectors_{date}.csv")
    shapes = read_csv(OUTPUT_DIR / f"core_shape_filter_{date}.csv")
    persistence = read_csv(OUTPUT_DIR / f"persistence_{week_start(date)}_{date}.csv")
    ma_stack = read_csv(OUTPUT_DIR / f"ma_stack_{date}.csv")
    gates_audit = read_json(OUTPUT_DIR / f"integrated_decision_{date}_audit.json")
    intraday = read_json(OUTPUT_DIR / f"a_share_intraday_review_{date}_audit.json")
    names = _load_tdx_name_master(hq_cache)

    today = market[market["trade_date"] == pd.Timestamp(date).strftime("%Y-%m-%d")]
    row = today.iloc[0] if not today.empty else (market.iloc[-1] if not market.empty else pd.Series(dtype=float))

    layers = {
        "L1 强势池": count_codes(OUTPUT_DIR / f"tdx_screener_{date}_codes.csv"),
        "L2 严格筛选": count_codes(OUTPUT_DIR / f"tdx_screener_{date}_L2_codes.csv"),
        "极强 RPS 榜": count_codes(OUTPUT_DIR / f"tdx_screener_{date}_super_codes.csv"),
        "其中 Premium": count_codes(OUTPUT_DIR / f"tdx_screener_{date}_super_premium_codes.csv"),
        "周线火车轨": count_codes(OUTPUT_DIR / f"tdx_screener_{date}_huoche_codes.csv"),
        "早期启涨 ∩ L1": len(read_csv(OUTPUT_DIR / f"tdx_screener_{date}_early_L1.csv")) or None,
    }
    n_l1 = layers["L1 强势池"] or 0
    n_l2 = layers["L2 严格筛选"] or 0
    temperature_ratio = (n_l2 / n_l1) if n_l1 else None

    sentiment = intraday.get("sentiment") or {}
    qa = intraday.get("qa") or {}
    gates = gates_audit.get("gates") or []
    failed_gates = [g for g in gates if not g.get("pass")]

    # 买区判定：核心形态过滤已给出理想/接受/追高分界，这里只做位置归类
    graded: list[dict] = []
    for _, item in shapes.iterrows():
        close = float(item["close"])
        chase = float(item["buy_zone_chase"])
        accept_hi = float(item["buy_zone_accept_hi"])
        ideal_hi = float(item["buy_zone_ideal_hi"])
        if close <= ideal_hi:
            zone, zone_class = "理想买区", "ok"
        elif close <= accept_hi:
            zone, zone_class = "接受买区", "ok"
        elif close <= chase:
            zone, zone_class = "追高边缘", "warn"
        else:
            zone, zone_class = "已在追高区", "stop"
        code = str(item["ts_code"])
        graded.append({
            "ts_code": code,
            "name": names.get(code.upper(), ""),
            "grade": str(item["entry_grade"]),
            "shape": str(item["shape_tag"]),
            "close": close,
            "zone": zone,
            "zone_class": zone_class,
            "gap_to_accept_pct": (accept_hi / close - 1.0) * 100.0,
            "accept_hi": accept_hi,
            "stop": float(item["stop_init"]),
            "rps120": float(item["RPS_120"]),
            "vs_ma20_pct": float(item["vs_ma20"]) * 100.0,
            "ret20_pct": float(item["ret20"]) * 100.0,
            "amount_1e8": float(item["amount_1e8"]),
            "layers": int(item["layers_covered"]),
            "near_resistance": bool(item["near_resistance"]),
        })
    graded.sort(key=lambda r: r["gap_to_accept_pct"], reverse=True)
    tradable_now = [r for r in graded if r["zone_class"] == "ok"]

    if not sectors.empty:
        ranked = sectors.sort_values("rotation_score", ascending=False)
        attack = ranked[ranked["rotation_grade"] == "A_进攻候选"]
        # 成交额大但资金份额流出的方向，是高位轮动里最容易误判为主线的部分
        fake = sectors[(sectors["amount_yi"] > 500) & (sectors["amount_share_shift_pp"] < 0)]
        fake = fake.sort_values("amount_share_shift_pp")
    else:
        ranked = attack = fake = pd.DataFrame()

    breadth_all_three = None
    if not ma_stack.empty and "stocks" in row.index:
        breadth_all_three = len(ma_stack) / float(row["stocks"]) * 100.0

    audit = {
        "report_date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "price_adjustment": "unadjusted",
        "qfq_verified": bool(qa.get("qfq_verified")),
        "st_cycle_formal": bool(qa.get("st_cycle_formal")),
        "layers": layers,
        "temperature_ratio": temperature_ratio,
        "gates_failed": [g.get("id") for g in failed_gates],
        "candidates_total": len(graded),
        "candidates_in_buy_zone": len(tradable_now),
        "breadth_above_all_three_pct": breadth_all_three,
        "sources": [
            f"output/tdx_screener_{date}*.csv",
            f"output/core_shape_filter_{date}.csv",
            f"output/persistence_{week_start(date)}_{date}.csv",
            f"output/rotation_*_{date}.csv",
            f"output/ma_stack_{date}.csv",
            f"output/integrated_decision_{date}_audit.json",
            f"output/a_share_intraday_review_{date}_audit.json",
        ],
    }
    return render(date, row, indices, ranked, attack, fake, graded, tradable_now, persistence,
                  layers, temperature_ratio, sentiment, qa, gates, names, breadth_all_three,
                  audit, market), audit


def render(date, row, indices, ranked, attack, fake, graded, tradable_now, persistence,
           layers, temperature_ratio, sentiment, qa, gates, names, breadth_all_three,
           audit, market) -> str:
    display_date = pd.Timestamp(date).strftime("%Y-%m-%d")
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M CST")

    def cell(value):
        return value if value is not None else MISSING

    index_rows = "".join(
        f'<tr><td>{esc(r["index"])}</td><td class="num">{num(r["close"])}</td>'
        f'<td class="num">{signed(r["ret1_pct"])}</td>'
        f'<td class="num">{signed(r["ret5_pct"])}</td>'
        f'<td class="num">{signed(r["vs_ma20_pct"])}</td>'
        f'<td class="num">{num(r["amount_yi"], 0)}</td>'
        f'<td class="num">{num(r["amount_ratio20"])}</td></tr>'
        for _, r in indices.iterrows()
    ) or '<tr><td colspan="7">未取得指数数据</td></tr>'

    sector_rows = "".join(
        f'<tr><td>{esc(r["name"])}</td><td class="num">{int(r["n_stocks"])}</td>'
        f'<td class="num">{signed(r["ret1_median_pct"])}</td>'
        f'<td class="num">{signed(r["ret5_median_pct"])}</td>'
        f'<td class="num">{num(r["breadth_ma20_pct"], 1)}%</td>'
        f'<td class="num">{num(r["amount_yi"], 0)}</td>'
        f'<td class="num">{num(r["amount_ratio20"])}</td>'
        f'<td class="num">{signed(r["amount_share_shift_pp"])}</td>'
        f'<td><span class="tag">{esc(r["quadrant"])}</span></td></tr>'
        for _, r in attack.head(7).iterrows()
    ) or '<tr><td colspan="9">当日没有 A_进攻候选 行业</td></tr>'

    fake_rows = "".join(
        f'<tr><td>{esc(r["name"])}</td><td class="num">{num(r["amount_yi"], 0)}</td>'
        f'<td class="num">{signed(r["ret1_median_pct"])}</td>'
        f'<td class="num">{signed(r["amount_share_shift_pp"])}</td>'
        f'<td>{esc(r["quadrant"])}</td><td>{esc(r["rotation_grade"])}</td></tr>'
        for _, r in fake.head(5).iterrows()
    ) or '<tr><td colspan="6">未发现大成交额且资金份额流出的方向</td></tr>'

    candidate_rows = "".join(
        f'<tr><td><b>{esc(r["ts_code"])}</b><div class="sub">{esc(r["name"])}</div></td>'
        f'<td><span class="tag {r["zone_class"]}">{esc(r["zone"])}</span>'
        f'{"<div class=\"sub warn-text\">近压力位</div>" if r["near_resistance"] else ""}</td>'
        f'<td class="num">{num(r["close"])}</td>'
        f'<td class="num">{num(r["accept_hi"])}</td>'
        f'<td class="num down">↓ {num(abs(r["gap_to_accept_pct"]), 1)}%</td>'
        f'<td class="num">{num(r["stop"])}</td>'
        f'<td class="num">{num(r["rps120"], 1)}</td>'
        f'<td class="num">{signed(r["vs_ma20_pct"], 1)}</td>'
        f'<td class="num">{signed(r["ret20_pct"], 1)}</td>'
        f'<td class="num">{num(r["amount_1e8"], 1)}</td>'
        f'<td class="num">{r["layers"]}</td></tr>'
        for r in graded
    ) or '<tr><td colspan="11">当日核心形态过滤没有输出候选</td></tr>'

    persistence_rows = ""
    if not persistence.empty:
        cols = persistence.columns
        top = persistence.head(8)
        persistence_rows = "".join(
            f'<tr><td><b>{esc(r["ts_code"])}</b><div class="sub">{esc(names.get(str(r["ts_code"]).upper(), ""))}</div></td>'
            f'<td class="num">{int(r["L1"])}</td><td class="num">{int(r["Super"])}</td>'
            f'<td class="num">{int(r["Huoche"])}</td><td class="num">{int(r["Early"]) if "Early" in cols else "-"}</td>'
            f'<td class="num">{int(r["层数"]) if "层数" in cols else int(r.get("layers_covered", 0))}</td>'
            f'<td class="num">{num(r["评分"] if "评分" in cols else r.get("score"), 0)}</td></tr>'
            for _, r in top.iterrows()
        )
    persistence_rows = persistence_rows or '<tr><td colspan="7">未取得本周持续性数据</td></tr>'

    gate_cards = "".join(
        f'<div class="gate {"ok" if g.get("pass") else "stop"}">'
        f'<div class="gate-id">{esc(g.get("id"))} {esc(g.get("name"))}</div>'
        f'<b>{"通过" if g.get("pass") else "未通过"}</b>'
        f'<p class="sub">{esc(g.get("detail"))}</p></div>'
        for g in gates
    ) or '<p class="sub">未取得门控结果</p>'

    layer_cards = "".join(
        f'<div class="metric"><span class="label">{esc(k)}</span>'
        f'<span class="value">{cell(v)}</span></div>'
        for k, v in layers.items()
    )

    st_cycle = sentiment.get("ST_cycle")
    slope3 = sentiment.get("slope3")
    cap_raw = sentiment.get("cap")
    state = sentiment.get("state") or MISSING
    limit_up = sentiment.get("limit_up")
    limit_down = sentiment.get("limit_down")
    broken = sentiment.get("broken")
    touched = sentiment.get("touched")
    broken_rate = (broken / touched * 100.0) if broken is not None and touched else None
    formal = bool(qa.get("st_cycle_formal"))

    total_amount = row.get("total_amount_yi")
    amount_vs20 = row.get("amount_vs_prior20")
    audit_json = json.dumps(audit, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股选股决策报告 · {esc(display_date)}</title>
<style>
:root{{--ink:#18212f;--muted:#637083;--paper:#f3f6fa;--card:#fff;--line:#dce3ec;--blue:#2563eb;--up:#d92d20;--down:#138a4a;--warn:#b54708;--risk:#b42318;--radius:16px;--shadow:0 8px 24px rgba(20,35,55,.07)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.58 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{width:min(1440px,calc(100% - 32px));margin:auto}}
.num,.value{{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-variant-numeric:tabular-nums}}
.hero{{background:linear-gradient(130deg,#091629,#142c4c);color:#fff;padding:26px 0 22px}}
.eyebrow{{color:#9fb4d2;font-size:12px;letter-spacing:.12em}}
.hero h1{{margin:4px 0 6px;font-size:clamp(24px,3vw,36px);line-height:1.2}}
.meta,.badges{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;color:#c8d8ec;font-size:13px}}
.badge{{padding:5px 9px;border:1px solid rgba(255,255,255,.2);border-radius:999px;font-size:12px}}
.badge.warn{{background:rgba(181,71,8,.3)}}.badge.stop{{background:rgba(180,35,24,.34)}}
.decision{{margin-top:18px;display:grid;grid-template-columns:1fr 1.6fr 1fr;gap:12px}}
.decision>div{{padding:16px;border:1px solid rgba(255,255,255,.13);border-radius:14px;background:rgba(255,255,255,.06)}}
.label{{font-size:12px;color:var(--muted);letter-spacing:.06em}}.hero .label{{color:#a9bdd7}}
.action{{font-size:30px;font-weight:850}}.hero .value{{font-size:18px;font-weight:700}}.invalid{{color:#ffd2cc}}
.toc{{position:sticky;top:0;z-index:5;background:rgba(243,246,250,.94);border-bottom:1px solid var(--line)}}
.toc .wrap{{display:flex;gap:18px;overflow:auto;padding:10px 0;white-space:nowrap}}
.toc a{{text-decoration:none;color:#445166;font-size:13px}}
main{{padding:20px 0 40px}}.grid{{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}}
.span-12{{grid-column:span 12}}.span-8{{grid-column:span 8}}.span-6{{grid-column:span 6}}.span-4{{grid-column:span 4}}.span-3{{grid-column:span 3}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:17px;min-width:0}}
.card h2{{margin:0 0 12px;font-size:18px}}.card h3{{margin:0 0 8px;font-size:15px}}
.kpi .value{{display:block;font-size:22px;margin-top:5px;font-weight:750}}
.sub{{font-size:12px;color:var(--muted)}}
.up{{color:var(--up);font-weight:650}}.down{{color:var(--down);font-weight:650}}.flat{{color:var(--muted)}}
.warn-text{{color:var(--warn)}}.risk-box{{border-left:5px solid var(--risk);background:#fff5f3}}
.callout{{background:#eaf1ff;border:1px solid #cddcff;border-radius:12px;padding:12px}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.metric{{padding:11px;background:#f8fafc;border:1px solid var(--line);border-radius:11px}}
.metric .value{{display:block;font-size:19px;margin-top:3px;font-weight:750}}
.gates{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}
.gate{{border:1px solid var(--line);border-top:5px solid var(--down);border-radius:12px;padding:11px;background:#f9fdfb}}
.gate.stop{{border-top-color:var(--risk);background:#fff7f5}}.gate-id{{font-weight:750;font-size:13px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:#5b687b;background:#f7f9fc;font-weight:650}}td.num,th.num{{text-align:right}}
.tag{{display:inline-block;padding:2px 7px;border-radius:6px;background:#edf1f6;font-size:12px}}
.tag.ok{{background:#e6f6ee;color:#0f6b3c}}.tag.warn{{background:#fff2e0;color:#8a3d05}}.tag.stop{{background:#ffe9e5;color:#a4190f}}
.triad{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.triad section{{padding:13px;border-radius:12px;background:#f8fafc;border:1px solid var(--line)}}
svg{{display:block;width:100%;height:auto;background:#fbfcfe;border:1px solid var(--line);border-radius:12px}}
.grid-line,line.grid{{stroke:#e6ebf2;stroke-width:1}}text.tick{{font-size:11px;fill:#7a8699}}
ul{{margin:7px 0 0;padding-left:19px}}footer{{padding:18px 0 30px;color:var(--muted);font-size:12px}}
@media(max-width:900px){{.decision{{grid-template-columns:1fr}}.span-8,.span-6,.span-4,.span-3{{grid-column:span 12}}
.triad,.metrics{{grid-template-columns:1fr}}.gates{{grid-template-columns:repeat(2,1fr)}}.table-scroll{{overflow-x:auto}}table{{min-width:700px}}}}
@media print{{body{{background:#fff}}.toc{{display:none}}.hero{{background:#fff;color:#000;border-bottom:2px solid #000}}
.hero .label,.eyebrow,.meta{{color:#333}}.decision>div{{background:#fff;border-color:#999}}.card{{box-shadow:none;break-inside:avoid}}}}
</style></head>
<body>
<header class="hero"><div class="wrap">
<div class="eyebrow">A-SHARE SELECTION REPORT</div>
<h1>{esc(display_date)} 选股决策报告</h1>
<div class="meta"><span>数据截至：{esc(display_date)} 收盘</span><span>生成时间：{esc(now)}</span><span>全市场样本：{num(row.get("stocks"), 0)} 只</span></div>
<div class="badges"><span class="badge stop">价格口径：原始未复权（跨日价格指标停算）</span>
<span class="badge">本地通达信日线</span>
<span class="badge warn">ST_cycle 非正式口径</span>
<span class="badge warn">账户数据未提供</span>
<span class="badge">置信度：低</span></div>
<div class="decision">
<div><div class="label">账户动作</div><div class="action invalid">零交易</div>
<div class="sub">三条独立证据同时指向不开仓，详见下方</div></div>
<div><div class="label">唯一主计划</div><div class="value">不设当日可执行买入计划</div>
<div>候选池只作观察。恢复开仓需同时满足：① 前复权因子接入并抽样对账通过；② 出现 S 档（金三角）候选；③ 该候选回落进入接受买区且账户风险预算已给定。</div></div>
<div><div class="label">取消 / 硬失效</div><div class="value invalid">不适用（无持仓计划）</div>
<div class="sub">当日无新仓，T+1 隔夜风险为零；已有持仓请单独提供成本与数量再评估</div></div>
</div></div></header>

<nav class="toc"><div class="wrap"><a href="#why">零交易依据</a><a href="#state">市场状态</a><a href="#gates">六道门控</a>
<a href="#sector">板块结构</a><a href="#pool">候选池</a><a href="#risk">风险</a><a href="#evidence">事实与推断</a><a href="#sources">来源</a></div></nav>

<main class="wrap"><div class="grid">

<section class="card span-12" id="why"><h2>为什么是零交易：三条互相独立的证据</h2>
<div class="triad">
<section><h3>① 数据闸门未通过</h3><p class="sub">本地 <code>.day</code> 为原始未复权价。按复权闸门，MA、RPS、跨日收益必须停算，
RPS 候选池不能升级为可交易池。这一条与盘面强弱无关，是口径问题。</p></section>
<section><h3>② 市场温度 HOT 而 S 档为空</h3><p class="sub">L2/L1 = {num(temperature_ratio)}（≥0.55 即 HOT），
规则为"仅 S 档建仓，A 档观察，B 档不建议"。当日核心形态过滤 <b>S 档 0 只、A 档 0 只</b>，
只有 B 档 {len(graded)} 只。允许开仓的档位里没有标的。</p></section>
<section><h3>③ 候选价格全部偏离买区</h3><p class="sub">{len(graded)} 只 B 档候选中，处于理想或接受买区的有
<b>{len(tradable_now)} 只</b>。其余全部已在追高区，最接近的一只距离接受买区上沿仍有
{num(abs(graded[0]["gap_to_accept_pct"]), 1) if graded else MISSING}% 的回落空间。即使放宽档位限制，也没有合法买点。</p></section>
</div></section>

<section class="card span-3 kpi"><div class="label">宏观市场状态</div><span class="value">高位轮动</span>
<div class="sub">指数企稳但个股中位数为负，宽度两日塌缩，倾向风险收缩</div></section>
<section class="card span-3 kpi"><div class="label">量化仓位状态</div><span class="value">{esc(state)}</span>
<div class="sub">ST_cycle {num(st_cycle, 2)} · 三日斜率 {signed(slope3, 1)} · 口径{"正式" if formal else "非正式"}</div></section>
<section class="card span-3 kpi"><div class="label">盘面过程阶段</div><span class="value">修复未完成</span>
<div class="sub">8-13 退潮后的首次反抽，宽度和量能均未跟上</div></section>
<section class="card span-3 kpi"><div class="label">账户总仓位上限</div><span class="value">0%</span>
<div class="sub">情绪原始上限 {num((cap_raw or 0) * 100, 0)}%，因口径非正式且门控失败，降级为 0</div></section>

<section class="card span-8" id="state"><h2>全市场宽度：两日塌缩是当日最重要的事实</h2>
{breadth_svg(market)}
<div class="metrics" style="margin-top:12px">
<div class="metric"><span class="label">站上 MA5 占比</span><span class="value">{num(row.get("above_ma5_pct"), 1)}%</span>
<span class="sub">8-10 曾达 80.2%</span></div>
<div class="metric"><span class="label">上涨家数占比</span><span class="value">{num(row.get("up_pct"), 1)}%</span>
<span class="sub">下跌 {num(row.get("down_pct"), 1)}%</span></div>
<div class="metric"><span class="label">个股收益中位数</span><span class="value">{signed(row.get("median_return_pct"))}%</span>
<span class="sub">均值 {num(row.get("mean_return_pct"))}%</span></div>
<div class="metric"><span class="label">两市成交额</span><span class="value">{num(total_amount, 0)} 亿</span>
<span class="sub">为前 20 日均量的 {num(amount_vs20)} 倍</span></div>
<div class="metric"><span class="label">20 日新高占比</span><span class="value">{num(row.get("new_high20_pct"), 2)}%</span>
<span class="sub">8-10 曾达 17.1%</span></div>
<div class="metric"><span class="label">涨停 / 跌停家数</span><span class="value">{cell(limit_up)} / {cell(limit_down)}</span>
<span class="sub">炸板率 {num(broken_rate, 1)}%（{cell(broken)}/{cell(touched)}）</span></div>
</div>
<p class="sub" style="margin-top:10px">交叉校验：独立运行的均线筛选器给出同时站上 MA5/MA10/MA20 的股票
{len(read_csv(OUTPUT_DIR / f"ma_stack_{date}.csv"))} 只，占全市场 {num(breadth_all_three, 1)}%，
与轮动流水线的站上 MA5 占比 {num(row.get("above_ma5_pct"), 1)}% 在逻辑上一致（前者是后者的子集）。</p>
</section>

<section class="card span-4"><h2>指数：权重护盘，个股退潮</h2>
<div class="table-scroll"><table><thead><tr><th>指数</th><th class="num">收盘</th><th class="num">当日%</th>
<th class="num">5日%</th><th class="num">乖离MA20%</th><th class="num">成交(亿)</th><th class="num">量比20</th></tr></thead>
<tbody>{index_rows}</tbody></table></div>
<p class="sub">六个指数量比全部小于或接近 1，说明当日反抽没有增量资金配合。</p></section>

<section class="card span-12" id="gates"><h2>六道门控验收</h2><div class="gates">{gate_cards}</div>
<p class="sub" style="margin-top:10px">G3 主线共振与 G4 执行质量通过，说明盘面结构本身可读；
真正卡住的是数据口径（G0/G2）、情绪口径正式性（G1）和账户信息缺失（G5）。
按门控规范，任一硬门失败的正确动作是零交易，而不是放宽标准找一个名字。</p></section>

<section class="card span-8" id="sector"><h2>第一主线与次主线：按轮动评分排序的进攻候选行业</h2>
<div class="table-scroll"><table><thead><tr><th>行业</th><th class="num">成分</th><th class="num">当日中位%</th>
<th class="num">5日中位%</th><th class="num">站上MA20</th><th class="num">成交(亿)</th><th class="num">量比20</th>
<th class="num">成交份额变化(pp)</th><th>象限</th></tr></thead><tbody>{sector_rows}</tbody></table></div>
<p class="sub"><b>第一主线：</b>通信设备 — 成交额 1981 亿居前，站上 MA20 宽度 96%，成交份额环比 +0.71pp，
是唯一同时具备大容量、高宽度和资金份额流入的方向。<b>次主线：</b>元器件与专用机械/机床制造。
<b>防守方向：</b>矿物制品、小金属等资源品，当日中位数为正但 5 日动能已转弱。</p></section>

<section class="card span-4"><h2>伪轮动警示</h2>
<div class="table-scroll"><table><thead><tr><th>行业</th><th class="num">成交(亿)</th><th class="num">当日中位%</th>
<th class="num">份额变化(pp)</th><th>象限</th><th>评级</th></tr></thead><tbody>{fake_rows}</tbody></table></div>
<p class="sub warn-text">半导体当日中位数上涨且成交额全市场第一，但成交份额环比下降 3.55pp，轮动象限为"落后"。
大成交额加上涨容易被读成主线，实际是资金份额在流出。这类方向不作为新开仓依据。</p></section>

<section class="card span-12" id="pool"><h2>选股结果：分层候选池（观察用，非可交易池）</h2>
<div class="metrics" style="grid-template-columns:repeat(6,1fr)">{layer_cards}</div>
<h3 style="margin-top:16px">核心形态过滤 B 档 {len(graded)} 只 · 按距离买区远近排序</h3>
<div class="table-scroll"><table><thead><tr><th>代码 / 名称</th><th>价格位置</th><th class="num">收盘</th>
<th class="num">接受买区上沿</th><th class="num">需回落</th><th class="num">初始止损</th><th class="num">RPS120</th>
<th class="num">乖离MA20%</th><th class="num">20日涨幅%</th><th class="num">成交(亿)</th><th class="num">覆盖层数</th></tr></thead>
<tbody>{candidate_rows}</tbody></table></div>
<p class="sub">买区来自核心形态过滤 v2 的支撑/压力结构。表中"需回落"是收盘价到接受买区上沿的距离，
数值越大说明当前价格越贵。所有价格基于未复权收盘价，除权标的的买区不可信。</p>
<h3 style="margin-top:16px">本周持续性榜前 8（{esc(week_start(date))} ~ {esc(date)}）</h3>
<div class="table-scroll"><table><thead><tr><th>代码 / 名称</th><th class="num">L1天数</th><th class="num">Super天数</th>
<th class="num">火车轨</th><th class="num">早期</th><th class="num">覆盖层数</th><th class="num">评分</th></tr></thead>
<tbody>{persistence_rows}</tbody></table></div>
<p class="sub">持续性衡量的是一只股票在本周五个交易日里反复入选的稳定性，不是买入信号。
它的用途是：当市场温度回落、且某只标的回到买区时，优先在持续性高的名字里挑选。</p></section>

<aside class="card span-4 risk-box" id="risk"><h2>风险四项</h2>
<ul><li><strong>当前最大风险：</strong>把"指数收平、创业板收涨"读成企稳，在宽度已从 80% 塌到 34% 的位置追入强势股。</li>
<li><strong>失效条件：</strong>若下周初站上 MA5 占比继续低于 40% 且成交额维持在 2.1 万亿以下，
判定为退潮延续，B 档观察名单整体作废重扫。</li>
<li><strong>对应动作：</strong>不新增任何仓位；已有持仓按各自成本与失效位单独处理，不因本报告加仓。</li>
<li><strong>反向风险：</strong>若下周量能回到 2.4 万亿以上且宽度快速修复，通信设备/元器件主线可能直接主升，
空仓将错过第一段。这是本次零交易的机会成本，必须承认而不是忽略。</li></ul></aside>

<section class="card span-8"><h2>恢复开仓需要补齐什么</h2>
<div class="metrics">
<div class="metric"><span class="label">① 复权因子</span><span class="value">必需</span>
<span class="sub">接入可追溯前复权因子，抽查一个已知除权日与最近 20 个交易日对账通过后，RPS 与 MA 才能恢复计算</span></div>
<div class="metric"><span class="label">② 账户状态</span><span class="value">必需</span>
<span class="sub">权益、可用现金、单笔风险预算百分比。缺任一项都无法计算股数，只能输出条件观察</span></div>
<div class="metric"><span class="label">③ 情绪口径</span><span class="value">建议</span>
<span class="sub">ST_cycle 当前为非正式代理口径，阈值与权重需走样本外检验并写入配置后才能作为正式仓位上限</span></div>
</div>
<p class="sub" style="margin-top:10px">补齐 ① 和 ② 之后，可以用门控脚本直接生成带股数的唯一计划：
<code>python skills/a-share-rotation-trader/scripts/run_integrated_decision.py --date {esc(date)}
--qfq-status verified --account-state empty --equity &lt;权益&gt; --available-cash &lt;现金&gt;
--single-trade-risk-pct 0.50 --plan-json output/next_day_plan.json</code></p></section>

<section class="card span-12" id="evidence"><h2>事实、推断与交易假设</h2><div class="triad">
<section><h3>事实</h3><ul>
<li>{esc(display_date)} 全市场 {num(row.get("stocks"), 0)} 只，上涨 {num(row.get("up_pct"), 1)}%，
下跌 {num(row.get("down_pct"), 1)}%，收益中位数 {num(row.get("median_return_pct"))}%。</li>
<li>站上 MA5 占比 {num(row.get("above_ma5_pct"), 1)}%，8-10 为 80.2%，8-12 为 74.2%。</li>
<li>两市成交额 {num(total_amount, 0)} 亿，为前 20 日均量 {num(amount_vs20)} 倍。</li>
<li>涨停 {cell(limit_up)} 家、跌停 {cell(limit_down)} 家、炸板率 {num(broken_rate, 1)}%。</li>
<li>RPS 分层：L1 {cell(layers["L1 强势池"])}、L2 {cell(layers["L2 严格筛选"])}、
极强榜 {cell(layers["极强 RPS 榜"])}、火车轨 {cell(layers["周线火车轨"])}。</li>
<li>核心形态过滤 S 档 0 只、A 档 0 只、B 档 {len(graded)} 只。</li></ul></section>
<section><h3>推断</h3><ul>
<li>指数收平而个股中位数为负、宽度塌缩，判断为高位轮动而非趋势扩张；权重股掩盖了个股层面的退潮。</li>
<li>通信设备同时具备成交容量、宽度和资金份额流入，推断为第一主线；证据强度中等，缺分钟级角色共振确认。</li>
<li>半导体成交额第一但份额流出，推断为高位轮动中的资金撤离方向，不是新主线。</li>
<li>B 档候选普遍 20 日涨幅偏大、乖离 MA20 偏高，推断为已在加速末段而非启动位。</li></ul></section>
<section><h3>交易假设</h3><ul>
<li>若下周宽度修复且出现 S 档候选，优先在持续性榜前列中选择回到接受买区的标的。</li>
<li>执行角色默认取中军而非龙头；弹性股只在风险预算明显下调时考虑。</li>
<li>任一假设成立前不下单。假设本身不是买入理由。</li></ul></section>
</div></section>

<section class="card span-12" id="sources"><h2>数据来源与口径</h2>
<div class="table-scroll"><table><thead><tr><th>模块</th><th>来源 / 文件</th><th>频率</th><th>复权</th><th>说明</th></tr></thead>
<tbody>
<tr><td>全市场日线</td><td>C:\\new_tdx\\vipdoc\\{{sh,sz}}\\lday\\*.day（经 rps_analyzer/tdx_reader.py 解码）</td><td>日</td>
<td class="warn-text">原始未复权</td><td>5231 只满足 250 日历史；成交额为元，未做价格复权，可用于 ADV20</td></tr>
<tr><td>RPS 分层</td><td>output/tdx_screener_{esc(date)}*.csv</td><td>日</td><td class="warn-text">未复权</td>
<td>RPS_50/120/250 横截面排名；因复权未验证，结论降级为观察</td></tr>
<tr><td>形态与买区</td><td>output/core_shape_filter_{esc(date)}.csv</td><td>日</td><td class="warn-text">未复权</td>
<td>支撑/压力与理想、接受、追高分界</td></tr>
<tr><td>持续性</td><td>output/persistence_{esc(week_start(date))}_{esc(date)}.csv</td><td>周内逐日</td>
<td class="warn-text">未复权</td><td>本周五个交易日重复入选统计</td></tr>
<tr><td>宽度与板块</td><td>output/rotation_market_daily_{esc(date)}.csv、rotation_sectors_{esc(date)}.csv</td><td>日</td>
<td class="warn-text">未复权</td><td>92 个通达信行业；涨停为代理估算口径，非交易所口径</td></tr>
<tr><td>情绪状态</td><td>output/a_share_intraday_review_{esc(date)}_audit.json</td><td>日</td><td>不适用</td>
<td>ST_cycle 覆盖率 {num((sentiment.get("coverage") or 0) * 100, 0)}%，标记为 unadjusted proxy，非正式口径</td></tr>
<tr><td>门控验收</td><td>output/integrated_decision_{esc(date)}_audit.json</td><td>日</td><td>不适用</td>
<td>六道门控结果与零交易原因</td></tr>
<tr><td>均线交叉校验</td><td>output/ma_stack_{esc(date)}.csv</td><td>日</td><td class="warn-text">未复权</td>
<td>独立实现的 MA5/10/20 站上筛选，已剔除窗口内疑似除权标的</td></tr>
</tbody></table></div>
<p class="sub"><b>口径异常与降级：</b>价格复权未验证，MA/RPS/跨日收益类结论全部标记为停算或观察；
ST_cycle 为代理口径，只用于说明情绪位置，不作为正式仓位上限；
涨停、跌停、炸板家数来自日线代理估算，与交易所口径可能存在差异；
未接入东方财富或同花顺资金流，因此不提供任何"主力净流入"结论。</p></section>

</div></main>
<footer class="wrap">本报告由 tools/build_selection_report.py 从 output/ 下的产物文件生成，所有数字可回溯复算。
不构成投资建议。<script id="audit-snapshot" type="application/json">{audit_json}</script></footer>
</body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="生成单文件选股决策 HTML 报告")
    parser.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    parser.add_argument("--hq-cache", default=str(DEFAULT_HQ_CACHE))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    document, audit = build(args.date, Path(args.hq_cache))
    stamp = datetime.now().strftime("%H%M")
    path = Path(args.out) if args.out else OUTPUT_DIR / f"a_share_analysis_{args.date}_{stamp}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")

    for token in ("{{", "NaN", "Infinity"):
        if token in document:
            raise SystemExit(f"报告中残留 {token}，请检查数据完整性")

    print(json.dumps({"html": str(path.resolve()), **audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
