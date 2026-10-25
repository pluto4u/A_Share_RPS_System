#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = json.loads((ROOT / "output/eastmoney_reflow_ma_core_20260814.json").read_text(encoding="utf-8"))
now = datetime.now().strftime("%Y-%m-%d %H:%M")
cores = d["industry_cores"]
near = [x for x in cores if x["tag"] == "均线附近"]
hot = [x for x in cores if x["tag"] in ("站上均线", "涨停/近涨停")]
far = [x for x in cores if x["tag"] == "乖离过大"]


def signed(v, n=2):
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.{n}f}</span>'


def rows(items):
    out = []
    for i, x in enumerate(items, 1):
        boards = "、".join(x["boards"][:3])
        out.append(
            f"<tr><td>{i}</td><td>{x['ts_code']}</td><td>{x['name']}</td><td>{boards}</td>"
            f"<td>{x['close']:.2f}</td><td>{signed(x['ret1_pct'])}</td>"
            f"<td class=up>{x['main_yi']:+.2f}</td>"
            f"<td>{signed(x['vs_ma5_pct'])}</td><td>{signed(x['vs_ma20_pct'])}</td>"
            f"<td>{x['amount_yi']:.1f}</td><td>{x['amount_ratio']}</td>"
            f"<td>{x['adv20_yi']:.1f}</td><td>{x['tag']}</td></tr>"
        )
    return "\n".join(out)


html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>东财回流板块 · 站上MA5/MA20 核心股 · 20260814</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9;--up:#c62828;--down:#16794b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1440px;margin:auto;padding:18px}}
.hero{{background:#0d2136;color:#fff;border-radius:16px;padding:24px}}
.eyebrow{{color:#9ecbeb;font-weight:700;letter-spacing:.08em;font-size:12px}}
h1{{margin:8px 0 12px;font-size:24px}}h2{{margin:0 0 12px;font-size:18px}}
.lede{{color:#e8f1f8;font-size:15px;line-height:1.7}}
.meta{{color:#c3d7e7;margin-top:12px;font-size:13px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:14px}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.kv div{{background:#f5f8fb;border:1px solid var(--line);border-radius:10px;padding:11px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:16px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{background:#f6f8fa;color:var(--muted)}}
.up{{color:var(--up);font-weight:600}}.down{{color:var(--down);font-weight:600}}.flat{{color:var(--muted)}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:11px;border-radius:8px}}
.ok{{border-left:5px solid #16794b;background:#f3faf6;padding:11px;border-radius:8px}}
.risk{{border-left:5px solid #c54f3f;background:#fff6f4;padding:11px;border-radius:8px}}
footer{{color:var(--muted);padding:16px 4px;font-size:13px}}
</style></head><body><main class="wrap">
<section class="hero">
<div class="eyebrow">东财回流板块成分 · 个股主力净流入 · 本地日线 MA5/MA20</div>
<h1>2026-08-14 回流板块里，站上 MA5 与 MA20 的核心股</h1>
<p class="lede">板块来自东财：通信产业链周五流入且近4日回流。核心股再加三道过滤：东财个股主力净流入&gt;0、本地收盘同时高于 MA5 和 MA20、ADV20≥1.5亿。
不是买入名单。均线附近更像中军；乖离过大只说明它在板上，不说明能追。</p>
<div class="meta">采集 {now} · 交易日 2026-08-14 · 成分与个股资金 push2delay.eastmoney.com ·
价格 本地 TDX 未复权（G0 抽样 14/14 已通过，最新收盘与前复权对齐）· 沪深 A 股不含北交所</div>
</section>
<article class="card"><div class="kv">
<div><small>行业核心股</small><b>{d['industry_core_count']} 只</b></div>
<div><small>均线附近</small><b>{len(near)} 只</b></div>
<div><small>个股流入最大</small><b>亨通光电 +31.21 亿</b></div>
<div><small>距 MA20 最近</small><b>新易盛 +1.1%</b></div>
</div>
<p class="ok" style="margin-top:12px"><b>更像中军（均线附近）：</b>
新易盛（流入 +18.22 亿，距 MA20 +1.1%）、光迅科技、大族激光、华工科技、奥飞数据、烽火通信、长飞光纤、江海股份。
亨通光电流入最大，但距 MA20 已 +15.1%，是板上龙头不是回踩。</p>
<p class="warn"><b>口径：</b>东财个股主力净流入仍是成交方向估算。MA 用本地收盘。净流入且站上均线 ≠ 可执行买单。账户未提供，不生成仓位。</p>
</article>
<article class="card"><h2>均线附近（距 MA20 ≤12% 且距 MA5 ≤8%）</h2>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>收盘</th><th>涨跌%</th>
<th>主力(亿)</th><th>距MA5</th><th>距MA20</th><th>成交额亿</th><th>量比</th><th>ADV20亿</th><th>标记</th></tr></thead>
<tbody>{rows(near)}</tbody></table>
</article>
<article class="card"><h2>站上均线、位置更高</h2>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>收盘</th><th>涨跌%</th>
<th>主力(亿)</th><th>距MA5</th><th>距MA20</th><th>成交额亿</th><th>量比</th><th>ADV20亿</th><th>标记</th></tr></thead>
<tbody>{rows(hot[:12])}</tbody></table>
<p class="risk" style="margin-top:12px">天孚通信距 MA20 +25.8%、麦捷科技 +21.3%，已从核心表降到乖离过大，不列在上表。</p>
</article>
<footer>原始 JSON output/eastmoney_reflow_ma_core_20260814.json · 板块报告 output/a_share_analysis_20260814_eastmoney_reflow.html</footer>
</main></body></html>
"""
out = ROOT / "output/a_share_analysis_20260814_em_reflow_ma.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)
