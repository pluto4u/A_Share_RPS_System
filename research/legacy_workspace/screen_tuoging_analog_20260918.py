from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
sys.path.insert(0, str(ROOT / "tmp"))
from screen_siruipu_right_side_v2 import add_indicators

TARGET = pd.Timestamp("2026-09-18")
BENCH = "688072.SH"


def b(v) -> bool:
    return str(v).strip().lower() == "true"


def spark_svg(g: pd.DataFrame, label: str) -> str:
    s = g.tail(60).copy()
    base = float(s.close.iloc[0])
    vals = s.close.astype(float) / base * 100
    w, h, p = 270, 72, 7
    lo, hi = float(vals.min()), float(vals.max())
    span = max(hi - lo, 1)
    xs = np.linspace(p, w - p, len(vals))
    ys = p + (hi - vals.to_numpy()) / span * (h - 2 * p)
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(label)}近60日归一化收盘走势"><polyline points="{points}" fill="none" stroke="#1b4d7a" stroke-width="2.6"/><line x1="7" y1="{h-7}" x2="{w-7}" y2="{h-7}" stroke="#d9e2ec"/></svg>'


def fail_reasons(r: pd.Series) -> list[str]:
    reasons = []
    if not b(r.above_ma60): reasons.append("未站上MA60")
    if not b(r.ma20_rising): reasons.append("MA20未转升")
    if float(r.vs_ma20_pct) > 10: reasons.append("距MA20超过10%")
    if float(r.vs_ma20_pct) < 5: reasons.append("启动乖离不足5%")
    if float(r.vol_ratio) < 1.2: reasons.append("量比不足1.20")
    if float(r.vol_ratio) > 2.2: reasons.append("量能过激")
    if float(r.RPS_120) < 90: reasons.append("RPS120不足90")
    if float(r.RPS_250) < 85: reasons.append("RPS250不足85")
    if float(r.RPS_50) > 40: reasons.append("RPS50不再属于早期修复")
    if not b(r.g2_full): reasons.append("G2不完整")
    return reasons


