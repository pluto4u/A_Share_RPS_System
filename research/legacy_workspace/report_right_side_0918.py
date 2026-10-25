"""Build a date-specific audited report without changing strategy definitions."""
import html
import json
from datetime import datetime
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'
TAG='20260918'
def load(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
audit=load(f'right_side_{TAG}_audit.json')
integrated=load(f'integrated_decision_{TAG}_audit.json')
eco=load('limit_ecology_latest.json')
g0=load('qfq_verification.json')
meta=load(f'siruipu_right_side_v2_{TAG}.json')
hist=next(x for x in load('daily_dual_strategy_history.json') if x['data_asof']=='2026-09-18')
assert all(s['ok'] for s in hist['steps'])
assert integrated['market']['trade_date']==eco['as_of_trade_date']=='2026-09-18'
a=pd.read_csv(OUT/f'right_side_{TAG}_A_enriched.csv',dtype={'ts_code':str})
b=pd.read_csv(OUT/f'right_side_{TAG}_B_enriched.csv',dtype={'ts_code':str})
from screen_siruipu_right_side import load_industry_map
imap=load_industry_map(Path('C:/new_tdx/T0002/hq_cache/tdxhy.cfg'))
rankmap={x['industry']:i+1 for i,x in enumerate(integrated['industries'])}
a=a.merge(imap[['ts_code','industry']],on='ts_code',how='left',validate='one_to_one')
a['industry_rank']=a.industry.map(rankmap)
a['阶段']=a.role.map({'DISCOVERY':'预警转强','UPGRADE':'放量启动'})
a['新信号']=a.a_new_vs_previous_day.map({True:'较前日新增',False:'连续信号'})
a['数值条件']=a.g2_full.map({True:'通过',False:'未通过'})
a['行业地位']=a.industry_rank.map(lambda x:f'第{int(x)}组 · 仅观察' if pd.notna(x) else '非前列组 · 仅观察')
a['ma60_gap_pct']=(a.close/a.ma60-1)*100
a=a.sort_values(['g2_full','RPS_120','adv20_yi'],ascending=[False,False,False])
def table(frame):return '<div class="scroll">'+frame.to_html(index=False,border=0,float_format=lambda x:f'{x:.2f}',na_rep='未取得',escape=True)+'</div>'
def select_table(frame,keys,labels):
    z=frame[keys].copy();z.columns=labels;return table(z)
basekeys=['ts_code','name','阶段','新信号','industry_name','行业地位','close','RPS_50','RPS_120','RPS_250','vs_ma20_pct','ma60_gap_pct','vol_ratio','数值条件']
baselabels=['代码','名称','形态','持续性','行业','行业地位','收盘/元','RPS50','RPS120','RPS250','距MA20%','距MA60%','量比','G2数值']
num=a[a.g2_full]
first_b=sum(x['first_qualifying_signal'] for x in audit['B_first_audit'])
near_keys=['002138.SZ','001314.SZ','688037.SH','688072.SH','688105.SH']
focus=a.set_index('ts_code').loc[near_keys].reset_index()
focus['评估']=['20日线向上且突破前20日高；乖离8.89%，等回踩','20日线向上、刚站上60日线；仍接近前20日高压力','已站上60日线，但20日线仍下降；不当作成熟趋势','20日线向上、突破前20日高，但属于连续信号且乖离9.74%','RPS50/120均超90，但行业不在前列、20日线仍下降']
focushtml=select_table(focus,['ts_code','name','close','RPS_120','ma20','ma60','prior_high20','atr20','评估'],['代码','名称','收盘','RPS120','MA20','MA60','此前20日高','ATR20','观察结论'])
inds=pd.DataFrame(integrated['industries']).head(8)
indhtml=select_table(inds,['industry','name','members','breadth_ma20_pct','ret5_pct','median_rps120','amount_vs_adv20','top1_amount_share_pct','single_stock_driven'],['精确分组代码','行业简称','有效成分','MA20宽度%','5日中位收益%','RPS120中位','成交额/基准','最大股占额%','单股驱动'])
gates=[]
for x in integrated['gates']:
    d=x['detail'].replace('第一主线可执行 0 只','第一行业归属 0 只').replace('已保留候选与交易资格','已保留研究候选')
    gates.append({'门控':x['id']+' '+x['name'],'结果':'通过' if x['pass'] else '未通过','解释':d})
market=integrated['market'];raw=eco['raw']
stage_counts=a['阶段'].value_counts().to_dict()
top_industry=a.groupby('industry_name').size().sort_values(ascending=False).head(4).to_dict()
generated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
body=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>0918右侧趋势筛选与评估</title><style>
:root{{--navy:#142338;--muted:#617087;--teal:#087d76;--amber:#a65b08;--line:#e1e7ef}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fa;color:#1d2939;font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif}}main{{max-width:1440px;margin:auto;padding:24px}}header{{padding:28px;background:var(--navy);color:white;border-radius:20px}}h1{{font-size:30px;margin:5px 0 12px}}h2{{font-size:21px;margin:0 0 14px}}h3{{font-size:17px}}p{{margin:10px 0}}.eyebrow{{color:#a1dcd6;letter-spacing:2px}}.muted{{color:var(--muted)}}header .muted{{color:#c6d1e2}}nav{{display:flex;gap:18px;flex-wrap:wrap;margin:18px 0}}a{{color:#087d76}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}}section,.card{{padding:22px;background:white;border:1px solid var(--line);border-radius:16px;margin:16px 0;break-inside:avoid}}.card{{grid-column:span 3;margin:0}}.num{{font-size:32px;color:var(--teal);font-weight:700}}.alert{{border-left:5px solid #e4a84c;background:#fff7e9;padding:13px 17px;color:#6f430d}}.scroll{{overflow:auto;width:100%}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{background:#eef3f8}}td:last-child{{white-space:normal;min-width:100px}}.tag{{display:inline-block;border:1px solid #7494b5;padding:4px 10px;border-radius:8px;margin-right:8px}}.up{{color:#b52631}}.down{{color:#008060}}details summary{{cursor:pointer;font-weight:bold}}li{{margin:8px 0}}footer{{padding:16px;color:var(--muted)}}@media(max-width:800px){{main{{padding:12px}}h1{{font-size:24px}}.card{{grid-column:span 6}}header{{padding:20px}}}}@media(max-width:480px){{.card{{grid-column:span 12}}}}@media print{{body{{background:white}}main{{padding:0}}nav{{display:none}}.scroll{{overflow:visible}}table{{font-size:10px}}th,td{{white-space:normal;padding:5px}}details{{display:block}}}}
</style></head><body><main><header><div class="eyebrow">本地数据 · 右侧趋势 · 盘后研究</div><h1>9月18日：信号扩散，但别把修复当成买点</h1><p><span class="tag">当前动作：观察，不生成买单</span><span class="tag">严格首次回踩：0只</span></p><p>数据截至2026-09-18收盘；生成于{generated}（北京时间）。沪深A股，不含北交所。</p><p class="muted">原始价格锚：本地通达信；MA/RPS/ATR：本地同花顺等比前复权。G0运营一致性通过，不代表独立外部复权证明。</p><p>宏观趋势：暂按风险收缩后的修复观察（推断，指数趋势未覆盖）；量化状态：修复，ST_cycle 82.2；盘面过程：修复扩散。模型formal=false，35%仅是风险上限参考，不是目标仓位。</p><p class="muted">唯一计划：等待行业共振与账户条件补齐。入场区间、首仓、追高禁区、硬失效价暂不授权；缺少账户权益/持仓/现金/风险预算及实时确认。若行业宽度或结构转弱，取消新增；T+1新仓当日不能卖出。</p></header>
<nav><a href="#results">筛选结果</a><a href="#focus">重点观察</a><a href="#market">市场与行业</a><a href="#gates">六道门</a><a href="#all">全部名单</a><a href="#audit">核验与缺口</a></nav>
<div class="grid"><article class="card"><div>策略A完整命中</div><div class="num">{len(a)}</div><div class="muted">日报只展示前30，非全量</div></article><article class="card"><div>RPS120 ≥ 80</div><div class="num">{audit['A_rps120_ge80']}</div><div class="muted">不等于完整G2通过</div></article><article class="card"><div>G2数值条件通过</div><div class="num">{len(num)}</div><div class="muted">尚须行业/执行/账户门</div></article><article class="card"><div>B原始 / 首次复核</div><div class="num">{len(b)} / {first_b}</div><div class="muted">华能蒙电是重复回踩</div></article></div>
<section id="results"><h2>这次到底选出了什么</h2><p>策略A：预警转强{stage_counts.get('预警转强',0)}只、放量启动{stage_counts.get('放量启动',0)}只；相较前一交易日YJ/QD条件，69只新增、5只连续。新增只表示前一天未满足，并非历史首次启动。</p><p>本页A池为V2的DISCOVERY或UPGRADE，加20日均成交额≥1.5亿元。RPS在原始A筛选中用于排序而非硬过滤；之后才单独应用RPS120≥80等G2数值门。严格数值门还要求上市≥260根、收盘高于MA20/MA60、距MA20≤12%。</p><p>原ZZ1234公式复刻命中{meta['tdx_exact_count']}只，是另一口径，不与A的74只、行业整合候选25只相加。复刻使用本地前复权序列，不能宣称与通达信终端结果逐只相同。</p><div class="alert">最重要的区别：贴近20日线 ≠ 趋势已恢复。74只中只有16只通过本页全部数值门，其余可能尚未站上60日线、RPS不足或乖离偏大。</div></section>
<section id="focus"><h2>五只值得继续复核的观察股</h2><p>以下不是胜率排序，也不是当前买单。先看结构更完整的顺络电子、亿道信息；芯源微、拓荆科技保留观察；诺唯赞仅作非前列行业对照。不因某只股票入选就放宽G3。</p>{focushtml}<p class="muted">MA与此前高点是9月18日快照参考，不是可机械挂单的入场/止损价。ATR是20日真实波幅均值，单位元。价格计算基准为本地前复权；候选当日收盘已与TDX原始价逐只核对。</p><h3>早期修复组：需要再等一步</h3><p>中船特气688146、长川科技300604、盛美上海688082、长电科技600584、北方华创002371、通富微电002156虽然有转强形态，但收盘仍低于各自MA60，不能称为完整G2合格。其较低RPS50与较高RPS120并不矛盾：代表近50日相对偏弱、120日历史表现仍强。</p><h3>不追高组</h3><p>华峰测控当日上涨14.36%，距MA20约9.07%，且MA20尚未上行；义翘神州、明微电子、新莱应材、泓博医药距MA20超过12%，未通过统一G2乖离条件。放量不是无条件追入理由。</p></section>
<section id="market"><h2>市场改善是真的，趋势恢复还没完成</h2><p><b>事实：</b>5207只有效收益样本中，上涨占{market['up_pct']}%，收益中位数<span class="up">+{market['median_ret1_pct']}%</span>；站上MA20占{market['above_ma20_pct']}%，站上MA60占{market['above_ma60_pct']}%。TDX生态涨停76、跌停1、炸板27，炸板率26.21%；昨日涨停股平均收盘收益<span class="up">+2.50%</span>。</p><p><b>推断：</b>单日修复扩散明显，但约六成个股仍在MA20之下，不宜把普涨日当作全面趋势牛市。情绪评分82.2仍受连续两日升档确认限制，维持修复状态。</p><p>本地TDX生态成交额20,334.93亿元；前复权库市场样本成交额20,701.30亿元，两者覆盖和样本过滤不同，不作为同口径增减额混用。指数位置/分时均价未覆盖，本页不据此判断指数趋势。</p>{indhtml}<p class="muted">按精确TDX行业代码排序，简称存在层级概括；例如首位“酿酒”T030503仅9个有效成分，不等于全部白酒行业。行业基准额沿用整合脚本：此前20日成交额中位数；A/B流动性为含当日20日均值，两者不是同一个ADV20定义。</p><p><b>行业判断：</b>评分第一组酿酒最大单股占47.85%，G3降级。元器件、半导体分别第二、第三，MA20宽度75.52%、86.80%，最大单股占比4.96%、4.57%，是更分散的修复方向，但按现有执行规则仍仅观察。没有使用估算资金流，也没有把成交额上升解释为净流入。</p></section>
<section id="gates"><h2>六道门：数据通过，不等于交易通过</h2>{table(pd.DataFrame(gates))}<p>表内25只为独立行业整合筛选的展示结果，不是74只A池的通过数量；其G4仅检查流动性与乖离，未验证逐股2R空间和实时买点。</p><div class="alert">当前G3实现：先取排名第一行业，再做集中度否决；第一组被否决后不会自动递补第二组。因此零交易既有规则设计因素，也有账户资料缺失因素，不是行情数据坏了。本次保留规则，不为得到买单临时改参数。</div></section>
<section><h2>B类首次回踩：今天为0只</h2><p>原脚本唯一命中：华能蒙电600863，收盘4.73元、RPS50=59.27、RPS120=59.59、量比0.69、距MA20为−0.35%。TURN日期9月9日，至9月18日为7根K线。</p><p>但9月14、15、16日已满足AMBUSH，且9月10日即触及MA20上方2%区域。无论按“首次合格回踩”还是“首次进入回踩区”定义，9月18日都不是首次。因此从本次严格B名单排除；生产脚本原始结果保留便于追溯，未静默改动策略。</p></section>
<section id="all"><h2>通过G2数值条件的16只</h2>{select_table(num,basekeys,baselabels)}<details><summary>展开全部74只A候选及数值门结果</summary>{select_table(a,basekeys,baselabels)}</details></section>
<section><h2>行动边界与六问</h2><ol><li>顺势还是抄底：收复MA20但低于MA60，只称早期修复；不因跌幅大就买。</li><li>位置是否关键：看此前20日高、MA60与最近结构低点，不能仅看一根阳线。</li><li>确认了吗：日线信号已确认，但行业门没有通过，盘中承接也尚未取得。</li><li>错在哪里：交易前先定结构失效位；跌破后不得补亏损仓。新仓T+1不能当日止损，跌停时计划损失可能被突破。</li><li>至少2R吗：本次未形成合法主计划，尚未验证逐股2R，不假设前高必能达到；风险距离还须考虑ATR和涨跌幅限制。</li><li>亏1R还能执行吗：账户权益、现金、持仓、可卖数量与风险预算未提供，不输出股数。</li></ol><p><b>空仓：</b>保持观察，等行业许可与买点确认。<b>已有仓：</b>按原计划失效位管理，不因重复入选加仓；收盘失守MA20或MA60时依既定趋势退出规则检查，最早在可卖时执行。<b>重仓：</b>先核对相关性及T+1可卖数量，不把同类科技股当分散。</p><p><b>机会成本：</b>等待可能错过快速V形拉升；相反，追修复可能遭遇MA60/前高阻力。当前没有可靠测算，不虚构错过收益或上涨概率。</p></section>
<section id="audit"><h2>数据质量与可追溯性</h2><ul><li>TDX 5327个沪深股票文件中5208个截至9月18日，119个较早；原因可能包含停牌、退市或未更新，未逐一归因。所有本次75只A/B原始候选均有9月18日TDX记录，收盘价差≤0.011元。</li><li>本地前复权RPS面板截至9月18日，共10,072,314行、5221个代码，当日5209只；RPS50/120/250有效样本分别5185/5160/5114。独立逐期重算横截面排名，最大差0；股票×日期无重复。</li><li>G0 16/16原始历史锚通过；全库复权公式、量额、最新归一零错配。它只证明本地运营一致性，非独立外部复权证明。</li><li>A“刚启动”并未强制前日无信号，故另报69新增、5连续；B未实现首次去重，故原始1只与严格0只分开。</li><li>TDX本地名称与行业为当前版本；历史ST不可完整还原。ST_cycle未走样本外验证；不宣称本策略稳定盈利。</li><li>核验置信范围：数据日期/数值一致性有直接检查支持；未来收益不确定，行业分组与门控设计仍需回测校准。</li></ul><details><summary>来源、版本与审计文件</summary><p>生产库 data/hithink/market.duckdb:v_daily_qfq；原始锚 C:/new_tdx/vipdoc；行业 C:/new_tdx/T0002/hq_cache/tdxhy.cfg；RPS output/tdx_stock_daily_with_rps.parquet。均为日频，价格元、成交额元（展示时亿元）、量比无单位，日期北京时间。</p><p>版本：V2右侧发现器、bottom_ambush_v1、ST_cycle_v1。本页未改变生产公式；完整结果 right_side_20260918_A_enriched.csv / right_side_20260918_B_enriched.csv；校验 right_side_20260918_audit.json；整合审计 integrated_decision_20260918_audit.json；每日运行日志 daily_dual_strategy_last_run.log。</p><p>同步、库校验、G0、G1、RPS、A、B步骤均完成，退出码0；同步/校验业务信封ok=true。报告数据截至与目标日期一致，无未来K线参与。</p></details></section><footer>研究筛选与评估，不自动下单，不承诺盈利。页面离线可读；行情快照不能代替下次交易时的实际报价与风险评估。</footer></main></body></html>'''
path=OUT/'a_share_analysis_20260918_right_side_review.html'
assert 'NaN' not in body and 'Infinity' not in body
assert len(a)==74 and len(num)==16 and len(b)==1 and first_b==0
path.write_text(body,encoding='utf-8')
# Keep the shared journal's original candidates, but make the current audit corrections visible.
journal=OUT/'a_share_analysis_daily_dual_strategy_journal.html'
text=journal.read_text(encoding='utf-8')
notice='''<aside style="margin:16px;padding:18px;background:#fff4dc;color:#674000;border:2px solid #e0a34a;border-radius:12px"><b>9月18日复核补充：</b>A完整命中74只，本页只显示前30；16只通过G2数值条件。B原始1只（华能蒙电）是重复回踩，严格首次回踩为0只。G3行业共振、G5账户门未通过，全部仅观察。A有69只较前日新增、5只连续，不等于历史首次启动。<a href="a_share_analysis_20260918_right_side_review.html">查看完整复核与名单</a></aside>'''
assert '数据截至 2026-09-18' in text
if '9月18日复核补充' not in text:
    journal.write_text(text.replace('<body>','<body>'+notice,1),encoding='utf-8')
print(json.dumps({'report':str(path),'A':len(a),'numeric_G2':len(num),'strict_B':first_b,'stages':stage_counts,'top_industry_counts':top_industry},ensure_ascii=False))
