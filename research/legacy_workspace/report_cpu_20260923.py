"""9/23 source-backed review. Does not alter production thresholds or orders."""
import json, html, re
from pathlib import Path
from datetime import datetime
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; O=ROOT/'output'
def read(n): return json.loads((O/n).read_text(encoding='utf-8'))
d=read('cpu_right_side_20260923_research.json'); old=read('cpu_right_side_20260922_research.json')
rows={r['code']:r for r in d['rows']}; prior={r['code']:r for r in old['rows']}
A=pd.read_csv(O/'right_side_20260923_A_enriched.csv'); aud=d['screen_audit']
prod=read('integrated_decision_20260923_audit.json')
assert d['asof']=='2026-09-23' and len(A)==19 and aud['B_raw']==0
def e(v): return html.escape(str(v))
def f(v,n=2): return '未覆盖' if v is None else f'{v:.{n}f}'
def table(head,body): return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(v)+'</th>' for v in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in body)+'</tbody></table></div>'
sources={
 'reuters':('路透9/23开盘报道：油价、债券收益率回升','https://www.investing.com/news/stock-market-news/wall-st-opens-lower-as-oil-bond-yields-mover-higher-4913260'),
 'meta':('Meta 9/8：Muse的云计算环境','https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse'),
 'amd':('AMD 9/18：EPYC与Agent工作负载','https://newsroom.amd.com/news/amd-epyc-cpus-deliver-every-layer-agentic-ai-stack/'),
 'tf':('通富微电2026半年报','https://static.cninfo.com.cn/finalpage/2026-08-29/1225527099.PDF'),
 'montage':('澜起官方内存接口产品','https://www.montage-tech.com/Memory_Interface'),
 'hh':('华海诚科2026半年报公告转载','https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12569471&stockid=688535'),
 'tg':('铜冠铜箔2026半年报公告转载','https://stock.stockstar.com/notice/SN2026082600052440.shtml'),
 'yd':('亿道信息2026半年度业绩预告','https://static.cninfo.com.cn/finalpage/2026-07-11/1225420030.PDF'),
 'calendar':('上交所2026休市安排','https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml')}
