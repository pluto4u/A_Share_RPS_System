#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = json.loads((ROOT / "output/ths_local_limit_compare_20260821.json").read_text(encoding="utf-8"))
now = datetime.now().strftime("%Y-%m-%d %H:%M")


def rows(items, extra=""):
    out = []
    for i, x in enumerate(items, 1):
        st = "ST" if x.get("is_st") else "—"
        h = x.get("height")
        ot = x.get("open_times")
        extra_td = f"<td>{ot if ot is not None else '—'}</td>" if extra == "break" else ""
        out.append(
            f"<tr><td>{i}</td><td>{x['ts_code']}</td><td>{x.get('name') or '—'}</td>"
            f"<td>{st}</td><td>{h if h is not None else '—'}</td>"
            f"<td>{x.get('pct')}</td>{extra_td}</tr>"
        )
    return "\n".join(out) if out else "<tr><td colspan=6>无</td></tr>"


html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>20260821 涨停生态对照 · 同花顺 vs 本地</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1100px;margin:auto;padding:18px}}
.hero{{background:#0d2136;color:#fff;border-radius:16px;padding:24px}}
.eyebrow{{color:#9ecbeb;font-weight:700;letter-spacing:.08em;font-size:12px}}
h1{{margin:8px 0 12px;font-size:24px}}h2{{margin:0 0 12px;font-size:18px}}
.lede{{color:#e8f1f8;font-size:15px}}
.meta{{color:#c3d7e7;margin-top:12px;font-size:13px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:14px}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.kv div{{background:#f5f8fb;border:1px solid var(--line);border-radius:10px;padding:11px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:16px}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}}
thead th{{background:#f6f8fa;color:var(--muted)}}
.ok{{border-left:5px solid #16794b;background:#f3faf6;padding:11px;border-radius:8px}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:11px;border-radius:8px}}
footer{{color:var(--muted);padding:16px 4px;font-size:13px}}
</style></head><body><main class="wrap">
<section class="hero">
<div class="eyebrow">校验 · 同花顺官方池 × 本地日线重建 · 沪深 A 股不含北交所</div>
<h1>2026-08-21 涨停生态对照</h1>
<p class="lede">同花顺涨停池 54 只全部落在本地重建里。连板高度 54/54 一致。
本地多出 5 只 ST（广度口径保留）和康希诺：收盘封住 20% 涨停，但盘中开板 13 次，同花顺把它放进炸板池。</p>
<div class="meta">对照时间 {now} · 本地 TDX 未复权日线 5306 只，当日 5193 只有效 ·
同花顺 hithink-finance special limit-*-pool · 非投资建议</div>
</section>
<article class="card"><div class="kv">
<div><small>涨停交集</small><b>{d['limit_up']['overlap']} / 同花顺 {d['limit_up']['ths_n']}</b></div>
<div><small>高度不一致</small><b>0 只</b></div>
<div><small>仅本地多出</small><b>{len(d['limit_up']['only_local'])} 只</b></div>
<div><small>仅同花顺多出</small><b>{len(d['limit_up']['only_ths'])} 只</b></div>
</div>
<p class="ok" style="margin-top:12px"><b>事实：</b>剔 ST 后本地 55、同花顺 54，唯一差额是康希诺。
同花顺涨停池漏判 0 只沪深非 ST 收盘涨停（除这只定义分歧）。</p>
<p class="warn"><b>口径：</b>本地涨停 = 收盘触及涨停价。同花顺炸板池包含盘中打开过的股票，即使收盘回封。
国仪公司 688828 涨 10.4%，未到 20% 涨停价，本地不算炸板，同花顺列入炸板池。</p>
</article>
<article class="card"><h2>仅本地有、同花顺涨停池没有</h2>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>ST</th><th>本地连板</th><th>涨跌%</th></tr></thead>
<tbody>{rows(d['limit_up']['only_local'])}</tbody></table>
</article>
<article class="card"><h2>跌停：仅本地多出</h2>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>ST</th><th>连板</th><th>涨跌%</th></tr></thead>
<tbody>{rows(d['limit_down']['only_local'])}</tbody></table>
<p>同花顺跌停 13 只全部被本地覆盖。本地多 *ST萃华 与哈药股份（收盘 −10.02%）。</p>
</article>
<article class="card"><h2>炸板错位</h2>
<p>数量都是 18，交集 16。</p>
<h3>仅本地</h3>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>ST</th><th>连板</th><th>涨跌%</th></tr></thead>
<tbody>{rows(d['limit_break']['only_local'])}</tbody></table>
<h3>仅同花顺</h3>
<table><thead><tr><th>#</th><th>代码</th><th>名称</th><th>ST</th><th>连板</th><th>涨跌%</th><th>开板次数</th></tr></thead>
<tbody>{rows(d['limit_break']['only_ths'], 'break')}</tbody></table>
</article>
<footer>原始对照 output/ths_local_limit_compare_20260821.json · 本地生态 JSON 仍停在 08-20，本次按 08-21 日线现场重建，未改写 ST_cycle 生产文件。</footer>
</main></body></html>
"""
out = ROOT / "output/a_share_analysis_20260821_ths_limit_compare.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)
