import json,html
from pathlib import Path
from datetime import datetime
import pandas as pd
R=Path(__file__).resolve().parents[1]; O=R/'output'
d=json.loads((O/'cpu_right_side_20260921_research.json').read_text(encoding='utf-8'))
by={x['name']:x for x in d['rows']}; a=pd.read_csv(O/'right_side_20260921_A_enriched.csv'); audit=d['screen_audit']
style=(O/'a_share_analysis_20260920_cxmt_buy_sell_plan.html').read_text(encoding='utf-8').split('<style>',1)[1].split('</style>',1)[0]
style+=' .auditnum{font-size:24px;color:#08717a;font-weight:700}.status{font-weight:700}.timeline{display:flex;gap:10px;flex-wrap:wrap}.day{border:1px solid #ccdadd;background:#fff;border-radius:10px;padding:10px 15px} article{min-width:0} .scroll td{max-width:380px} '
E=html.escape
def table(head,rs): return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+E(t)+'</th>' for t in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rs)+'</tbody></table></div>'
def n(x): return f'{x:.2f}' if x is not None else '不适用'
def pct(x): return f'<span class="{"up" if x>=0 else "down"}">{x:+.1f}%</span>'
def z(r): return f"{r['zone'][0]:.1f}–{r['zone'][1]:.1f}" if r['zone'] else '尚无合法回踩区'
def item(name): return by[name]
core=['深南电路','沪电股份','华天科技','通富微电','澜起科技','海光信息']
theses={
'深南电路':('研究优先①｜载板/PCB','业务有载板对应，已站上MA20/60；等待缩量回踩后的确认，不能把未命中A/B写成启动买点。'),
'沪电股份':('研究优先②｜服务器/网络PCB','乖离较低、站上MA20/60；近端空间小，价格便宜于前高不等于赔率足够。'),
'华天科技':('信号交集｜封测扩散','今天A信号且数值门通过；属连续信号，量比2.79、乖离11.82%，不宜追开盘加速。'),
'通富微电':('业务联系较直接｜AMD封测','公司披露AMD客户关系，但价格未收复MA60；先看64.45附近能否站稳，再重做形态与风险。'),
'澜起科技':('产品关联｜内存接口','RCD/MRCD/MDB等有真实产品基础；先看217.54附近MA60确认，不假设单CPU收入同步翻倍。'),
'海光信息':('情绪映射中心｜国产CPU','A信号已出现，但离MA60仍远；国内替代逻辑不是AMD新平台授权，不能把246元修复偷换成成熟趋势。')}
coretable=table(['研究角色 / 股票','收盘 / RPS120','MA20 / MA60','观察条件（不是订单）','静态回踩区 / 结构参考','近端压力预算'],[
 [theses[x][0]+'<br><b>'+x+'</b><br>'+by[x]['code'],n(by[x]['close'])+' / '+f"{by[x]['rps120']:.1f}",n(by[x]['ma20'])+' / '+n(by[x]['ma60']),theses[x][1],z(by[x])+('<br>结构线 '+n(by[x]['invalid']) if by[x]['zone'] else '<br>先收复MA60'),(n(by[x]['rr'])+'R' if by[x]['zone'] else '未形成合法入场方案，不算有效赔率')] for x in core])