def S(k): return '<a href="'+sources[k][1]+'">'+sources[k][0]+'</a>'
us=[('AMD',616.105,-1.22882,'23:15:21'),('INTC',120.96,-2.34135,'23:14:20'),('ARM',327.43,-1.73169,'23:14:49'),('NVDA',225.97,-1.26709,'23:14:56'),('SOXX',564.69,-1.41241,'23:15:13'),('QQQ',741.75,-.76392,'23:14:18'),('SPY',769.69,-.47713,'23:16:40'),('MU',1080.35,-1.44231,'23:16:12'),('XBI',156.82,-3.11979,'23:17:05'),('META',756.36,2.68329,'23:20:49'),('USO',147.29,2.22793,'23:20:38'),('TLT',80.72,-1.25994,'23:20:43')]
ust=table(['代码','价格（美元）','较前收盘','北京时间9/23'],[(a,f(b),f'{c:+.2f}%',t) for a,b,c,t in us])
sensitivity=[(v,int(((A.RPS_120>=80)&A.above_ma60&(A.vs_ma20_pct<=v)).sum())) for v in (8,12,15)]
first=[r for r in prod['candidates'] if r['executable']]
checks=[
('数据新鲜度','5328个沪深A日线文件中，5209个截至9/23；停牌/旧日文件未冒充当日。','通过；18:10的日期错位已消除'),
('复权锚与口径','G0 16/16，公式/量/额/最新归一错配均0；研究池98只当日OHLC逐只对齐TDX。','仅本地运营一致性，不是独立外部复权证明'),
('RPS复算',f'50/120/250分母为5185/5163/5115，独立重算最大差0。','排名可信不代表盈利概率'),
('公式分层','ZZ1234=183；A=19；B=0；生产G2=2。','规则不同，不能混用或相加'),
('首次启动','A中16只较前日新增、3只连续。','新增不等于上市以来首次启动'),
('首次回踩','B=0；源AMBUSH代码未严格落实首次触碰去重。','今日空名单无此歧义，不能声称整套首次逻辑已完全验证'),
('消息时间','美股9/23仍盘中，CPU/半导体多数下跌；不能复制9/21暴涨。','撤销“今晚全面加速”假设'),
('宏观逆风','路透报道油价与债券收益率回升；USO涨、TLT跌与报道方向一致。','是宏观压力线索；不把ETF涨幅当原油或收益率涨幅'),
('Agent需求','Meta产品说明和AMD技术文章支持CPU/内存/工具执行需求。','产品需求不等于今晚新增采购，也非每个VM独占一颗CPU'),
('业务映射','通富AMD客户关系、澜起内存接口、铜冠高频高速铜箔均有业务依据。','不能推断所有A股都获订单或具有同等利润弹性'),
('应用与硬件分化','META约+2.68%，SOXX约−1.41%，XBI约−3.12%。','不能用Meta上涨机械推导全部硬件/AI医药上涨'),
('A股宽度','上涨33.7%，中位收益−0.63%，成交额17626.4亿，较昨约−17.4%。','盘面推断为风险偏好收缩，不是普涨'),
('情绪标签','ST_cycle 88.4→40.4，涨停64→51、跌停6→14、炸板率30.4%→37.8%。','“修复”是模型档位，不是市场已经企稳'),
('行业许可','第一房地产；半导体第4、元器件第10。深物业A具第一行业资格。','A策略19只都不在严格G2两只中；不能为CPU故事放宽行业门'),
('个股结构','澜起、沪电仍在MA20/60上；通富在MA60下；华虹在两线下。','同一叙事，交易结构不同'),
('追高与轮动','华海A新启动但乖离11.66%；百普、义翘乖离40.4%/44.0%。','新信号不等于便宜；强动能不可直接追'),
('赔率检验','以研究回踩区上沿及最近可见压力计算，核心观察不具保守2R。','不能拿遥远60日高点充当4天目标；区间仅静态观察'),
('参数稳健性',f'固定RPS≥80和站MA60，A中乖离≤8/12/15%分别为{sensitivity[0][1]}/{sensitivity[1][1]}/{sensitivity[2][1]}只。','名单依赖阈值；这不是样本外回测，不做盘后调参'),
('亏损反例','相似信号次根开盘至第4根收盘：通富−11.25%、华虹−10.76%。','足以否定必赚；当前池回溯有选择偏差，不估计胜率'),
('日历与账户','9/24、28、29、30仅4个交易日；G5缺账户，T+1及休市影响退出。','月底是复盘截止日，不是保本承诺；当前不产生订单')]
assert len(checks)==20
checktable=table(['检验视角','证据 / 反证','结论'],[(f'{i:02d} · {a}',b,c) for i,(a,b,c) in enumerate(checks,1)])
core=['688535.SH','301217.SZ','001314.SZ','688401.SH','688008.SH','002463.SZ','002156.SZ','688347.SH']
notes={
'688535.SH':'A新增。看112–114一带未来承接、再转强；失守动态MA60约111.92且不收复则取消。116.88近压，不能追涨后默认2R。',
'301217.SZ':'A新增。高频高速铜箔扩散观察；看113–116一带承接，MA60约113.31失守降级；123.97前高压力。',
'001314.SZ':'A新增。端侧产品业务有依据，但云端CPU需求不等于终端订单；53–54附近承接需确认，MA60约53.43是结构参考。',
'688401.SH':'A新增，只按形态观察；70–72一带回踩确认，MA60约70.18失守降级。不宣称取得新增CPU订单。',
'688008.SH':'A/B今日均不命中，作为既有观察。215–219未来承接区非挂单；MA60约214.55失守取消；230–233近压。',
'002463.SZ':'A/B均不命中；124–126附近未来缩量承接再评估。MA20约123.62失守降级；129.67–131.25压力。',
'002156.SZ':'取消启动优先级。62.80已低于昨日低点62.83，仍低于MA60约64.06；先修复64.06，再看65。',
'688347.SH':'继续降级。229.52低于昨日低点229.63；先收复MA20约231.83，MA60约270.92仍远，不因低于前高抄底。'}
ct=table(['研究标的 / 当日信号','收盘 / 日涨跌','MA20 / MA60','RPS120 / 乖离','观察与失效（不可执行）'],[(e(rows[c]['name'])+'<br>'+c+'<br>A:'+('是' if rows[c]['A'] else '否'),f(rows[c]['close'])+f'<br>{rows[c]["ret1"]:+.2f}%',f(rows[c]['ma20'])+' / '+f(rows[c]['ma60']),f(rows[c]['rps120'],1)+' / '+f(rows[c]['bias'],1)+'%',notes[c]) for c in core])
at=table(['代码/名称','阶段','收盘','RPS120','乖离','MA60','数值G2*','前日新增'],[(r.ts_code+'<br>'+e(r['name']),r.role,f(r.close),f(r.RPS_120,1),f(r.vs_ma20_pct,1)+'%',f(r.ma60),'是' if r.g2_full else '否','是' if r.a_new_vs_previous_day else '否') for _,r in A.sort_values(['g2_full','RPS_120'],ascending=False).iterrows()])
gt=table(['门控','结果','说明'],[(r['id']+' '+r['name'],'通过' if r['pass'] else '未通过',e(r['detail'])) for r in d['gates']])
it=table(['排名/细分代码','行业','成分','MA20宽度','共振','单股驱动'],[(str(i+1)+' '+r['industry'],r['name'],r['members'],f(r['breadth_ma20_pct'],1)+'%',f(r['resonance'],1),'是' if r['single_stock_driven'] else '否') for i,r in enumerate(d['industries'])])
rrcodes=['000011.SZ','688535.SH','301217.SZ','688008.SH','002463.SZ']
rt=table(['标的','假设回踩入场*','结构参考','计划距离','ATR20','涨跌停×0.6价格风险','有效风险D','最近压力','潜在R'],[(rows[c]['name'],f(rows[c]['zone'][1]),f(rows[c]['invalid']),f(rows[c]['zone'][1]-rows[c]['invalid']),f(rows[c]['atr']),f(rows[c]['zone'][1]*(.12 if c.startswith(('300','301','688')) else .06)),f(rows[c]['D']),f(rows[c]['pressure']),f(rows[c]['rr'])) for c in rrcodes])
xt=table(['反例','信号日','次根开盘','第4根收盘','未计费收益'],[(r['name'],r['signal'],r['entry_date'],r['exit_date'],f(r['gross_return_pct'])+'%') for r in d['counterexamples'][:4]])
# Reuse only the existing native chart renderer; no prior-date narrative is executed.
chart_source=Path(__file__).with_name('report_cpu_20260922.py').read_text(encoding='utf-8')
chart_source=chart_source[chart_source.index("chartcodes="):chart_source.index("generated=datetime")]
chart_source=chart_source.replace("['688008.SH','002156.SZ','688347.SH','002463.SZ']","['688535.SH','301217.SZ','688008.SH','000011.SZ']").replace('至9/22','至9/23')
exec(compile(chart_source,'reused_chart_renderer','exec'))
generated=datetime.now().astimezone().isoformat(timespec='minutes')
mapping=table(['情景层','可能被市场交易的逻辑（非事实预测）','研究映射','必须否决的跳跃'],[
('直接需求','Agent把AI从回答问题扩展为执行任务，CPU与内存资源占用提高','通富、澜起、海光','VM不等于独占CPU；海外景气不等于国产替代订单'),
('瓶颈扩散','算力扩容使封装材料、互连与高速板材料受到关注','华海诚科、铜冠铜箔、沪电、深南','封装材料/PCB不是CPU本体，新增收入及客户关系尚待验证'),
('端侧扩散','Agent功能由云向端渗透，AI PC/边缘计算产品受关注','亿道信息、瑞芯微','Meta云架构不证明端侧销量兑现；瑞芯乖离已偏大'),
('国产化二阶联想','政策或采购偏好改变时，国产计算生态可能获重估','海光、龙芯、华虹、中芯及设备','这是独立假设，不把美股CPU涨跌写成A股代工订单'),
('反向轮动','海外科技承压，A股资金可能选择内需或地产','深物业A；其余地产仅按行业池','外盘跌不必然利好地产；不能把避险叙事当买点'),
('医药旁支','AI工具提高研发效率的长期想象仍可研究','百普、义翘等既有池','XBI盘中跌3.12%，个股乖离极高，不是今晚新增统一利好')])
body=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>9月23日右侧趋势｜外盘分化与20项检验</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf2f7;color:#20334a;font:16px/1.75 "Microsoft YaHei",sans-serif}}main{{max-width:1420px;margin:auto;padding:24px}}header{{background:#132b43;color:white;padding:28px;border-radius:18px}}h1{{font-size:32px;margin:8px 0}}h2{{font-size:23px}}h3{{font-size:18px}}.action{{color:#ffda93;font-size:21px}}section,article{{padding:24px;background:white;border-radius:14px;margin-top:18px;min-width:0;break-inside:avoid}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}}.stat{{background:white;padding:20px;border-radius:12px}}.stat b{{display:block;font-size:28px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;vertical-align:top;text-align:left;border-bottom:1px solid #dce4ee;min-width:80px}}th{{background:#eef4fa}}a{{color:#17679f}}p,td,a{{overflow-wrap:anywhere}}.small{{font-size:14px;color:#60748b}}header .small{{color:#bbd0e4}}.warn{{background:#fff4dd;border-left:4px solid #bc8429;padding:14px}}nav a{{display:inline-block;margin:12px 16px 0 0}}svg{{width:100%;height:auto}}@media(max-width:760px){{main{{padding:10px}}header,section,article{{padding:17px}}.grid{{grid-template-columns:1fr}}.stats{{grid-template-columns:1fr 1fr}}h1{{font-size:26px}}}}@media print{{nav{{display:none}}body{{background:white}}section{{border:1px solid #ddd}}}}</style></head><body><main>
<header><div>沪深A股 · 2026-09-23收盘 · 不含北交所</div><h1>逻辑可以展开，买点必须收紧</h1><p class="action">A 19只 / B 0只；今晚美股芯片回撤。当前动作：观察，不产生买单。</p><p>G0–G4通过，G5缺账户。严格G2仅深物业A、盈新发展；其中深物业A有第一行业资格，但没有通过完整入场与2R检验。科技研究优先复查华海诚科、铜冠铜箔、亿道信息，旧池观察澜起、沪电；这不是替代生产门控的买入排名。</p><p>唯一主计划：暂不新开仓；先等外盘收盘与下个交易日承接。深物业A只作第一行业观察，9.0–9.2附近若未来回踩，再核算赔率和账户条件；收盘跌破动态MA20约8.98且不修复，取消该回踩假设。没有挂单、没有单股仓位。</p><p class="small">生成：{generated}。美股冻结9/23北京时间23:14–23:20盘中快照，并非收盘；本报告不混用昨日美股大涨。量化状态“修复”，ST_cycle=40.4，formal=false；35%仅风险参考上限，不是目标仓位。盘面推断：风险偏好收缩；宏观三态缺完整指数证据，不强行分类。账户与有效分钟确认未覆盖；T+1新仓当天不能卖。数据置信度较高，映射与短线盈利判断不确定。</p></header>
<nav><a href="#screen">选股与门控</a><a href="#us">美股</a><a href="#map">映射情景</a><a href="#stocks">核心观察</a><a href="#checks">20项检验</a><a href="#plan">月底纪律</a><a href="#all">完整名单</a></nav>
<div class="stats"><div class="stat">A刚启动<b>19只</b>16新增 / 3连续</div><div class="stat">B回踩<b>0只</b>无旧名单替补</div><div class="stat">原始ZZ1234<b>183只</b>不是19只的同义口径</div><div class="stat">严格G2<b>2只</b>1只有第一行业资格</div></div>
<section id="screen"><h2>一、傍晚的数据阻断已消除，交易限制仍在</h2><p>本次未使用skip-sync。增量同步确认9/23版本已存在，库校验通过；TDX已有5209只9/23日K。重算G0/G1后刷新RPS和A/B，并对RPS独立复算，最大差0。G0只证明本地运营一致性，不是独立外部复权证明。</p>{gt}<p>G2的“可执行”字段只表示第一行业资格，不代表可立即下单。深物业A收9.59、RPS120=81.1；盈新发展收3.28、属于第3细分行业，仅观察。二者均非今日A/B命中。A中17只RPS120≥80，9只数值条件合格，但均未进入严格G2。</p><p>本次仍发现既有实现边界：A未强制只输出首次信号；B未严格首次去重；A的ADV均值与生产G2滞后成交额中位数不同。本次不擅自修改策略阈值，报告如实区分。</p></section>
<section id="us"><h2>二、今晚不是“芯片全面暴涨”，而是风险偏好与板块分化</h2>{ust}<p>行情来自本次finance快照，单位美元；时间均为北京时间，非同步到秒、不代表最终收盘。路透9/23开盘报道指出油价、债券收益率回升及地缘谈判不确定性施压股市。{S('reuters')}。当前USO上涨、TLT下跌与该背景一致；这不是原油现货价格或国债收益率的替代数值。</p><p>Meta逆势上涨，而CPU、半导体与生物科技ETF下跌，说明“应用受到欢迎→所有基础设施一起涨”的推导不成立。获利回吐是可能解释，不是已验证的唯一原因；未发现足以把今晚跌势简单解释成产业订单恶化的证据。</p><p>长期需求依据仍在：{S('meta')}的VM配置含CPU、内存与工具执行环境；{S('amd')}讨论CPU对Agent工作负载的支持。两者是9/8、9/18信息，不是今晚新采购公告，且厂商性能宣传不等于独立盈利预测。</p></section>
<section id="map"><h2>三、把映射充分展开，但保留每一层反证</h2>{mapping}<p>已核实的业务锚：{S('tf')}披露AMD客户关系；{S('montage')}列有内存接口芯片；{S('hh')}披露环氧塑封材料；{S('tg')}披露高频高速铜箔；{S('yd')}提及AI PC、AIoT等产品。这里只据此建立研究关系，不声称取得Meta、AMD或英伟达本次新增订单。</p><p class="warn">强乐观情景：海外回撤后企稳，A股核心守位、材料与端侧扩散；中性情景：故事延续但高低切，强股不一定给低风险买点；悲观情景：利率/油价压力延续，次级映射成为兑现出口。不能给这些情景编造胜率。</p></section>
<section><h2>四、A股自身的证据比故事更重要</h2><p>同口径成交额21330.3→17626.4亿元，约−17.4%；上涨比例43.8%→33.7%，日收益中位−0.63%，MA20上方占比56.2%→49.9%。TDX生态金额17315.1亿元是另一覆盖口径，不混算。</p><p>涨停64→51，跌停6→14，炸板率30.4%→37.8%，昨日涨停平均收盘收益转为−0.13%。ST_cycle从88.4降至40.4；“修复”仅模型档位，不能解释成盘面已经修复。</p>{it}<p>房地产按TDX细分代码分组，同名不代表整个地产统一排名；酿酒第2但单股成交额占比48.4%，不能当广泛共振。半导体第4、元器件第10，均不是可执行第一行业。未使用估算资金流或过时概念成分。</p></section>
<section id="stocks"><h2>五、新信号与旧观察分开，逐股给出取消条件</h2>{ct}<p>以上8只均“观察，不可执行”。列表是研究优先顺序而非收益概率排序。今日半导体/PCB部分个股相对抗跌，但A股先于今晚美股开盘，不能声称它们已经通过今晚外盘下跌的压力测试；要等下一交易日验证。</p><p>其他跟踪：深南414.77（+3.69%）但乖离约10%；拓荆730.30（+1.09%）保留趋势观察。百普129.80、义翘150.00，乖离约40.4%/44.0%，不因AI医药故事追涨。沃格、江南新材、安洁的涨停也不等于次日可成交低风险买点。</p></section>
<div class="grid">{''.join(charts)}</div>
<section><h2>六、用赔率反驳“月底都能赚”</h2>{rt}<p>*这里是假设未来回踩到静态均线附近，不是已触发价格、保证能到达的价格或挂单。D=max(计划距离、1×ATR20、涨跌幅限制×0.6×入场价)。12%/6%下限是未完成样本外验证的保守风险预算启发式，不是等到跌这么多才止损，也不是最大可能损失。此表深物业A的ATR约0.554约束最强，其余列举股票为涨跌幅压力下限约束最强。</p><p>最近可见压力不等于确定目标；即使放弃保守下限，名义窄止损也不能保证成交。深物业A有行业资格但此例仅约1.37R，不能为凑2R把目标抬到无依据的价位。宏观、行业、入场和账户需要同时满足。</p></section>
<section id="checks"><h2>七、20项不同观点的证据检验</h2><p>20个视角的可核验摘要，不是20次独立统计实验，不输出保证盈利的概率。支持与否决同等记录。</p>{checktable}<h3>历史负收益反例</h3>{xt}<p class="small">研究区间2025/9/1–2026/9/15；当前CPU池回看YJ或QD、MA60之上、RPS120≥80、乖离≤12%、前20日成交额中位≥1.5亿，次根开盘至第4根收盘。只取各股负收益例反驳必赚；有当前池选择/幸存者偏差，未模拟行业门、停牌、涨跌停成交、费用及组合，个股4根K也不保证4个市场交易日。不能据此估计策略胜率或期望收益。</p></section>
<section id="plan"><h2>八、月底是审计期限，不是盈利期限</h2><p>从9/24开始，到9/30仅24、28、29、30四个交易日；9/25–27休市，10/1–7继续休市。{S('calendar')}。若9/24买入，当天不能卖，下一正常卖出机会已是9/28；9/30新买则不能当天完成月底退出。</p><ul><li>空仓：今晚仅冻结观察池；次日不盲追高开，不因低开自动抄底。外盘继续走弱、第一行业不符、结构破位或赔率不足，取消新开仓。</li><li>已有趋势仓：按原硬失效位与动态均线管理；收盘破MA20降险，连续两日下方或破MA60按规则退出，在下一可成交时点执行。跳空或跌停可能使实际损失超过预算。</li><li>重仓：优先审视相关科技股合计风险，不把多只CPU/PCB/材料视作独立分散；不向亏损仓补仓。</li><li>9/28重新验证假期消息与价格，9/29–30按原计划复盘，不为“月底必须赢”扛过失效位。情绪短线不能临时改成长线解套。</li></ul><p>不买的机会成本是错过一次快速反弹；接受这个成本不等于预测一定下跌。本次未提供账户，首仓/单股上限未生成；35%模型上限也不是建议必须用满。</p></section>
<section id="all"><h2>九、A策略完整19只</h2>{at}<p>*数值G2不含行业许可；生产严格G2为深物业A、盈新发展。上表不是买单，B为0。RPS未被悄悄改为A的统一硬筛，东方国信等低RPS命中如实保留。</p></section>
<section><h2>十、事实、推断与拒绝填充的数据</h2><p><b>事实：</b>本地TDX原始锚与同花顺生产库对齐，指标来自v_daily_qfq派生面板；日频、人民币元、成交额元、原始量。98只研究池包含既有池、映射池和本次A/B/G2，不是全市场基本面逐股尽调。</p><p><b>推断：</b>当前风险偏好收缩，科技故事尚有产业依据但执行性下降。半导体落到第4使大量个股因行业阈值被排除，G2从35变2有明显规则效应，不能解释成33家公司基本面一夜恶化；要回测边界效应，不能临时把前3改前4。</p><p><b>交易假设：</b>只有产业催化、第一行业、承接、结构和保守赔率同时改善，才重新评估；当前没有通过完整交易评估的买单。</p><p>未使用估算资金流、过时概念成分、虚构分钟历史、未经核验的当前估值。没有执行完整组合样本外回测，没有修改生产参数，没有下单。三项主要风险：海外利率/油价、节假日T+1退出、题材映射过度；对应动作是等待确认、限制风险、遇失效撤销，而非保证盈利。</p><p>审计文件：right_side_20260923_audit.json、cpu_right_side_20260923_research.json、integrated_decision_20260923_audit.json、cpu_20260923_twenty_checks.json。<a href="a_share_analysis_daily_dual_strategy_journal.html">唯一双策略日报</a>。本页为研究记录，非收益承诺。</p></section></main></body></html>'''
assert len(charts)==4 and not any(v in body for v in ('NaN','Infinity','{{PLACEHOLDER}}'))
(O/'a_share_analysis_20260923_cpu_20_checks.html').write_text(body,encoding='utf-8')
(O/'cpu_20260923_twenty_checks.json').write_text(json.dumps({'generated':generated,'asof':d['asof'],'us_snapshot':us,'sources':sources,'checks':[{'id':i,'lens':a,'evidence':b,'conclusion':c} for i,(a,b,c) in enumerate(checks,1)],'sensitivity':sensitivity},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'A':len(A),'checks':len(checks),'charts':len(charts),'sensitivity':sensitivity,'report':'a_share_analysis_20260923_cpu_20_checks.html'},ensure_ascii=False))