def main() -> None:
    gate = json.loads((OUT / "qfq_verification.json").read_text(encoding="utf-8"))
    eco = json.loads((OUT / "limit_ecology_latest.json").read_text(encoding="utf-8"))
    audit = json.loads((OUT / "integrated_decision_20260918_audit.json").read_text(encoding="utf-8"))
    assert gate["price_adjustment"] == "verified_qfq"
    assert gate["full_market_checks"]["latest_anchor"]["max_date"] == "2026-09-18"
    assert eco["as_of_trade_date"] == "2026-09-18"

    a = pd.read_csv(OUT / "right_side_20260918_A_enriched.csv")
    for c in ["RPS_50", "RPS_120", "RPS_250", "vs_ma20_pct", "vol_ratio", "adv20_yi", "ret1_pct", "close", "ma20", "ma60", "atr20", "prior_high20", "prior_high60", "high_today", "low10"]:
        a[c] = pd.to_numeric(a[c], errors="coerce")
    up = a[a.role.eq("UPGRADE")].copy()

    strict_mask = (
        up.RPS_120.ge(90) & up.RPS_250.ge(85) & up.RPS_50.le(40)
        & up.vs_ma20_pct.between(5, 10, inclusive="both")
        & up.vol_ratio.between(1.2, 2.2, inclusive="both")
        & up.adv20_yi.ge(1.5)
        & up.above_ma60.map(b) & up.ma20_rising.map(b) & up.g2_full.map(b)
    )
    strict_all = up[strict_mask].copy()
    assert BENCH in set(strict_all.ts_code)
    strict = strict_all[strict_all.ts_code.ne(BENCH)].copy()

    industry_rows = audit["industries"]
    rank = {x["name"]: i + 1 for i, x in enumerate(industry_rows)}
    stats = {x["name"]: x for x in industry_rows}
    strict["industry_rank"] = strict.industry_name.map(rank).fillna(999)
    # Hierarchical ordering: same industry first, then industry rank, then RPS120. No additive score.
    strict["same_industry"] = strict.industry_name.eq("半导体")
    strict = strict.sort_values(["same_industry", "industry_rank", "RPS_120"], ascending=[False, True, False])

    near_mask = (
        up.RPS_120.ge(92) & up.RPS_250.ge(85) & up.RPS_50.le(50)
        & up.vs_ma20_pct.between(5, 12, inclusive="both")
        & up.vol_ratio.between(1.15, 2.3, inclusive="both")
        & up.adv20_yi.ge(1.5)
        & up.ts_code.ne(BENCH) & ~up.ts_code.isin(strict.ts_code)
    )
    near = up[near_mask].copy()
    near["reasons"] = near.apply(lambda r: "；".join(fail_reasons(r)), axis=1)
    near["industry_rank"] = near.industry_name.map(rank).fillna(999)
    near["same_industry"] = near.industry_name.eq("半导体")
    near = near[near.reasons.ne("")].sort_values(["same_industry", "industry_rank", "RPS_120"], ascending=[False, True, False]).head(8)

    selected_codes = [BENCH] + strict.ts_code.tolist() + near.ts_code.tolist()
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount", "RPS_50", "RPS_120", "RPS_250"]
    px = pd.read_parquet(OUT / "tdx_stock_daily_with_rps.parquet", columns=cols)
    px["trade_date"] = pd.to_datetime(px.trade_date)
    px = px[px.ts_code.isin(selected_codes) & px.trade_date.le(TARGET)].sort_values(["ts_code", "trade_date"])
    calc, series = {}, {}
    for code, g in px.groupby("ts_code"):
        z = add_indicators(g.reset_index(drop=True))
        tr = pd.concat([z.high-z.low, (z.high-z.close.shift()).abs(), (z.low-z.close.shift()).abs()], axis=1).max(axis=1)
        z["ATR20X"] = tr.rolling(20).mean()
        r, p = z.iloc[-1], z.iloc[-2]
        calc[code] = {
            "ret5_pct": float((r.close / z.close.iloc[-6] - 1) * 100),
            "ret10_pct": float((r.close / z.close.iloc[-11] - 1) * 100),
            "ret20_pct": float((r.close / z.close.iloc[-21] - 1) * 100),
            "ma10": float(r.M10), "ma20": float(r.M20), "ma60": float(r.M60),
            "ma20_slope": float(r.M20 - p.M20), "atr_pct": float(r.ATR20X / r.close * 100),
            "confirm": float(r.high), "support": float(max(x for x in [r.M10, r.M20, r.M60] if x < r.close)),
            "hard_fail": float(z.low.tail(20).min()), "prior20_high": float(z.high.iloc[-21:-1].max()),
            "prior60_high": float(z.high.iloc[-61:-1].max()),
        }
        series[code] = z

    records = []
    for _, r in strict.iterrows():
        m = {k: (v.item() if hasattr(v, "item") else v) for k, v in r.to_dict().items()}
        m.update(calc[r.ts_code])
        m["industry_rank"] = int(m["industry_rank"])
        records.append(m)
    near_records = []
    for _, r in near.iterrows():
        m = {k: (v.item() if hasattr(v, "item") else v) for k, v in r.to_dict().items()}
        m.update(calc[r.ts_code])
        m["industry_rank"] = int(m["industry_rank"])
        near_records.append(m)

    bench_row = strict_all[strict_all.ts_code.eq(BENCH)].iloc[0]
    benchmark = {k: (v.item() if hasattr(v, "item") else v) for k, v in bench_row.to_dict().items()}
    benchmark.update(calc[BENCH])

    payload = {
        "as_of": "2026-09-18", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "definition": {
            "source_pool": "right_side_20260918_A_enriched.csv 中 UPGRADE",
            "strict": "RPS120≥90、RPS250≥85、RPS50≤40、站上MA60、MA20转升、距MA20为5%至10%、量比1.2至2.2、ADV20≥1.5亿、G2完整",
            "ordering": "同属半导体优先；随后按行业排名、RPS120层级排序；未使用加总分",
        },
        "benchmark": benchmark, "strict_peers": records, "near_misses": near_records,
        "decision": audit["decision"], "industry_stats": {n: stats[n] for n in {r["industry_name"] for r in records + near_records} if n in stats},
        "ecology": {"state": eco.get("cycle_state"), "ST_cycle": eco.get("ST_cycle"), "st_cycle_formal": eco.get("st_cycle_formal")},
    }
    (OUT / "a_share_analysis_20260919_tuojing_analog_screen.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    strict_rows = "".join(
        f'<tr><td><b>{html.escape(str(m["name"]))}</b><br><code>{m["ts_code"]}</code></td><td>{html.escape(str(m["industry_name"]))} #{m["industry_rank"]}</td><td>{m["close"]:.2f}<br><span class="up">{m["ret1_pct"]:+.2f}%</span></td><td>{m["RPS_50"]:.1f}/{m["RPS_120"]:.1f}/{m["RPS_250"]:.1f}</td><td>{m["ret5_pct"]:+.1f}%/{m["ret10_pct"]:+.1f}%</td><td>{m["vs_ma20_pct"]:+.1f}%</td><td>{m["vol_ratio"]:.2f}×</td><td>{m["atr_pct"]:.1f}%</td><td>{spark_svg(series[m["ts_code"]], str(m["name"]))}</td><td>观察：{m["confirm"]:.2f}上方确认；{m["support"]:.2f}支撑；{m["hard_fail"]:.2f}结构失效</td></tr>'
        for m in records
    )
    near_rows = "".join(
        f'<tr><td><b>{html.escape(str(m["name"]))}</b><br><code>{m["ts_code"]}</code></td><td>{html.escape(str(m["industry_name"]))} #{m["industry_rank"]}</td><td>{m["close"]:.2f}</td><td>{m["RPS_50"]:.1f}/{m["RPS_120"]:.1f}/{m["RPS_250"]:.1f}</td><td>{m["vs_ma20_pct"]:+.1f}%</td><td>{m["vol_ratio"]:.2f}×</td><td class="risk">{html.escape(str(m["reasons"]))}</td></tr>'
        for m in near_records
    )
    peer_names = "、".join(str(m["name"]) for m in records)
    first = records[0] if records else None
    first_line = (
        f'最接近的是{first["name"]}：与拓荆同属半导体，RPS120={first["RPS_120"]:.1f}、RPS250={first["RPS_250"]:.1f}，距MA20 {first["vs_ma20_pct"]:+.1f}%，量比{first["vol_ratio"]:.2f}倍。'
        if first else "没有股票严格通过同形条件。"
    )
    html_doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>拓荆科技同形股票筛选</title><style>
    :root{{--navy:#142338;--bg:#f3f6fa;--line:#dce4ed;--red:#c7353b;--green:#087e5d;--amber:#9b5d0b;--muted:#657084}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#17202e;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1420px;margin:auto;padding:20px}}header,section{{background:white;border:1px solid var(--line);border-radius:18px;padding:23px;margin-bottom:16px}}header{{background:var(--navy);color:white}}h1{{font-size:29px;margin:4px 0 9px}}h2{{margin:0 0 10px}}.badge{{display:inline-block;padding:5px 10px;border:1px solid #7692ae;border-radius:9px;margin:3px 6px 3px 0}}.muted{{color:var(--muted)}}header .muted{{color:#cbd5e1}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:middle}}th{{background:#edf2f7}}.up{{color:var(--red)}}.risk{{color:var(--amber);font-weight:700}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}}.card{{border:1px solid var(--line);border-radius:13px;padding:14px}}.num{{font:700 23px ui-monospace,Consolas,monospace}}.callout{{background:#fff7e7;border-left:5px solid #d9a43d;padding:13px 16px}}svg{{width:220px;height:58px;display:block}}code{{font-size:13px}}@media(max-width:800px){{main{{padding:9px}}.cards{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}}}</style></head><body><main>
    <header><div>本地沪深日K · 拓荆科技同形筛选</div><h1>有3只严格同形，但全部仍是观察名单</h1><p><span class="badge">数据截至 2026-09-18</span><span class="badge">严格候选：{len(records)}只</span><span class="badge">G0/G1通过</span><span class="badge">G3/G5未通过</span></p><p>严格候选：{peer_names}。{first_line}</p><p class="muted">不是按行业故事找股票，而是按拓荆当日的RPS—均线—量价结构逐层筛选。当前系统结论仍是“研究观察，不产生可执行买单”，不能把同形直接翻译成买入。</p></header>
    <section><h2>筛选定义</h2><div class="cards"><div class="card"><div>慢周期质量</div><div class="num">RPS120≥90</div><p>RPS250≥85、RPS50≤40，保留中长周期强、短周期刚修复的特征。</p></div><div class="card"><div>右侧结构</div><div class="num">站上MA60</div><p>MA20转升、G2完整、处于启动而非仍在均线下方。</p></div><div class="card"><div>不过热与流动性</div><div class="num">乖离5%–10%</div><p>量比1.2–2.2、ADV20≥1.5亿元，兼顾启动确认与T+1退出。</p></div></div><p>排序采用串联门控：同属半导体优先，再看行业排名与RPS120层级；没有制作加总分。</p></section>
    <section><h2>严格同形候选</h2><div class="scroll"><table><thead><tr><th>股票</th><th>行业/排名</th><th>收盘/当日</th><th>RPS 50/120/250</th><th>5日/10日</th><th>距MA20</th><th>量比</th><th>ATR</th><th>近60日走势</th><th>确认与失效</th></tr></thead><tbody>{strict_rows}</tbody></table></div><p class="callout"><b>执行含义：</b>三只与拓荆“长周期强、短周期启动”的形态相似，不代表未来路径相同。若下一交易日直接高开追价，T+1当日无法纠错；只观察确认位能否收盘站稳，或回踩关键均线是否缩量承接。</p></section>
    <section><h2>近似但未通过严格门控</h2><div class="scroll"><table><thead><tr><th>股票</th><th>行业/排名</th><th>收盘</th><th>RPS 50/120/250</th><th>距MA20</th><th>量比</th><th>落选原因</th></tr></thead><tbody>{near_rows}</tbody></table></div><p>这些股票只能作为后续观察池；不能因名称熟悉、单日大涨或行业强而临时放宽条件。</p></section>
    <section><h2>为什么现在不能直接下单</h2><ul><li>9月18日行业共振排序中，酿酒排名第一但被判定为单股驱动；系统没有产生合格的第一行业，因此G3未通过。</li><li>元器件排名第2、半导体排名第3且均非单股驱动，但在现行门控下只能观察，不能自动递补为第一行业。</li><li>账户权益、可用现金、持仓成本、当天可卖数量和单笔风险预算未提供，G5无法计算仓位。</li><li>ST_cycle={eco.get('ST_cycle')}、状态“{eco.get('cycle_state')}”，st_cycle_formal={str(eco.get('st_cycle_formal')).lower()}，只作风险上限参考。</li></ul><p><b>空仓：</b>先观察严格候选的确认或回踩，不追开盘急拉。<b>已有仓：</b>按各自MA20/MA60管理，不因为再次入选同形池加仓。<b>机会成本：</b>等待可能少吃一段V形拉升，但避免把一次放量反弹误当持续主升。</p></section>
    <section><h2>数据与审计边界</h2><p><b>事实：</b>本地TDX原始收盘锚与本地v_daily_qfq等比前复权指标，截至2026-09-18；G0本地运营一致性通过，但不是独立外部复权证明。</p><p><b>推断：</b>“同形”只描述历史价格结构相似度，不代表基本面、行业地位或未来收益相同。</p><p><b>交易假设：</b>只有确认位收盘站稳，或关键均线回踩获得承接，延续上涨的赔率才改善。</p><p>未使用估算资金流、陈旧概念板块或分钟级CER。覆盖沪深A股，不含北交所。</p></section>
    </main></body></html>'''
    assert "NaN" not in html_doc and "Infinity" not in html_doc and "http://" not in html_doc and "https://" not in html_doc
    report = OUT / "a_share_analysis_20260919_tuojing_analog_screen.html"
    report.write_text(html_doc, encoding="utf-8")
    print(json.dumps({"report": str(report), "strict": records, "near": near_records}, ensure_ascii=False))


if __name__ == "__main__":
    main()
