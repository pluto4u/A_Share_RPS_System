"""Render a dated review from completed, gated production outputs; no rule changes."""
import json
import html
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output'
def load(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))
a = load('integrated_decision_20261013_audit.json')
h = next(x for x in load('daily_dual_strategy_history.json') if x['data_asof']=='2026-10-13')
assert h['g0']['price_adjustment']=='verified_qfq'
assert h['ecology']['as_of_trade_date']=='2026-10-13'
assert len(h['strategy_a'])==2 and len(h['strategy_b'])==0
esc = html.escape
def table(heads, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
comments = {
 '603999.SH':'强度较好，但ATR约6%，波动较大；不追离均线过远的突破。',
 '601900.SH':'ATR约3.8%，适合优先观察回踩；成交额/ADV20约2.19倍但当日涨幅有限，须看承接。',
 '600551.SH':'距MA20较近，但距60日高仍约14%，上方压力不轻；等待转强。',
 '002292.SZ':'RPS120较强、接近前高，但属于第二行业分组，按现规则仅观察。',
 '600640.SH':'量能低于ADV20，尚需验证回流；第二行业分组，仅观察。',
 '600977.SH':'已超过此前60日高，但距MA20约9.3%，不追扩张；第二行业分组。',
}
rows=[]
for c in a['candidates']:
    ma=c['close']/(1+c['vs_ma20_pct']/100)
    rows.append([c['ts_code']+' '+c['name'],f"{c['close']:.2f}",f"{c['RPS_120']:.1f}",f"{ma:.2f}",f"{c['vs_ma20_pct']:.1f}%",'第一组资格池' if c['executable'] else '第二组观察',comments[c['ts_code']]])
inds=[[x['industry'],x['name'],x['members'],f"{x['resonance']:.1f}",f"{x['breadth_ma20_pct']:.1f}%",f"{x['amount_vs_adv20']:.2f}倍"] for x in a['industries'][:5]]
stamp=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>10月13日右侧选股与次日推演</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f8;color:#203047;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1250px;margin:auto;padding:24px}}header{{background:#14283c;color:white;padding:28px;border-radius:12px}}h1{{font-size:27px;margin:0}}h2{{font-size:21px}}section{{background:white;padding:24px;margin-top:18px;border-radius:10px}}.metrics{{display:flex;flex-wrap:wrap;gap:28px;margin-top:20px}}.metrics b{{display:block;font-size:24px}}small{{color:#64748b}}header small{{color:#d3dee8}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #dde3eb;vertical-align:top}}th{{white-space:nowrap}}.scroll{{overflow-x:auto}}.risk{{background:#fff4df;padding:14px}}a{{color:#24629a}}@media(max-width:600px){{main{{padding:10px}}section,header{{padding:16px}}}}@media print{{section{{break-inside:avoid}}}}</style><main>
<header><small>数据截至2026-10-13收盘 · 沪深A股，不含北交所 · 生成 {stamp}</small><h1>右侧选股：可以研究，不宜把修复当主升</h1><p>G0/G1已恢复通过。刚启动2只，回踩0只；行业趋势资格池3只。没有生成买单，不代表没有候选。宏观市场判断：风险收缩后的修复（推断）；量化状态：修复；盘面过程：分歧后修复。置信度：中。</p><div class="metrics"><div><b>2 / 0</b>策略A / B</div><div><b>34.2%</b>样本站上MA20</div><div><b>42.4</b>ST_cycle</div><div><b>35%</b>模型风险上限，非目标仓位</div></div><p>唯一主计划：暂不生成买单，先观察传媒第一分组承接；缺当前账户、风险预算及具体入场/失效计划，G5未通过。分钟和竞价数据未覆盖。价格指标采用同花顺本地v_daily_qfq。</p></header>
<section><h2>一、选股名单：不同口径，不混为一谈</h2><p>原版ZZ1234合并公式命中36只；V2为启动2只＋趋势监测3只；每日策略A保留2只、B没有命中。行业RPS筛选另得6只，其中第一行业分组3只。各名单并非同一套公式，不能把6只说成A/B命中。</p>
{table(['代码 / 名称','收盘','RPS120 / RPS50','判断'],[['002674 兴业科技','21.71','98.1 / 17.6','放量启动；距MA20约9.5%，中期强但近50日弱，非当前第一行业，不追涨。'],['300063 天龙集团','8.97','21.7 / 80.0','早期转强；距MA20约2.8%，但RPS120未达80，只作修复观察。']])}
<p>监测标签：601127赛力斯、301002崧盛股份、300984金沃股份；不是新的启动买单。B的实现尚需对“首次回踩”去重语义进一步审计，本次0命中不受此问题影响。</p></section>
<section><h2>二、较值得跟踪的行业趋势池</h2><p>优先观察顺序是人工风险偏好排序，不是收益概率排名：南方传媒（波动较低）→读者传媒（强度较好、波动更高）→时代出版（回踩结构）。MA20只是观察锚，不是到价自动买入或保证成交的止损价。</p>{table(['代码 / 名称','收盘元','RPS120','MA20元','距MA20','资格','结构判断'],rows)}
<p class="risk">三只第一组股票仍不是可直接下单的标的。只有行业维持强势、回踩不破关键结构并再转强，且按实际入场价测得至少2R空间，才考虑制定新计划。当前未确定止损/目标，不声称已满足2R。T+1当天新买不能卖；跳空或跌停可能超出计划损失。</p></section>
<section><h2>三、市场证据与行业共振</h2><p>本地价格样本5210只：上涨53.6%，日收益中位数+0.21%，站上MA20为34.2%、MA60为43.7%。这更接近普遍下跌后的修复，而非多数股票进入上升趋势。</p><p>本地涨停生态：涨停70、跌停10、炸板22，炸板率23.9%，最高连板3，昨日涨停股今日平均收益+1.72%。ST_cycle较昨日上升6点至42.4，仍是修复；阈值未完成样本外验证。</p>{table(['分组代码','显示名称','成分数','共振分','站上MA20','成交额/ADV20'],inds)}
<p>前两组同被名称表显示为“传媒娱乐”，但代码T060101与T060102不同；本次严格按代码区分，不把同名当成同组。行业表文件更新于10月11日，并非当日更新。银行可作防守强弱参照；不把排名第四改成执行第一主线。</p>
<p>公开收盘资料：沪指3800.86（+0.10%）、深成指−0.25%、创业板−0.18%，沪深成交额约16759亿元。公开盘面报道提到算力硬件修复，但日内热点不等于本模型5/20日共振第一行业。<a href="https://finance.sina.com.cn/jjxw/2026-10-13/doc-inivamwm8772763.shtml">收盘报道</a> · <a href="https://finance.eastmoney.com/a/202610133894185078.html">成交额交叉参考</a>。</p><p>口径差异：生态成交额16380亿元、趋势样本16685亿元、公开沪深口径16759亿元，覆盖与样本过滤不同；不拼接计算放量率。未使用估算资金净流入。</p></section>
<section><h2>四、下一交易日情景推演（预期10月14日）</h2>{table(['情景','确认条件（研究阈值，未校准）','应对'],[['修复延续','传媒第一组多数守住结构；市场站上MA20比例从34.2%继续回升；涨停反馈维持正向','观察南方传媒/读者传媒回踩后再转强。不追高开，不因上涨扩大风险上限。'],['冲高回落','指数回升但行业宽度走弱，或核心放量不涨、回落失守支撑','空仓继续等；已有仓按原止损和T+1可卖量减险，不向亏损仓加仓。'],['修复失败','市场跌破10月13日低点，同时跌停增加、昨日涨停反馈转负','取消新开仓计划；可卖旧仓优先控制结构破坏风险。']])}<p>这是条件分支，不分配无回测依据的概率。若10月14日休市，顺延到实际下一交易日。尚未核验完整隔夜美股收盘，不把未结束行情写为事实。</p></section>
<section><h2>五、门控与审计</h2><p>G0：16/16原始锚通过，前复权公式/量额/最新归一零错配；只证明本地运营一致性，不是独立外部复权证明。G1：10月13日、覆盖率100%。G2–G4通过；G5因账户与唯一计划不完整未通过。当前账户未更新，未沿用9月的150万元假设。</p><p>同步返回SKIP是远端版本已最新，不是使用--skip-sync；库校验通过，7个流程步骤完成。风险上限35%仅作参考，不是推荐投入35%。空仓等待的代价是可能错过V形上涨；已有仓不因本次未生成买单而机械全卖，应按原计划管理。</p><p>数据：data/hithink/market.duckdb:v_daily_qfq；通达信C:/new_tdx/vipdoc原始日K；RPS刷新2026-10-13。<a href="a_share_analysis_daily_dual_strategy_journal.html">唯一双策略日报</a> · <a href="integrated_decision_20261013_audit.json">六门审计</a> · <a href="siruipu_tdx_exact_20261013.csv">原版36只</a>。非投资建议，不承诺盈利。</p></section></main></html>'''
path=OUT/'a_share_analysis_20261013_right_side_review.html'
path.write_text(body,encoding='utf-8')
assert not any(x in body for x in ['NaN','Infinity','{{PLACEHOLDER}}'])
assert len(a['candidates'])==6
print(path)
