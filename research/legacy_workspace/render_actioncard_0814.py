#!/usr/bin/env python3
"""用行动卡布局生成 2026-08-14 盘后复盘 HTML。思考结论来自已跑通的门控，不在此重算门控。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")
STAMP = datetime.now().strftime("%H%M")
OUT = ROOT / "output" / f"a_share_analysis_20260814_{STAMP}_actioncard.html"

AUDIT = json.loads((ROOT / "output/integrated_decision_20260814_audit.json").read_text(encoding="utf-8"))
SLICE = json.loads((ROOT / "output/review_0814_slice.json").read_text(encoding="utf-8"))
ECO = json.loads((ROOT / "output/limit_ecology_latest.json").read_text(encoding="utf-8"))

HT_CLOSE, HT_VS = 20.30, 5.36
HT_MA20 = round(HT_CLOSE / (1 + HT_VS / 100), 2)
HT_INV = round(HT_MA20 * 0.985, 2)
HT_EXT12 = round(HT_MA20 * 1.12, 2)


def signed(v: float, d: int = 2) -> str:
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    arrow = "↑" if v > 0 else ("↓" if v < 0 else "→")
    return f'<span class="{cls}">{v:+.{d}f} {arrow}</span>'


def row_stock(s: dict, role: str, note: str) -> str:
    rps = s.get("rps120")
    return (
        f"<tr><td>{s['ts_code']}</td><td>{s['name']}</td><td>{role}</td>"
        f"<td>{s['close']:.2f}</td><td>{signed(s['ret1_pct'])}</td>"
        f"<td>{signed(s['open_ret_pct'])}</td><td>{signed(s['close_vs_high_pct'])}</td>"
        f"<td>{signed(s['vs_ma20_pct'])}</td>"
        f"<td>{'是' if s['above_ma60'] else '否'}</td>"
        f"<td>{rps if rps is not None else '未取得'}</td>"
        f"<td>{s['amount_yi']:.2f}</td>"
        f"<td>{s['amount_ratio'] if s['amount_ratio'] is not None else '未取得'}</td>"
        f"<td>{note}</td></tr>"
    )


lead = {s["ts_code"]: s for s in SLICE["lead_stocks"]}
near = {s["ts_code"]: s for s in SLICE["near_executable"]}
qf = near["301603.SZ"]
cx = lead["300083.SZ"]
hd = lead["002248.SZ"]
rf = lead["002520.SZ"]
kd = lead["688305.SH"]
ht = lead["601882.SH"]
yj = lead["002943.SZ"]
yh = lead["002903.SZ"]
med = {s["ts_code"]: s for s in SLICE["second_stocks"]}
kl = med["002821.SZ"]
yq = med["301047.SZ"]
bp = med["301080.SZ"]
gl = med["603087.SH"]
idx = {x["name"]: x for x in SLICE["indices"] if x.get("available")}
amt0, amt1 = SLICE["prev_amount_yi"], SLICE["market_amount_yi"]
amt_chg = (amt1 / amt0 - 1) * 100

ind_rows = []
for i, r in enumerate(AUDIT["industries"][:8], 1):
    tag = "第一主线" if i == 1 else ("次主线" if i == 2 else ("单股驱动" if r["single_stock_driven"] else "—"))
    ind_rows.append(
        f"<tr><td>{i}</td><td>{r['name']}</td><td>{r['members']}</td>"
        f"<td>{signed(r['ret5_pct'])}</td><td>{signed(r['ret20_pct'])}</td>"
        f"<td>{r['breadth_ma20_pct']:.0f}%</td><td>{r['median_rps120']:.0f}</td>"
        f"<td>{r['amount_vs_adv20']:.2f}×</td><td>{r['amount_yi']:.0f}</td>"
        f"<td>{r['top1_amount_share_pct']:.0f}%</td><td>{r['resonance']:.0f}</td>"
        f"<td>{tag}</td></tr>"
    )

idx_rows = []
for name in ("上证指数", "沪深300", "创业板指", "科创50", "国证2000"):
    x = idx[name]
    idx_rows.append(
        f"<tr><td>{name}</td><td>{x['close']:.2f}</td>"
        f"<td>{signed(x['open_ret_pct'])}</td><td>{signed(x['ret1_pct'])}</td>"
        f"<td>{signed(x['close_vs_high_pct'])}</td>"
        f"<td>{x['amount_yi']:.0f}</td></tr>"
    )

audit_json = json.dumps({
    "layout": "action-card",
    "thinking_chain": "gated_pipeline_v1",
    "trade_date": "2026-08-14",
    "generated_at": NOW,
    "gates": AUDIT["gates"],
    "decision_from_gates": AUDIT["decision"]["action"],
    "unique_plan_status": "观察，不可执行",
    "watch": "601882.SH 海天精工",
}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股0814盘后复盘 · 行动卡</title>
<style>
:root{{--bg:#eef2f6;--ink:#16202f;--muted:#5f6b80;--line:#d8e0e9;--up:#c62828;--down:#16794b;--amber:#9a5b00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1440px;margin:auto;padding:18px}}
.hero{{background:linear-gradient(135deg,#06182a,#11405f);color:#fff;border-radius:18px;padding:26px}}
.eyebrow{{color:#9ecbeb;font-weight:700;letter-spacing:.08em;font-size:12px}}
h1{{font-size:26px;margin:8px 0 14px}}h2{{margin:0 0 12px;font-size:20px}}
.lede{{font-size:16px;line-height:1.7;color:#e8f1f8;margin:0 0 12px}}
.cmd{{background:#ffffff14;border:1px solid #ffffff2b;border-radius:12px;padding:14px;color:#fff}}
.cmd b{{color:#ffe082}}
.metaline{{color:#c3d7e7;margin-top:14px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:14px;margin-top:14px}}
.card{{grid-column:span 12;background:#fff;border:1px solid var(--line);border-radius:15px;padding:18px}}
.half{{grid-column:span 6}}
.kv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.kv div{{background:#f5f8fb;border:1px solid var(--line);border-radius:10px;padding:11px}}
.kv small{{color:var(--muted);display:block}}.kv b{{font-size:16px}}
.gates{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:10px}}
.gate{{border:1px solid var(--line);border-top:4px solid #16794b;border-radius:10px;padding:10px;background:#fbfcfd;font-size:12px}}
.gate.stop{{border-top-color:#c54f3f;background:#fff8f6}}
.gid{{font-weight:800;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
thead th{{color:var(--muted);background:#f6f8fa}}
.tablebox{{overflow:auto}}
.up{{color:var(--up);font-weight:600}}.down{{color:var(--down);font-weight:600}}.flat{{color:var(--muted)}}
.warn{{border-left:5px solid #d18516;background:#fff8e9;padding:12px;border-radius:8px}}
.risk{{border-left:5px solid #c54f3f;background:#fff6f4;padding:12px;border-radius:8px}}
.ok{{border-left:5px solid #16794b;background:#f3faf6;padding:12px;border-radius:8px}}
.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.three section{{background:#f7f9fb;border:1px solid var(--line);border-radius:10px;padding:13px}}
.action td{{font-size:13px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:999px;background:#e9f1f8;font-size:12px;font-weight:700}}
.tag.risktag{{background:#fff3e0;color:var(--amber);border:1px solid #e8c48f}}
footer{{color:var(--muted);padding:16px 4px;font-size:13px}}
@media(max-width:980px){{.half{{grid-column:span 12}}.gates,.three{{grid-template-columns:1fr}}}}
@media print{{body{{background:#fff}}}}
</style></head><body><main class="wrap">

<section class="hero">
<div class="eyebrow">行动卡布局 · 思考链仍为六道门控 · 不得改写 G5</div>
<h1>2026-08-14 盘后复盘与次日计划</h1>
<p class="lede">通用机械是当天全市场共振分最高的行业：22 只成分全部站上 MA20，量能 1.55 倍 ADV20，且不是单股驱动。
但它还不是可执行主线——中位 RPS120 只有 59.9，4 只已经达到 80 的股票距 MA20 全部超过 12%。
指数几乎平收，两市成交额较 8 月 13 日少 16%，昨日涨停股高开低走。宏观状态是高位轮动中的弱修复，不是趋势扩张。</p>
<div class="cmd"><b>账户级指令：</b>空仓不追。已有通用机械中军且仍站 MA20 的可以留；医药即使 RPS 更高也只算次主线，趋势单减半、情绪单按修复状态降到账户上限 35% 以内。
次日唯一观察标的是海天精工，在 RPS120 升到 80 且回踩不破 MA20 之前，<b>不产生可执行买单</b>。</div>
<div class="metaline">分析时点 {NOW}（北京时间）· 数据截至 2026-08-14 收盘 ·
价格复权 verified_qfq（抽样 14/14）· ST_cycle 非正式口径 · 沪深 A 股不含北交所 ·
下一交易日按日历推断为 8 月 17 日（8 月 15-16 为周末），此为推断不是事实。</div>
</section>

<section class="grid">
<article class="card"><h2>门控结论（思考链，不被版式改写）</h2>
<div class="gates">
<article class="gate"><div class="gid">G0 复权</div><b>通过</b><p>verified_qfq · 14/14</p></article>
<article class="gate"><div class="gid">G1 风险</div><b>通过</b><p>修复 · ST_cycle 46 · 上限 35%</p></article>
<article class="gate"><div class="gid">G2 RPS</div><b>通过</b><p>9 只候选，全部在次主线</p></article>
<article class="gate"><div class="gid">G3 主线</div><b>通过</b><p>通用机械 共振 93 · 宽度 100%</p></article>
<article class="gate"><div class="gid">G4 执行</div><b>通过</b><p>流动性与乖离检查完成</p></article>
<article class="gate stop"><div class="gid">G5 账户</div><b>未通过</b><p>第一主线无同时满足 RPS120≥80 且乖离≤12% 的标的</p></article>
</div>
<p class="warn" style="margin-top:12px"><b>风险 · 行动卡不能把观察写成买入。</b>
下面「等待买」是观察清单。G5 未通过时，正确动作仍是研究观察 / 零交易。</p>
</article>

<article class="card"><h2>数据口径与拒绝使用清单</h2>
<div class="kv">
<div><small>交易日</small><b>2026-08-14 已开市</b></div>
<div><small>价格</small><b>本地日线 · 前复权已验证</b></div>
<div><small>行业</small><b>tdxhy.cfg 当日映射</b></div>
<div><small>涨停生态</small><b>本地重建 · 对齐东财 63/63</b></div>
<div><small>ST_cycle</small><b>46 · 非正式 · 仅作上限</b></div>
<div><small>双时点</small><b>日线代理，不是分时</b></div>
</div>
<p class="risk" style="margin-top:12px"><b>拒绝使用（未取得，不用估算值填上）：</b>
东财/同花顺估算资金流与超大单方向；13:17到14:30 分钟差值（腾讯分钟仅当日，本地分钟停更至 2024-06）；
分时均价；北向盘中净额；全市场实时涨跌家数；题材/概念归属；流通市值归一化 CER；北交所。</p>
<p>日线代理的含义：用「8 月 13 日收盘到 8 月 14 日收盘」和「8 月 14 日开盘到收盘」描述路径。
这能判断高开低走或收盘靠近高点，不能判断午后是否回流。科创50 开盘 +1.23%、收盘 0.00%、距高 -1.36%，只能说明冲高回落，不能还原 14:00 之后发生了什么。</p>
</article>

<article class="card half"><h2>市场宽度与情绪</h2>
<div class="kv">
<div><small>中位涨跌</small><b>{signed(AUDIT['market']['median_ret1_pct'])}</b></div>
<div><small>上涨家数占比</small><b>44.3%</b></div>
<div><small>站上 MA20 / MA60</small><b>81.3% / 39.3%</b></div>
<div><small>两市成交额</small><b>{amt1:.0f} 亿</b></div>
<div><small>较上一交易日</small><b>{signed(amt_chg, 1)}</b></div>
<div><small>涨停 / 跌停</small><b>63 / 14</b></div>
<div><small>炸板率</small><b>26%</b></div>
<div><small>一/二/三板</small><b>49 / 4 / 3</b></div>
<div><small>昨涨停今收 / 今开</small><b>{signed(ECO['raw']['YLR_close'])} / {signed(ECO['raw']['YLR_open'])}</b></div>
</div>
<p class="warn" style="margin-top:12px">昨日涨停股开盘平均 +2.18%，收盘 -0.09%：高开低走。
跌停从 6 只增到 14 只。这是分歧，不是加速。</p></article>

<article class="card half"><h2>指数路径（日线代理）</h2>
<div class="tablebox"><table>
<thead><tr><th>指数</th><th>收盘</th><th>开盘相对昨收</th><th>收盘相对昨收</th><th>收盘相对当日高</th><th>成交额亿</th></tr></thead>
<tbody>{''.join(idx_rows)}</tbody></table></div>
<p>创业板收在当日高点附近；科创50 是明显的冲高回落。权重指数几乎平盘。
量能收缩 + 小盘强于权重 + 科创回吐，更接近高位轮动，而不是趋势扩张。</p>
</article>

<article class="card"><h2>行业共振（只有行业，没有题材）</h2>
<div class="tablebox"><table>
<thead><tr><th>#</th><th>行业</th><th>成分</th><th>5日中位</th><th>20日中位</th><th>MA20宽度</th>
<th>RPS120中位</th><th>量能/ADV20</th><th>成交额亿</th><th>最大个股权重</th><th>共振分</th><th>标记</th></tr></thead>
<tbody>{''.join(ind_rows)}</tbody></table></div>
<p class="ok"><b>路径命名：宽度先于强度。</b>
通用机械已经走完「全员站上 MA20、量能放大、非单股」这一段，停在「中位强度不够、高 RPS 成员乖离过大」。
下一步应该是中军回踩把乖离消化，而不是后排补涨当新起点。</p>
</article>

<article class="card"><h2>第一主线内部结构 · 通用机械</h2>
<p>22 只成分：站上 MA20 的 22 只，站上 MA60 的 11 只，RPS120≥80 的 4 只。当日中位涨跌 +0.27%，上涨占比 54.5%。同一把尺子：</p>
<div class="tablebox"><table>
<thead><tr><th>代码</th><th>名称</th><th>角色</th><th>收盘</th><th>当日</th><th>开盘</th><th>距高点</th>
<th>距MA20</th><th>MA60</th><th>RPS120</th><th>成交额亿</th><th>量比</th><th>备注</th></tr></thead>
<tbody>
{row_stock(cx, "龙头/中军", "量能最大且量比低于1，收绿，乖离超限，不追")}
{row_stock(hd, "弹性/脉冲", "量比 12.4，RPS120 仅 57，不是中军")}
{row_stock(rf, "弹性", "量比 4.7，涨 4%，乖离 17%，后排补涨")}
{row_stock(kd, "中军偏高位", "RPS 达标但乖离 14%，收 -2.3%")}
{row_stock(qf, "高RPS弹性", "RPS120=98，成交不足 2 亿，乖离 14%")}
{row_stock(ht, "中军候选", "唯一乖离合格且双均都在，RPS120=75 未过 80")}
{row_stock(yh, "结构未完成", "站上 MA20 但仍在 MA60 下，距 60 日高 -47%")}
{row_stock(yj, "后排", "RPS50=0.2，不参与比较")}
</tbody></table></div>
<p class="warn"><b>风险 · 华东数控不是主线确认。</b>量比 12 倍、20 日涨幅与 RPS 中期不匹配，是脉冲。
把它算进「中军回流」会把单股行情误读成板块扩散。</p>
</article>

<article class="card"><h2>次级方向：现在不做的分类理由</h2>
<div class="tablebox"><table>
<thead><tr><th>方向</th><th>事实</th><th>分类理由</th><th>等待条件</th><th>现在</th></tr></thead>
<tbody>
<tr><td>医药（次主线）</td>
<td>共振分 88，RPS120 中位 72，成交额 345 亿；凯莱英收 {signed(kl['ret1_pct'])}，乖离 +4.9%；义翘神州 +8.5%，乖离 +50%</td>
<td><b>不在第一主线</b>。另有一批成员属于位置过高</td>
<td>只有通用机械宽度跌破 50% 且医药成为新的第一主线，才切换观察；不得与主计划同时下单</td>
<td>已有仓按趋势单减半；新仓不开</td></tr>
<tr><td>义翘神州 / 百普赛斯</td>
<td>距 MA20 约 +50%，量比 8.2 / 5.3</td>
<td><b>位置过高</b></td>
<td>乖离回到 12% 以内再谈执行质量</td>
<td>禁止追涨</td></tr>
<tr><td>甘李药业</td>
<td>收 {signed(gl['ret1_pct'])}，上影占比 0.89，距高 -6.0%</td>
<td><b>结构分裂</b>（冲高后长上影）</td>
<td>重新站稳当日高点一侧，且乖离仍不超过 12%</td>
<td>不作为医药代表</td></tr>
<tr><td>商业连锁</td>
<td>5 日中位全市场最高 +4.2%，但 20 日仅 +7.8%，MA20 宽度 86%</td>
<td><b>确认不足</b>：短周期脉冲，中期强度一般</td>
<td>20 日收益与宽度同时进入前 5</td>
<td>列入下一日观察，不开仓</td></tr>
<tr><td>综合类 / 建筑</td>
<td>最大个股权重 36%</td>
<td><b>单股驱动</b></td>
<td>权重降到 35% 以下且宽度仍在</td>
<td>不得当作板块主线</td></tr>
</tbody></table></div>
</article>

<article class="card half"><h2>唯一计划：观察，不可执行</h2>
<p><span class="tag risktag">风险 · 不是条件买入</span> 标的 601882.SH 海天精工 · 趋势账本 · 确认买点（尚未触发）</p>
<table>
<tr><th>为什么是它</th><td>第一主线里唯一同时满足：双均之上、乖离 5.4%≤12%、量比 2.0 不是脉冲。RPS120=75.4，差 4.6 分到门槛。</td></tr>
<tr><th>入场触发</th><td>下一交易日回踩 {HT_MA20:.2f}–19.60（MA20 附近）后重新站上 19.80，且收盘仍在 MA20 上方；<b>同时</b>当日 RPS120≥80。两条件缺一不可。</td></tr>
<tr><th>首仓 / 上限</th><td>示例账户权益 100 万、单笔风险 0.50%：有效止损取 max(计划距离, 涨跌幅 10%×0.6, 1×ATR 3.53%) = <b>6.0%</b>（跌停不可成交下限生效）。约 4200 股、仓位约 8%。当日不加仓。未提供真实账户时这只是示例。</td></tr>
<tr><th>取消</th><td>竞价低开超过 -2%；或回踩直接跌破 {HT_INV:.2f}；或通用机械当日上涨宽度跌破 50%。</td></tr>
<tr><th>硬失效</th><td>{HT_INV:.2f}（MA20×0.985）。T+1：次日开盘低于该价减半，收盘仍在下方清仓。</td></tr>
<tr><th>追高禁区</th><td>21.26 以上（8 月 14 日高点）直接拉升禁止买入；距 MA20 超过 12%（约 {HT_EXT12:.2f}）同样禁止。</td></tr>
<tr><th>机会成本</th><td>若创世纪次日高开 3%，空仓错过的是龙头隔夜，现价距 MA20 仍有 15%，止损不清晰，不值得用 35% 上限里的额度去买一个过热标的。用 4.6 分 RPS 缺口换不追价，是显式承担的成本。</td></tr>
</table>
<p class="ok">创世纪、乔锋智能、科德数控今天看起来更强，但它们正是乖离过滤器要挡掉的对象。
过滤器在这里的工作方式：主线对了，买点错了，仍然零交易。</p>
</article>

<article class="card half"><h2>隔夜持仓处理（账户未提供，三分叉）</h2>
<p class="warn">用户未提供持仓、成本、可卖数量。下列为机械规则，不是对你账户的判断。</p>
<table>
<tr><th>空仓</th><td>不追。只有海天精工触发上述两条才从观察升级；否则继续空仓。隔夜总仓 0%。</td></tr>
<tr><th>已有通用机械中军</th><td>创世纪若下一交易日收盘仍在 MA20 之上可留，收盘跌破减半；华东数控视为弹性脉冲，减到观察仓。板块合计不超过账户 15%，高度相关机床股视为一笔风险。</td></tr>
<tr><th>已有医药</th><td>不是第一主线。趋势单减半；凯莱英、百济神州这类乖离仍 ≤12% 的可以留一半；义翘、百普赛斯、百花医药（距 MA20 +50% 一带）按位置过高处理，反弹减、不补仓。</td></tr>
<tr><th>重仓（超过 50%）</th><td>收到 35% 以内（修复档上限，ST_cycle 非正式，不得授权更高）。优先减 ATR 高、乖离大、不在第一主线的。</td></tr>
</table>
<p>8 月 14 日是周五。T+1 加周末，情绪单不隔夜；趋势单也按上限的下限来，不把 35% 用满。</p>
</article>

<article class="card"><h2>此刻行动卡</h2>
<div class="tablebox"><table class="action">
<thead><tr><th>立即买</th><th>等待买</th><th>持有</th><th>减仓</th><th>清仓</th><th>禁止追涨</th></tr></thead>
<tbody><tr>
<td><b>无</b></td>
<td>海天精工：回踩 MA20 {HT_MA20:.2f}–19.60 后重回 19.80，且 RPS120≥80。创世纪仅当乖离回到 12% 以内才进入观察，不与海天同时作为计划。</td>
<td>通用机械中军若仍站 MA20；医药中军（凯莱英类）只留半仓</td>
<td>医药仓减半；华东数控、日发精机等量比大于 4 的弹性仓；修复状态下账户降至 35% 内</td>
<td>收盘失守 MA20 的弹性股；距 MA20 仍大于 30% 且放量滞涨的医药后排</td>
<td>创世纪 / 乔锋智能 / 科德数控（乖离&gt;12%）；义翘神州 / 百普赛斯（乖离约 50%）；华东数控（量比 12 倍）；任何涨停附近的后排</td>
</tr></tbody></table></div>
</article>

<article class="card"><h2>事实、推断、交易假设</h2>
<div class="three">
<section><b>事实</b>
<p>本地日线最新日 2026-08-14；复权抽样 14/14 通过；涨停 63 只与东财池一致；
两市成交额 2.09 万亿，上一交易日 2.51 万亿；上证 +0.01%，创业板 +1.12%，科创50 开盘 +1.23% 收盘 0.00%；
通用机械 22/22 站上 MA20，中位 RPS120=59.9，RPS120≥80 的 4 只乖离均超过 12%；海天精工收 20.30，距 MA20 +5.36%，RPS120=75.4。</p></section>
<section><b>推断</b>
<p>用共振分把通用机械定为第一主线、医药定为次主线，是模型输出不是市场命名。
「宽度先于强度」是对 100% MA20 宽度与 59.9 中位 RPS 同时存在的解释。
宏观状态选「高位轮动中的弱修复」，依据是量能收缩、YLR 高开低走、科创冲高回落、跌停增加；若下一交易日量能回到 2.5 万亿以上且跌停回落，该推断失效。</p></section>
<section><b>交易假设</b>
<p>假设：第一主线的可执行买点出现在中军回踩 MA20，而不是龙头继续拉乖离。
因此空仓宁可错过创世纪高开，也不在 15% 乖离处开趋势单。
反向风险：机床设备因事件驱动直接缺口越过 12% 乖离上限，观察计划永远等不到回踩。</p></section>
</div>
<p class="risk" style="margin-top:12px"><b>风险四项：</b>
最大风险是把次主线医药的高 RPS 当成可执行信号，或把华东数控的脉冲量能当成主线扩散；
失效条件是通用机械 MA20 宽度跌破 50%，或海天精工收盘跌破 {HT_INV:.2f}；
对应动作是取消观察、空仓继续空、有仓按趋势单减半；
反向风险是主线不回踩直接走主升，账户仓位为 0——该机会成本已在追高禁区里显式承担。
本页为研究与风险管理材料，不构成收益保证。</p>
</article>
</section>

<footer>自包含单文件 · 无外链 CSS/JS/字体/CDN · 思考链 gated_pipeline_v1 · 输出布局 action-card ·
审计摘要已内嵌。<script id="audit-snapshot" type="application/json">{audit_json}</script></footer>
</main></body></html>
"""

OUT.write_text(html, encoding="utf-8")
print(f"写出 {OUT}")
print(f"chars={len(html):,}  HT_MA20={HT_MA20}  HT_INV={HT_INV}  HT_EXT12={HT_EXT12}")
for bad in ("NaN", "Infinity", "None", "{{PLACEHOLDER}}", "TODO", "http://", "https://", "<script src"):
    print(f"  {bad:20s} {html.count(bad)}")
print("  立即买无:", html.count("<b>无</b>"))
print("  不可执行:", html.count("不可执行"))
