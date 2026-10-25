#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

src = json.loads(Path("output/eastmoney_friday_reflow_20260814.json").read_text(encoding="utf-8"))

hy_q = [x for x in src["industry_reflow"] if x.get("both_in") and x["main_yi"] >= 8]
gn_q = [x for x in src["concept_reflow"] if x.get("both_in") and x["main_yi"] >= 20]
hy_miss = [x for x in src["industry_inflow_top20"] if not x.get("reflow")]
gn_miss = [x for x in src["concept_inflow_top20"][:12] if not x.get("reflow")]


def yi(v):
    if v is None:
        return "—"
    return f"{v:+.2f}"


def pct(v):
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def rows(items, extra=True):
    out = []
    for i, x in enumerate(items, 1):
        prior = yi(x.get("prior4_yi"))
        struct = "同向" if x.get("both_in") else ("分裂" if x.get("split") else "—")
        cls_p = "up" if (x.get("pct") or 0) > 0 else ("down" if (x.get("pct") or 0) < 0 else "flat")
        cls_m = "up" if x["main_yi"] > 0 else "down"
        out.append(
            f"<tr><td>{i}</td><td>{x['name']}<br><span class=muted>{x['code']}</span></td>"
            f"<td class={cls_p}>{pct(x.get('pct'))}</td>"
            f"<td class={cls_m}>{yi(x['main_yi'])}</td>"
            f"<td>{yi(x.get('super_yi'))}</td><td>{yi(x.get('big_yi'))}</td>"
            f"<td class=down>{prior}</td><td>{struct}</td><td>{x.get('leader') or '—'}</td></tr>"
        )
    return "\n".join(out)


now = datetime.now().strftime("%Y-%m-%d %H:%M")
html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>东财 0814 流入且回流板块</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9;--up:#c62828;--down:#16794b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1280px;margin:auto;padding:18px}}
.hero{{background:#0d2136;color:#fff;border-radius:16px;padding:24px}}
.eyebrow{{color:#9ecbeb;font-weight:700;letter-spacing:.08em;font-size:12px}}
h1{{font-size:24px;margin:8px 0 12px}}h2{{margin:0 0 12px;font-size:18px}}
.lede{{font-size:15px;line-height:1.7;color:#e8f1f8;margin:0}}
.metaline{{color:#c3d7e7;margin-top:12px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}
.card{{grid-column:span 12;background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.kv div{{background:#f5f8fb;border:1px solid var(--line);border-radius:10px;padding:11px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:16px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{color:var(--muted);background:#f6f8fa;font-weight:600}}
.up{{color:var(--up);font-weight:600}}.down{{color:var(--down);font-weight:600}}.flat{{color:var(--muted)}}
.muted{{color:var(--muted);font-size:12px}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:12px;border-radius:8px}}
.ok{{border-left:5px solid #16794b;background:#f3faf6;padding:12px;border-radius:8px}}
.risk{{border-left:5px solid #c54f3f;background:#fff6f4;padding:12px;border-radius:8px}}
footer{{color:var(--muted);padding:16px 4px;font-size:13px}}
</style></head><body><main class="wrap">
<section class="hero">
<div class="eyebrow">东方财富板块资金 · 估算主力净流入 · 不是交易所账户流水</div>
<h1>2026-08-14（周五）流入且回流的板块</h1>
<p class="lede">东财冻结快照：通信产业链是唯一同时满足「周五主力净流入、近4日净流出、超大单与大单同向」的主线。
概念层同一资金：光通信模块、CPO、液冷、算力。稀土周五也流入，但缺5日对照，不能判回流。
5G概念/通信技术周五净额很大，但大单为负，结构分裂，不算回流。</p>
<div class="metaline">采集 {now}（北京时间）· 数据对应 2026-08-14 收盘冻结 ·
来源 push2delay.eastmoney.com clist 今日 f62、5日 f164 ·
回流代理 = 5日主力净额 − 今日主力净额（近4日）&lt; 0 ·
周末拿不到 13:17 vs 14:30 分时快照，日K push2his 被限流未复核周四单日。</div>
</section>
<section class="grid">
<article class="card">
<div class="kv">
<div><small>行业硬回流</small><b>{len(hy_q)} 个 · 净流入≥8亿且同向</b></div>
<div><small>概念硬回流</small><b>{len(gn_q)} 个 · 净流入≥20亿且同向</b></div>
<div><small>第一主线</small><b>通信 / 光模块 / CPO</b></div>
<div><small>第二观察</small><b>被动元件、激光设备</b></div>
</div>
<p class="ok" style="margin-top:12px"><b>事实：</b>通信 BK1215 周五主力净流入 +139.07 亿，超大单 +122.23、大单 +16.84；近4日合计 −29.45 亿。光通信模块近4日 −115.83 亿后周五 +128.00 亿，是回流幅度最大的概念。</p>
<p class="warn"><b>口径：</b>这是成交方向模型估算，不是北向/机构账户。净流入不是买点。周末无法复现盘中 13:17→14:30 增量，回流用「5日−今日」近似。</p>
</article>
<article class="card"><h2>行业：流入且回流（超大单、大单同向，周五≥8亿）</h2>
<div class="tablebox"><table>
<thead><tr><th>#</th><th>板块</th><th>涨跌</th><th>周五主力(亿)</th><th>超大单</th><th>大单</th><th>近4日主力</th><th>结构</th><th>领涨</th></tr></thead>
<tbody>
{rows(hy_q)}
</tbody></table></div>
</article>
<article class="card"><h2>概念：流入且回流（同向，周五≥20亿）</h2>
<div class="tablebox"><table>
<thead><tr><th>#</th><th>板块</th><th>涨跌</th><th>周五主力(亿)</th><th>超大单</th><th>大单</th><th>近4日主力</th><th>结构</th><th>领涨</th></tr></thead>
<tbody>
{rows(gn_q)}
</tbody></table></div>
</article>
<article class="card"><h2>周五流入、但不能判回流</h2>
<p class="risk">下面这些周五净额不小，但要么超大单/大单分裂，要么东财5日字段对不上（无法算近4日）。<b>不列入回流名单。</b></p>
<div class="tablebox"><table>
<thead><tr><th>#</th><th>板块</th><th>涨跌</th><th>周五主力(亿)</th><th>超大单</th><th>大单</th><th>近4日主力</th><th>结构</th><th>领涨</th></tr></thead>
<tbody>
{rows(hy_miss + gn_miss)}
</tbody></table></div>
</article>
</section>
<footer>数据：东方财富 data.eastmoney.com/bkzj · 原始 JSON output/eastmoney_friday_reflow_20260814.json · 本页不构成交易指令。</footer>
</main></body></html>
"""
out = Path("output/a_share_analysis_20260814_eastmoney_reflow.html")
out.write_text(html, encoding="utf-8")
print("wrote", out, "bytes", out.stat().st_size)