allcpu=[r for r in d['rows'] if r['cpu_basket']]
cpulist=table(['股票 / 代码','研究关联','收盘 / 当日','RPS120 / 距MA20','MA20 / MA60','A / 数值门 / 完整G2','判断'],[[r['name']+'<br>'+r['code'],r['group'],n(r['close'])+' / '+pct(r['ret1']),f"{r['rps120']:.1f} / "+pct(r['bias']),n(r['ma20'])+' / '+n(r['ma60']),f"{'是' if r['A'] else '否'} / {'过' if r['basic'] else '未过'} / {'过' if r['g2'] else '未过'}",r['phase']+'<br>'+('；'.join(r['failures']) or '其余门仍需通过')] for r in allcpu])
nums=a[a.g2_full]
atab=table(['名称 / 代码','收盘','RPS120','距MA20','较前日新信号','行业 / 完整G2'],[[r['name']+'<br>'+r.ts_code,n(r.close),f'{r.RPS_120:.1f}',pct(r.vs_ma20_pct),'是' if r.a_new_vs_previous_day else '否，连续信号',by[r['name']]['industry']+' / '+('通过' if by[r['name']]['g2'] else '未通过')] for _,r in nums.iterrows()])
fulla=table(['股票 / 代码','阶段','收盘','RPS120','距MA20','数值条件','新/连续'],[[r['name']+' / '+r.ts_code,r.role,n(r.close),f'{r.RPS_120:.1f}',pct(r.vs_ma20_pct),'通过' if r.g2_full else '未过','新增' if r.a_new_vs_previous_day else '连续'] for _,r in a.iterrows()])
inds=table(['排名 / 行业代码','行业','MA20宽度','5日中位收益','最大股成交占比','行业共振分'],[[str(i+1)+' / '+r['industry'],r['name'],f"{r['breadth_ma20_pct']:.1f}%",pct(r['ret5_pct']),f"{r['top1_amount_share_pct']:.1f}%",f"{r['resonance']:.1f}"] for i,r in enumerate(d['industries'][:5])])
mapping=[
('第一圈：CPU重新定价','海光信息、龙芯中科；中国长城仅整机/生态联想','海光、龙芯均有CPU产品；不是给海外巨头代工CPU。','海外缺货未必流向国内厂商；架构、软件、供应约束和客户认证决定替代速度。'),
('第二圈：内存与互连的“配套增量”','澜起科技、聚辰股份','澜起有DDR5内存接口产品；聚辰属于配套芯片观察。','CPU数量、内存通道、接口规格与单机价值不是固定比例，不能随意外推倍数。'),
('第三圈：先进封装与测试','通富微电、华天科技、长电科技；华峰测控、长川科技','通富AMD客户关系有披露；其余按产品环节推演。','各家擅长的测试对象、平台、订单份额不同；不能说所有公司都承接同一款CPU。'),
('第四圈：载板—PCB—材料','深南电路、兴森科技、沪电股份、景旺电子、生益科技、联瑞新材','深南FC-BGA与沪电数据通讯业务有公开材料。','CPU缺货还可能拖慢服务器交付；通用PCB扩产不等于高端载板产能可以互换。'),
('第五圈：散热与服务器整机','英维克、高澜股份、申菱环境；浪潮、中科曙光、紫光','属于系统层扩散假设。','元件涨价或缺货会挤压整机利润；功耗、液冷渗透、订单与毛利需分别核实。'),
('第六圈：回流原有设备材料/光链','拓荆、芯源、江丰；中际旭创、新易盛、天孚通信','与长鑫、算力周期可能形成情绪共振；并非CPU涨价的直接利润函数。','外圈扩散最易一日游；不得将通信设备、特气与CPU视为完全同质。')]
maptable=table(['想象扩散顺序（假设）','观察对象','已知与推断的边界','反向传导'],mapping)
checks=[
('日期审计','本地数据真是9月21日吗？','5208个TDX股票文件最新日为9月21日；目标候选全部当日锚匹配。','通过；不把119个较早文件假装更新。'),
('复权审计','除权会不会制造假突破？','G0原始历史锚16/16通过，全库公式/量额/最新归一零错配。','通过本地运营验证；不是独立外部复权证明。'),
('RPS审计','排名是否被旧文件或分母污染？','RPS50/120/250当日横截面独立重算，最大差均为0。','通过；有效分母5185/5160/5114，不先删除短历史股改变排名。'),
('完整性审计','30只就是全部命中吗？','完整A45只，日报展示前30；整合G2用top-n=1000获得实际50只。','纠正展示截断；不同池不能相加。'),
('启动定义','“刚起来”真是首次吗？','A45只中38只较前日新信号，7只连续。华天、顺络为连续信号。','不能把连续信号或前日未中当历史首次。'),
('回踩定义','B是真正第一次回踩吗？','节能风电为首次满足全部AMBUSH条件，但9/11起已有多次触线。','首个合格信号1只；严格首次触线0只，且该股RPS120约14。'),
('时间因果','今晚的美股消息能解释白天A股涨幅吗？','A股9/21收盘早于本次美股交易；周末新闻有部分先被消化的可能。','不得倒置因果；今晚增量应在9/22验证。'),
('消息真实性','CPU暴涨是否只是单股传闻？','同一时段INTC/AMD/ARM均大涨，明显强于SOXX。','行情方向已证实，但仍为盘中快照，不是收盘。'),
('需求机制','Agent会不会只利好GPU？','AMD官方说明检索、工具调用、代码执行等CPU工作负载。','CPU与GPU协同，不是二选一，也没有统一固定配比。'),
('供给/技术反方','“只满足50%”和跑分能否直接算收入？','50%来自CTech转述管理层谈话；AMD基准含内部测试、配置差异和估计。','支持需求讨论，不能据此证明全球缺口50%、利润翻倍或公平独立跑分。'),
('订单穿透','哪些是真业务，哪些是热词联想？','通富AMD关系、澜起内存接口、深南载板、沪电通讯PCB有公开依据。','具体新品订单、份额、增量利润未逐项确认。'),
('国产替代反方','海外CPU缺货必然转单海光龙芯吗？','产品架构、适配、供给、采购与出口限制都会影响选择。','替代是情景，不是已拿到海外订单；不杜撰AMD最新IP授权。'),
('市场广度','是全面恢复还是局部热闹？','上涨81%，MA20宽度56.1%；同口径成交额约较9/18下降2.2%。','修复扩散，量能未同步放大；不能仅凭指数或美股宣布全面主升。'),
('行业轮动反方','半导体真是今天第一行业吗？','半导体第5，宽度86.8%；第一酿酒小组被41.1%单股占额否决。','G3未过；不能用海外CPU故事更改当天行业排名。'),
('技术位置','直接映射是否已形成右侧趋势？','海光246.45<MA60 283.11；澜起210.10<217.54；通富61.18<64.45。','逻辑更直接不等于形态更合格；均需价格确认。'),
('追高与相关性','能否买一篮子科技股分散风险？','华天乖离11.8%、聚辰14.7%、兴森16.9%；CPU/PCB/封装同受AI预期驱动。','禁止假分散与开盘追热；现有持仓需一并计入风险。'),
('赔率压力测试','窄止损能否凑出2R？','深南观察区上沿的压力预算约1.16R，沪电约0.33R，华天约0.25R。','均未达2R；0.6涨跌幅风险下限是未验证保守参数，不代表已证明最优。'),
('期限与T+1','持到月底有多少时间兑现？','9/22、23、24、28、29、30共6个交易日；中秋与国庆形成持仓间隔。','9/30新买当天不可卖，不适合月底平仓任务；失效必须提前退出。'),
('历史反例','右侧条件能否保证短持盈利？','通富8/18满足本次检查的形态及数值条件，下一根开盘至第6根收盘为负收益。','存在亏损反例；并非完整六门回测，不能估计本次胜率，更不能保证盈利。'),
('账户与证伪','二十种说法能不能替代风险预算？','缺权益、仓位、成本、可卖量、现金与风险预算，G5未过；20项检验也非20次独立实验。','结论必须允许“不买”和提前退出，拒绝把愿望当成统计证明。')]
checktable=table(['轮次 / 观点','反向问题','证据或检验','裁决'],[[f'<span class="auditnum">{i:02}</span><br>{x[0]}',*x[1:]] for i,x in enumerate(checks,1)])
exampletable=table(['股票','形态出现日','下一根开盘日','第6根收盘日','区间毛收益'],[[r['name'],r['signal'],r['entry_date'],r['exit_date'],pct(r['gross_return_pct'])] for r in d['counterexamples']])
sources=[
('AMD官方9/18：Agent工作流与Venice平台，注意测试脚注','https://newsroom.amd.com/news/amd-epyc-cpus-deliver-every-layer-agentic-ai-stack/'),
('CTech 9/20：转述Intel CEO在Splunk大会的供给与需求谈话','https://www.calcalistech.com/ctechnews/article/6bjuvbxxf'),
('每日经济新闻9/21 22:25：当晚美股芯片上涨报道','https://www.mrjjxw.com/articles/2026-09-21/4587867.html'),
('通富微电2026半年报：AMD客户及封测业务依据','https://static.cninfo.com.cn/finalpage/2026-08-29/1225527099.PDF'),
('澜起科技官网：内存接口产品与验证说明','https://web.montage-tech.com/Memory_Interface'),
('海光官网：CPU业务依据，网页旧型号不代表最新性能','https://www.hygon.cn/product/cpu'),
('龙芯官网：服务器CPU业务依据','https://www.loongson.cn/application/list?id=3'),
('深南电路2026半年报：封装基板与FC-BGA业务','https://static.cninfo.com.cn/finalpage/2026-08-27/1225512708.PDF'),
('沪电股份2026半年报：数据通讯应用','https://static.cninfo.com.cn/finalpage/2026-08-26/1225502252.PDF'),
('华天科技2025年报（2026-03-31披露）：先进封装战略，不是盈利承诺','https://static.cninfo.com.cn/finalpage/2026-03-31/1225059982.PDF'),
('上交所9/17公告：中秋、国庆休市安排','https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml')]
sourcehtml=''.join(f'<li><a href="{u}">{E(t)}</a></li>' for t,u in sources)
oldnames=['拓荆科技','芯源微','江丰电子','力芯微','纳芯微','百普赛斯','义翘神州','药康生物','美迪西','皓元医药','药石科技']
oldtab=table(['原观察股','收盘 / 当日','MA20 / MA60','乖离','今晚复核'],[[x,n(by[x]['close'])+' / '+pct(by[x]['ret1']),n(by[x]['ma20'])+' / '+n(by[x]['ma60']),pct(by[x]['bias']),by[x]['phase']+('；不是CPU消息的直接利润映射' if x in oldnames[5:] else '；不因消息忽略均线位置')] for x in oldnames])
stamp=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')
body=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>0921右侧选股｜CPU重估映射与20项反证</title><style>{style}</style></head><body><main>
<header><div class="kicker">LOCAL DATA × CPU RE-RATING × 20 ADVERSARIAL CHECKS</div><h1>把想象力用在研究上，<br>把买单交给证据。</h1><p>2026年9月21日晚：本地TDX当日K线已更新，G0/G1重算通过；45只A候选、1只原始B。CPU的强叙事与A股的成熟趋势，目前并未完全重合。</p><p class="decision">当前：观察，不可执行｜不保证持有至9月30日盈利</p><p>重点对照：<b>深南电路002916、沪电股份002463、华天科技002185</b>看结构；<b>通富微电002156、澜起科技688008、海光信息688041</b>看消息与后续价格确认。不是六只同时买入。</p><p class="muted">生成 {stamp}；A股数据截至9/21收盘，海外行情快照约北京时间22:58（美股盘中，未收盘）。沪深A股，不含北交所。TDX原始价格锚；指标为本地同花顺 v_daily_qfq。G0仅本地运营一致性，非独立外部证明。</p><div class="warning" style="color:#543d16"><b>唯一主计划：</b>不预埋消息买单。先等G3主线许可及G5账户风险资料，再评估第一行业内实际触发且≥2R的唯一标的。现有G3/G5未过，新增授权0；35%仅为未验证情绪模型的参考上限，不是建仓建议。区间失效、跳空超预算、主线不成立即取消。若未来建立新仓，T+1允许后才可卖出；止损价不保证成交。</div></header>
<nav><a href="#result">今日结果</a><a href="#news">美股原因</a><a href="#map">最强叙事与边界</a><a href="#focus">核心观察</a><a href="#challenge">20项检验</a><a href="#plan">月底持有计划</a><a href="#audit">完整名单与审计</a></nav>
<div class="grid"><article class="card third"><small>A完整 / 数值门通过</small><div class="metric">45 / 8</div><p>日报只展示A前30；38个较前日新增、7个连续。数值门通过不等于完整G2或买入许可。</p></article><article class="card third"><small>B：两种“首次”</small><div class="metric">1 / 0</div><p>节能风电首次满足全部条件1只；严格首次进入均线区0只。不能混用口径。</p></article><article class="card third"><small>情绪 / 时间窗口</small><div class="metric">93.2 / 6日</div><p>量化标签“主升”、formal=false，风险上限仍降至35%。宏观状态未覆盖完整指数证据；盘面过程推断为修复扩散。</p></article></div>
<section class="card" id="result"><h2>01｜今天到底选出了什么</h2><p>用户ZZ1234原公式复刻命中261只；V2策略A经角色和成交额过滤后45只；行业G2池50只。这是三套不同口径，不能相加，也不能把原公式261只全叫“可买”。以下8只满足A池的数值条件，但仍需行业与其他门控。</p>{atab}<p>其中顺络、达利凯普在前3行业，华天虽然属于封测且有A信号，半导体行业今天排第5，不能包装成完整G2已经通过。乔锋智能属于不同方向，不为了CPU故事强行建立供应关系。</p><div class="warning"><b>B口径：</b>节能风电601016，收盘3.39、RPS120约13.95；TURN为9/10，年龄7根K线。本次首次符合全部AMBUSH条件，但9/11、14、15、16、17、18已触及MA20上方2%区间。它既不是CPU映射，也不是严格首次触线，更未过RPS门。</div></section>
<section class="card" id="news"><h2>02｜今晚CPU暴涨：哪些证据成立</h2>{table(['标的','快照价格·美元','相对前收盘','北京时间'],[['Intel / INTC','121.75','+12.11%','22:57:54'],['AMD','613.50','+9.59%','22:58:00'],['Arm / ARM','314.00','+13.93%','22:58:50'],['SOXX','551.31','+3.42%','22:57:47']])}<p class="muted">行情来自本次金融行情工具，查询当晚；保留交易时间，不滚动冒充收盘。新闻交叉验证只支持相应发布时刻的行情方向与涨幅，不能把不同时间价格拼成同一快照。<a href="https://www.mrjjxw.com/articles/2026-09-21/4587867.html">22:25报道</a></p>
<div class="grid"><article class="card third fact"><h3>已确认：需求叙事有公司信息</h3><p>AMD于9/18说明Agent工作流中检索、工具调用及代码执行等CPU角色，并介绍Venice。这里的技术依据来自厂商披露，不是独立性能鉴定。<a href="https://newsroom.amd.com/news/amd-epyc-cpus-deliver-every-layer-agentic-ai-stack/">AMD原文</a></p></article><article class="card third infer"><h3>较可信线索：供给紧张</h3><p>CTech 9/20报道Intel CEO在Splunk大会谈到CPU需求旺盛、只能满足约一半客户需求。这是管理层表述的媒体转述，不能精确等价为整个行业缺口50%。<a href="https://www.calcalistech.com/ctechnews/article/6bjuvbxxf">报道</a></p></article><article class="card third hypo"><h3>市场解释：CPU地位重估</h3><p>推断：资金在交易Agent带动通用计算、内存和系统配套需求。CPU股强于SOXX支持这一解释，但不能分解出各消息对涨幅的因果贡献；广泛风险偏好回升也可能助推。</p></article></div><p><b>不采用的推导：</b>CPU取代GPU、GPU与CPU存在统一1:1固定采购比例、所有PC处理器都短缺、所有CPU价格都已宣布上涨、海外订单已经转给A股公司。Intel潜在合作传闻、涨价传闻与一年以前的英伟达投资消息也不能冒充今晚新公告。</p></section>
<section class="card" id="map"><h2>03｜把炒作想象推到上限，但不把推演写成事实</h2><p><b>最强叙事版本（纯情景）：</b>市场可能从“训练买GPU”，扩展到“Agent运行需要CPU、内存、互连及系统配套”；继而炒国产CPU替代、先进封装、载板、材料，最后扩散至服务器和散热。该链条可以推动估值联想，却不能证明公司订单同速增加，更不能证明6个交易日内股价必然兑现。</p>{maptable}<p>这是按公开业务搭建的人工研究分组，不是已验证概念成分，不进入生产行业排序或情绪打分。最强行情时外圈可能补涨；最弱行情时外圈往往先失去承接。必须同时保留这两个情景。</p><div class="warning">最值得比较的不是“名字最像CPU”的公司，而是<strong>业务关联、价格结构、赔率</strong>三者的交集。通富业务关联较直接但趋势未恢复；深南结构较好但属于间接传导——两者各有短板。</div></section>
<section class="card" id="focus"><h2>04｜六只核心观察，分清楚“想买”与“能买”</h2>{coretable}<p>同方向次级对照：景旺电子110.16，MA20约100.68，结构较完整但乖离9.4%；生益科技146.27，接近MA20约144.70，但没有今天A/B启动信号。龙芯中科RPS120仅20.6，不能只因名称最符合CPU就排成趋势首选。</p><p><b>今晚不授权的原因：</b>半导体位于第5行业；第一行业仍因集中度被否决；核心候选当前近端赔率未满足现有2R要求；账户资料未提供。原规则允许零交易，但这一行业排序结构可能过于保守，应离线校准，不在新闻刺激下临时修改。</p><details><summary>上一轮设备/材料/创新药观察股，今天如何变化</summary>{oldtab}<p>江丰只比MA60高约0.02元，不能称为稳固支撑；芯源MA20继续下行。百普MA20乖离约30.6%，药康约12.9%，不能拿昨日观察名单直接买。医药按自身业务与行业管理，不与CPU消息强行绑定。</p></details></section>
<section class="card"><h2>05｜环境：修复扩散，而不是所有股票都在新主升</h2><p>本地5208只有效样本：上涨81.0%，收益中位数+1.51%，MA20宽度56.1%、MA60宽度62.1%。同口径库成交额20245.9亿元，较9/18的20701.3亿元下降约2.2%。TDX生态另一个样本口径：涨停104、跌停3、炸板29、炸板率21.8%、高度4；不可把两套成交额混算。</p>{inds}<p>半导体宽度高、近5日相对强，但行业第5；酿酒第1仅9个有效成分，并非整个白酒板块，最大股成交占41.1%。当前生产规则不自动递补被否决的第一行业，G3失败。没有分钟承接数据，不使用估算主力净流入；宏观指数级结论降级。</p></section>
<section class="card" id="challenge"><h2>06｜20轮不同观点检验：允许推翻，而不是强行自证</h2><p>以下是20项可追溯的审查记录，不是20个独立模型、20轮回测或20次独立证据。通过数据审计不等于通过收益证明；同一条Agent新闻换20种说法，也不会提高真实胜率。</p>{checktable}<h3 style="margin-top:20px">一个足以否定“全部盈利”的历史反例</h3>{exampletable}<p class="muted">仅在当前CPU研究池内回看2025-09-01至2026-09-11的YJ/QD及价格/RPS数值条件，取下一根开盘到第6根收盘的前复权毛收益，展示负样本；没有模拟止损或完整六门。股票自身K线间隔也不保证均为连续市场交易日。当前名单有选择/幸存者偏差，未模拟限价、停牌、手续费、滑点，不能当成策略回测或本次预期收益。这只是说明“同类右侧信号也会亏”，不证明完整系统的胜率。</p></section>
<section class="card" id="plan"><h2>07｜到9月底：把“持有期限”改成带退出条件的计划</h2><div class="timeline">{''.join('<div class="day">9/'+x+'</div>' for x in ['22','23','24','28','29','30'])}</div><p>上交所公告确认9/25–27中秋休市，10/1–7国庆休市。若9/22建立仓位，到9/30只有6个交易日；这是短持事件窗口，不足以验证数年的产业趋势。<a href="https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml">交易所公告</a></p>
<div class="grid"><article class="card half"><h3>空仓：唯一执行顺序</h3><ol><li>9/22先观察开盘是否兑现过度；不因为美股高开后继续涨就提高预定风险。</li><li>重新确认第一行业、个股结构、买点与账户预算。缺任一项，观察而不下单。</li><li>若未来出现合法回踩，按当时均线和新平台重算；本报告静态区间不是自动成交指令。</li><li>若第一天没有机会，允许空仓到月底；不为完成“月底盈利”任务降低门槛。</li></ol></article><article class="card half"><h3>已有仓 / 重仓：先处理暴露</h3><p>已有趋势仓按原硬失效位管理；收盘破MA20触发减仓，连续两日未收复或收盘破MA60触发退出，在下一可交易时段执行。不得把止损向下移动以便熬到月底。</p><p>若买入理由只是消息情绪，其有效观察窗口更短；三日未创新高应按情绪账本退出，不强行转长期持有。新买T+1不可当天卖，跌停及跳空可使计划止损失效。</p><p>重仓账户先统计CPU/封装/PCB的合计相关风险；具体股数需持仓成本、权益、现金和可卖量，当前不猜测。</p></article></div>
<p><b>日期检查点：</b>9/24检查是否承担中秋隔夜风险，不满足持仓依据则降低风险；9/28检查假期后结构是否延续；9/30如果按本次“月底结束”的研究窗口评估，按实际可成交价格结算，不因浮亏无限延长。9/30不新开需当日平仓的股票仓位。</p>
<h3>风险和赔率口径</h3><div class="formula">D=max(入场−结构线, 入场×涨跌幅限制×0.6, ATR20)<br>压力预算RR=(最近可见高点−入场) / D</div><p>静态观察区为max(MA20,MA60)上方约0.3%–2%，上限不高于9/21收盘；不是回测最优区间。最近高点以当日、此前20/60日高点中在区间上方最近者作粗粒度代理。该算法可能低估突破后的趋势空间，但不能反过来拿远高点保证价格目标。费用未扣、实际交易赔率更差。</p><p>例：深南观察上沿378.22，结构370.80，计划距离7.42、ATR18.84、压力下限22.69，取D22.69；近高404.50，约1.16R。0.6涨跌幅下限未经过样本外验证，不是预计必跌幅度。当前既未过门控也未达2R，不生成仓位。</p><p><b>四类风险：</b>今晚美股冲高回落；国内供应或软件适配限制转单；高开透支并放大T+1风险；节假日与月底流动性变化。<b>机会成本：</b>不追可能错过首日跳空和连续上涨，幅度无法可靠预测；接受错过好于用“保证月底盈利”替代风险预算。</p></section>
<section class="card" id="audit"><h2>08｜全量结果、数据口径与复查</h2><details><summary>展开45只完整A候选</summary>{fulla}</details><details><summary>展开26只CPU及系统扩散研究对象</summary>{cpulist}</details><p>本次研究合并原池、CPU业务组、全市场A/B及行业G2池，共158只去重股票核对当日TDX原始OHLC，全部一致；没有将行业排名以外的CPU联想偷偷写入G2。50/120/250日RPS独立重算误差0；目标股票×日期无重复。</p><p>18:10自动任务因TDX还在9/18被阻断；23:10用户更新本地数据后，本次完整流程重新同步、校验、G0、G1、RPS、A/B成功。G0为16/16且全库一致性通过；不是把早上的失败状态强制覆盖为成功。</p><p>来源：本地通达信 .day为原始未复权锚；同花顺生产库 v_daily_qfq用于价格型指标，量额保持原始；旧名tdx_stock_daily_with_rps.parquet现为该前复权导出。G2流动性用前20日成交额中位数；A/B用其原脚本的20日均额，名称相似但口径不同。</p><p>审计文件：right_side_20260921_audit.json、integrated_decision_20260921_audit.json、cpu_right_side_20260921_research.json；完整A/B为right_side_20260921_A_enriched.csv与B_enriched.csv。生产策略未为消息临时放宽；审计辅助脚本仅增加日期参数，默认历史行为保留。</p><p>未覆盖：完整历史ST、实时竞价与分钟承接、逐股当前估值/解禁/减持公告全检、账户资料、完整样本外回测。近端高点是压力代理，不是经成交密集区验证的目标价。上述缺口不以估算资金或未经核实传闻填补。</p><h3>公开证据（本次检索日2026-09-21）</h3><ul>{sourcehtml}</ul></section><footer>研究与风险管理材料；无自动下单。积极推演不等于夸大事实，20项检验不等于20次独立实验，更不能保证短期收益。最终任务是提高决策质量并约束损失，而不是强迫市场在9月底给出盈利。</footer></main></body></html>'''
path=O/'a_share_analysis_20260921_cpu_20_checks.html'
assert len(checks)==20 and len(a)==45 and len(nums)==8 and len(allcpu)==26
assert 'NaN' not in body and 'Infinity' not in body and '<script src=' not in body
path.write_text(body,encoding='utf-8')
# Preserve the runner's original raw counts, but expose the audited distinctions in the unique journal.
j=O/'a_share_analysis_daily_dual_strategy_journal.html'; page=j.read_text(encoding='utf-8')
assert '数据截至 2026-09-21' in page
notice='<aside style="margin:18px;padding:18px;background:#fff5db;border:2px solid #ad7617;border-radius:12px"><b>9/21晚间完整复核：</b>A完整45只（本页展示30），数值条件通过8只；38新增/7连续。B原始1只节能风电，首次合格1只、严格首次触线0只。G0/G1已通过，G3/G5仍未过；不得下单。<a href="a_share_analysis_20260921_cpu_20_checks.html">查看CPU映射及20项反证报告</a></aside>'
if '9/21晚间完整复核' not in page: j.write_text(page.replace('<body>','<body>'+notice,1),encoding='utf-8')
print(json.dumps({'report':str(path),'A':len(a),'numeric_G2':len(nums),'raw_B':1,'first_qualified_B':1,'first_touch_B':0,'checks':len(checks),'research_rows':len(d['rows']),'bytes':path.stat().st_size},ensure_ascii=False))
