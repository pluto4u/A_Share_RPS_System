"""Read-only watchlist presentation; price references are not order levels."""

from __future__ import annotations

import html
from typing import Any

WATCH_STAGES = ("趋势观察", "缩量整理观察", "放量突破确认", "回踩再确认")


def watchlist(table: Any) -> Any:
    return table.loc[table.stage.isin(WATCH_STAGES)].copy()


def render_watchlist(table: Any, date: str, charts: dict[str, str]) -> str:
    selected = watchlist(table)
    version = "V2 分层" if "v2_failed" in table.columns else "V1"
    counts = selected.stage.value_counts().to_dict()
    stages = " · ".join(f"{stage} {counts.get(stage, 0)}只" for stage in WATCH_STAGES)
    cards = []
    for _, r in selected.iterrows():
        code, name, stage = (html.escape(str(r[x])) for x in ("ts_code", "name", "stage"))
        reason = html.escape(
            str(r.get("observation_reason", "通过强度和趋势筛选，且距MA20未超过12%。"))
        )
        cards.append(f'''<article class="stock" data-search="{code} {name} {stage}">
<header><div><h2>{name} <small>{code}</small></h2><span class="badge">{stage}</span></div>
<div class="close">{r.close:.2f}<small>当日收盘 / 元</small></div></header>
<div class="levels">
<div><span>趋势参考 · MA20</span><b>{r.ma20_reference:.2f}</b></div>
<div><span>前20日高点 · 压力参考</span><b>{r.prior_high20:.2f}</b></div>
<div><span>前10日低点 · 支撑参考</span><b>{r.prior_low10:.2f}</b></div></div>
<p class="metrics">RPS120 {r.rps120:.1f} · RPS250 {r.rps250:.1f} ·
量比 {r.volume_ratio:.2f} · 距MA20 {r.bias20_pct:.2f}%</p>
<p><b>观察理由：</b>{reason}</p>
<p><b>接下来关注：</b>接近前20日高点时，核对量能和收盘位置；
回落时，记录量能是否收缩、收盘是否仍在MA20上方。
收盘落到MA20下方时标记“趋势转弱待复核”，并在下一交易日重新运行筛选。</p>
<details><summary>展开量价图</summary>{charts.get(str(r.ts_code), "")}</details>
</article>''')
    empty = '<p class="empty">本日没有满足观察池条件的标的。</p>' if not cards else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{version} 右侧观察清单 · {html.escape(date)}</title><style>
body{{margin:0;background:#eef2f5;color:#203247;font:15px/1.7 system-ui,"Microsoft YaHei"}}
main{{max-width:1080px;margin:auto;padding:28px 20px}}h1{{margin:0;font-size:28px}}
.intro,.stock{{background:white;border:1px solid #dbe3ea;border-radius:14px;padding:24px;
margin-bottom:18px}}.muted,small,.levels span{{color:#627386}}
input{{box-sizing:border-box;width:100%;padding:13px;border:1px solid #bbcbd8;
border-radius:8px;margin:12px 0 20px;font:inherit}}
header{{display:flex;justify-content:space-between;gap:15px}}h2{{margin:0;font-size:21px}}
h2 small{{font-size:13px;font-weight:400}}.badge{{color:#226e68;font-size:13px}}
.close{{font-size:26px;font-weight:650;text-align:right}}
.close small{{display:block;font-size:12px}}
.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}}
.levels div{{background:#f2f6fa;padding:13px;border-radius:8px}}
.levels span{{display:block;font-size:12px}}.levels b{{font-size:23px;font-weight:600}}
.metrics{{font-size:13px;color:#526b82}}summary{{cursor:pointer;color:#236d8e}}
svg{{max-width:100%;height:auto}}a{{color:#236d8e}}footer{{font-size:12px;color:#627386}}
@media(max-width:600px){{main{{padding:14px 10px}}.stock,.intro{{padding:16px}}
.levels{{grid-template-columns:1fr}}h2 small{{display:block}}}}
</style></head><body><main><section class="intro">
<h1>{version} 右侧观察清单 · {len(selected)}只</h1>
<p class="muted">{stages}</p>
<p>数据截至 <b>{html.escape(date)}</b> 收盘；以下是历史价格参考，并非实时行情。</p>
<p>仅展示趋势、整理和确认观察标的。价格列表示均线或历史高低点，不是买卖价。</p>
<a href="watchlist.csv">下载观察清单</a>
</section><input id="search" placeholder="搜索名称、代码或状态" aria-label="搜索观察标的">
<p id="count" class="muted">显示 {len(selected)} 只</p>{empty}{"".join(cards)}
<footer>价格单位：元。MA20包含当日；前20日高点、前10日低点均排除当日。
本地重建前复权尚未独立对账，股本为当前缓存；确认规则尚未完成历史绩效验证。
按RPS排序用于查阅，不是预期收益排名。<a href="audit.json">数据与规则说明</a></footer>
</main><script>document.querySelector('#search').addEventListener('input',e=>{{
const q=e.target.value.trim().toLowerCase();let count=0;
document.querySelectorAll('.stock').forEach(card=>{{
card.hidden=!card.dataset.search.toLowerCase().includes(q);if(!card.hidden)count++}});
document.querySelector('#count').textContent='显示 '+count+' 只';
}});</script></body></html>"""
