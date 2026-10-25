#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = json.loads((ROOT / "output/friday_inflow_core_20260814.json").read_text(encoding="utf-8"))
stamp = datetime.now().strftime("%H%M")
out = ROOT / "output" / f"a_share_analysis_20260814_{stamp}_inflow_ma.html"


def signed(v, n=2):
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    a = "↑" if v > 0 else ("↓" if v < 0 else "→")
    return f'<span class="{cls}">{v:+.{n}f} {a}</span>'


def tag(s):
    if s["ret1_pct"] >= 19.5:
        return "涨停/近涨停"
    if s["vs_ma20_pct"] > 20:
        return "乖离过大"
    if s["ret1_pct"] < 0 and s["amount_ratio"] and s["amount_ratio"] >= 3:
        return "放量收阴"
    if s["vs_ma20_pct"] <= 12 and s["vs_ma5_pct"] <= 8:
        return "均线附近·更像中军"
    return "通过均线过滤"


ind_rows = []
for i, r in enumerate(d["industries"], 1):
    note = {
        "食品饮料": "真增额，但上涨家数仅 5%、中位 −2.7%，是放量下跌",
        "互联网": "额缩于大盘，宽度 90%，结构好于食品",
        "供气供热": "真增额，上涨仅 28%，合格核心股只有 3 只",
        "软件服务": "额缩于大盘，成交额最大，合格 33 只",
    }.get(r["name"], "")
    ind_rows.append(
        f"<tr><td>{i}</td><td>{r['name']}</td><td>{r['members']}</td>"
        f"<td>{r['amount_yi']:.0f}</td><td>{r['amount_vs_adv20']:.2f}×</td>"
        f"<td>{r['amount_vs_prev']:.2f}</td><td>{r['vs_market']:.2f}</td>"
        f"<td>{r['up_pct']:.0f}%</td><td>{r['above_ma5_pct']:.0f}%</td>"
        f"<td>{r['above_ma20_pct']:.0f}%</td><td>{signed(r['median_ret1_pct'])}</td>"
        f"<td>{note}</td></tr>"
    )

stock_rows = []
for s in d["core_stocks"]:
    t = tag(s)
    stock_rows.append(
        f"<tr><td>{s['industry_inflow_rank']}</td><td>{s['industry_name']}</td>"
        f"<td>{s['ts_code']}</td><td>{s['name']}</td>"
        f"<td>{s['close']:.2f}</td><td>{signed(s['ret1_pct'])}</td>"
        f"<td>{signed(s['vs_ma5_pct'])}</td><td>{signed(s['vs_ma20_pct'])}</td>"
        f"<td>{s['amount_yi']:.1f}</td><td>{s['amount_ratio']}</td>"
        f"<td>{s['adv20_yi']:.1f}</td><td>{s['industry_amount_share_pct']:.0f}%</td>"
        f"<td>{s['qualified_in_industry']}/{s['members']}</td><td>{t}</td></tr>"
    )

html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>周五放量行业 · MA5/MA20 核心股 · 20260814</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9;--up:#c62828;--down:#16794b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1440px;margin:auto;padding:18px}}
.hero{{background:linear-gradient(135deg,#06182a,#11405f);color:#fff;border-radius:16px;padding:24px}}
h1{{margin:6px 0 10px;font-size:24px}}h2{{margin:0 0 12px}}
.lede{{color:#e8f1f8;font-size:15px}}
.meta{{color:#c3d7e7;margin-top:10px;font-size:13px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:14px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{background:#f6f8fa;color:var(--muted)}}
.up{{color:var(--up);font-weight:600}}.down{{color:var(--down);font-weight:600}}.flat{{color:var(--muted)}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:11px;border-radius:8px}}
.ok{{border-left:5px solid #16794b;background:#f3faf6;padding:11px;border-radius:8px}}
.tablebox{{overflow:auto}}
</style></head><body><main class="wrap">
<section class="hero">
<div style="letter-spacing:.08em;font-size:12px;color:#9ecbeb;font-weight:700">筛选 · 不是交易指令 · 无账户状态</div>
<h1>2026-08-14 相对放量行业中，站上 MA5 与 MA20 的核心股</h1>
<p class="lede">估算净流入接口不可用。这里的「流入」= 行业成交额相对 ADV20 放量，并且成交额降幅小于大盘（周五两市约缩 16%）。
核心股 = 这些行业里周五成交额靠前、收盘同时高于 MA5 和 MA20、ADV20≥1.5 亿。不是买入名单。</p>
<div class="meta">生成 {d['generated_at']} · 数据截至 2026-08-14 收盘 · 本地通达信日线 · 行业 tdxhy.cfg · 沪深 A 股不含北交所</div>
</section>

<article class="card"><h2>口径</h2>
<p>扫描 95 个行业（成分≥8 只）。入选：成交额/ADV20 ≥ 1.20，且相对大盘超额 ≥ 10%，排除单股成交占比 &gt;35%。
结果 4 个行业、58 只均线合格、每行业最多 5 只按成交额取核心，共 15 只。</p>
<p class="warn"><b>风险 · 成交额不是净流入。</b>食品饮料周五成交额相对昨日 +17%，但上涨家数只有 5%、中位跌 2.7%——这是资金在进出，方向是出。
均线过滤只回答「还在均线上方」，不回答「还值得买」。</p>
</article>

<article class="card"><h2>周五相对放量行业</h2>
<div class="tablebox"><table>
<thead><tr><th>#</th><th>行业</th><th>成分</th><th>成交额亿</th><th>额/ADV20</th><th>相对昨日</th>
<th>相对大盘</th><th>上涨%</th><th>站上MA5</th><th>站上MA20</th><th>中位涨跌</th><th>备注</th></tr></thead>
<tbody>{''.join(ind_rows)}</tbody></table></div>
</article>

<article class="card"><h2>核心股（收盘 &gt; MA5 且 &gt; MA20）</h2>
<div class="tablebox"><table>
<thead><tr><th>行业序</th><th>行业</th><th>代码</th><th>名称</th><th>收盘</th><th>当日</th>
<th>距MA5</th><th>距MA20</th><th>成交额亿</th><th>量比</th><th>ADV20亿</th>
<th>占行业额</th><th>行业合格数</th><th>标记</th></tr></thead>
<tbody>{''.join(stock_rows)}</tbody></table></div>
<p class="ok"><b>更接近「核心/中军」而不是脉冲的：</b>
昆仑万维、智度股份、恺英网络、世纪华通、九丰能源、亨通股份、润泽科技、奥飞数据、中泰股份。
共同特征是距 MA20 不超过约 12%，且不是涨停。</p>
<p class="warn"><b>均线上方但不宜当核心：</b>
网宿科技涨停；一鸣食品、皇氏集团放量收阴且距 MA20 25%–55%；行云科技、宏景科技距 MA20 约 20%。
这些通过了你要的均线条件，但位置或当日形态不支持当成中军。</p>
</article>

<article class="card"><h2>事实 / 推断 / 边界</h2>
<p><b>事实：</b>周五两市成交额约为上一交易日的 0.84；上述 4 个行业满足相对放量规则；表内 15 只收盘高于 MA5 与 MA20。</p>
<p><b>推断：</b>互联网与软件服务是缩量市里相对有成交的方向；食品饮料的「流入」更像高位兑现。</p>
<p><b>交易假设：</b>未提供持仓与风险预算，本页不生成买单、仓位或次日计划。</p>
</article>
</main></body></html>
"""
out.write_text(html, encoding="utf-8")
print(out)
print("NaN", html.count("NaN"), "http", html.count("http"))
