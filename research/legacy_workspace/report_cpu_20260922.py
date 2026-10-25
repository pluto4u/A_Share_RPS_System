"""Evidence-linked close review; no production rules or orders are changed."""
import json,html
from pathlib import Path
from datetime import datetime
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];O=ROOT/'output'
d=json.loads((O/'cpu_right_side_20260922_research.json').read_text(encoding='utf-8')); rows={r['code']:r for r in d['rows']}
A=pd.read_csv(O/'right_side_20260922_A_enriched.csv'); aud=d['screen_audit']
def e(v): return html.escape(str(v))
def f(v,n=2): return '未取得' if v is None else f'{v:.{n}f}'
def table(head,body): return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(x)+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(x)+'</td>' for x in r)+'</tr>' for r in body)+'</tbody></table></div>'
def link(label,url): return f'<a href="{url}">{label}</a>'
src={
'meta':('Meta：Muse的云虚拟机架构，9月8日','https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse'),
'amd':('AMD：Agentic AI中的EPYC，9月18日','https://newsroom.amd.com/news/amd-epyc-cpus-deliver-every-layer-agentic-ai-stack/'),
'yesterday':('新浪：美国9月21日收盘，9月22日04:53','https://finance.sina.com.cn/world/2026-09-22/doc-inisrsvv2483208.shtml'),
'tf':('通富微电2026半年报，第10–13页附近','https://static.cninfo.com.cn/finalpage/2026-08-29/1225527099.PDF'),
'montage':('澜起官方内存接口产品','https://www.montage-tech.com/Memory_Interface'),
'sn':('深南电路2026半年报','https://static.cninfo.com.cn/finalpage/2026-08-27/1225512708.PDF'),
'hygon':('海光官方CPU产品','https://www.hygon.cn/product/cpu'),
'rock':('瑞芯微官方产品','https://www.rock-chips.com/'),
'hh':('华虹宏力2026半年报','https://static.cninfo.com.cn/finalpage/2026-08-27/1225514149.PDF'),
'holiday':('上交所中秋与国庆休市公告，9月17日','https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml')}
def S(k): return link(*src[k])
checks=[
('日期审计','TDX严格沪深A股前缀：5328个文件，5208个最新为9/22；最初粗扫5245包含非目标文件，已纠正。','通过；其余旧日期不冒充当日'),
('复权与原始锚','G0样本16/16，全库公式、量额、最新归一零错配；133只研究池当日OHLC逐只对齐TDX。','通过本地运营一致性；非独立外部证明'),
('RPS独立重算','50/120/250有效分母5184/5161/5114，与面板RPS最大差均为0。','通过；RPS不是胜率'),
('全量与展示上限','A完整32，日报只展示30；A的RPS120≥80为16只，数值G2为8只。','已标清；不可把展示30当完整命中'),
('公式是否混用','原始ZZ1234为199，V2 A为32，G2为35，三者不同规则、不可相加。','通过分开输出；并非筛选结果互相矛盾'),
('启动是否真正首次','A31只较前日新增，1只连续；不等于31只历史首次启动。','限定结论；不夸大先手程度'),
('回踩是否首次','B今日0；源AMBUSH条件没有强制首次去重，之前审计已区分首次合格与首次触碰。','今日无歧义；策略文字与代码存在待修订缺口'),
('美股时间反证','昨日INTC/AMD/ARM约+12/+10/+17%；今晚23:38快照约+0.21/+0.61/+1.32%。','否决“今晚再次集体暴涨”前提'),
('需求链是否真实','Muse官方有CPU/内存/工具执行；AMD官方讨论Agent负载，证据支持CPU需求叙事。','部分支持；虚拟机不等于独占物理CPU，性能宣传非新增订单'),
('映射是否直接','通富有AMD业务证据；澜起有内存接口产品；国产替代、PCB、材料为不同强度传导。','不能把同一涨幅或盈利弹性分配给全产业链'),
('市场宽度反证','上涨比例81%→43.8%，日收益中位−0.17%；涨停104→64，炸板率21.8%→30.4%。','不是普涨确认；总成交额增加不代表全部买方占优'),
('行业与标的交集','半导体由第5升第3；第一为房地产，G2完整35只但第一行业交集0。','G3通过不等于这些股票可执行；不临时换行业排序'),
('追高的同日检验','澜起开盘到收盘−3.55%，通富−1.52%，深南−2.44%，海光−3.40%。','否决“消息强+股价涨=追入也赚钱”；非可成交收益回测'),
('均线结构反证','澜起站上MA60且A命中；通富仍在MA60下，海光与华虹差距更大。','澜起相对升级，其他不能一并升为趋势买点'),
('放量是否等于吸筹','通富量比约2.05、海光约2.19，同时有高开回落；华天量比1.86收跌。','日K无法识别吸筹或出货；等待修复，不编造资金流'),
('热度与空间','澜起距MA20+11.29%，瑞芯微+15.61%，聚辰约+24%；涨停股未必有次日可买容量。','强势可以成立，追价资格仍可否决'),
('2R保守检验','澜起示例218元、失效216.09：计划1.91，ATR10.48，20%×0.6风险下限26.16；到233.19约0.58R。','不满足2R；12%下限是未验证风控启发式，不是预测跌幅或等到跌12%才卖'),
('阈值敏感性','固定RPS120≥80，A中MA60之上、乖离≤8/12/15%对应2/8/9只；RPS≥90且乖离≤12%为4只。','名单依赖参数；不能为凑股票调参后宣称稳健'),
('实现与说明一致性','源情绪代码额外允许ST≥85且不升时单项触发高潮，文档主要写至少两项；日报生态原值55%需应用formal=false后降为35%。','展示已纠正；代码/文档分歧记录待离线校准，不临时修改风险参数'),
('期限、反例与账户','9/23、24、28、29、30仅5个交易日；相似筛选历史存在5根K线负收益，且账户/G5缺失。','否决“20轮自证能保证月底全盈利”；无确定仓位与买单')]
assert len(checks)==20
checktable=table(['检验','证据 / 反证','结论'],[(f'{i:02d} · {a}',b,c) for i,(a,b,c) in enumerate(checks,1)])
core=['688008.SH','002156.SZ','002916.SZ','002463.SZ','002185.SZ','688041.SH','688347.SH','603893.SH']
plans={
'688008.SH':('第一观察：已修复MA60','216–220附近缩量承接后重新转强；不是挂单区','跌回动态MA60约216.09且不能收复，撤销该修复假设','先看230–233；当前保守赔率不达标'),
'002156.SZ':('第二观察：业务直接，结构未过','重新站稳64.25附近，再看65.9–66.1突破质量','再失62.83且不能修复，短线转弱；原止损优先','未站MA60，不抢跑'),
'002916.SZ':('结构候补：长上影需消化','维持373.75附近结构；原375–381研究区不抬高追价','跌破动态MA20，按趋势账本降险','405.55、422.88为不同层次压力'),
'002463.SZ':('结构候补：近MA20','123–125附近承接需确认；今日B没有命中','动态MA20约123.18失守则取消回踩假设','129.94–131.25压力近，非高赔率默认买点'),
'002185.SZ':('降级：放量阴线、贴近MA60','先守18.14附近，再观察收复18.55质量','收盘失守MA60，趋势结构失效','18.95压力，量价不能解释为已洗完'),
'688041.SH':('国产CPU情绪观察','先看269.74反压，再看动态MA60约281.21','跌破254.60且无法修复，取消短线承接假设','买在265开盘者仍亏，不能把收涨当赢'),
'688347.SH':('降级：利好下未站稳MA20','先收复232.55，再看240.44–240.46','失守229.63取消短线止跌假设','不是先进服务器CPU订单映射；MA60仍272.36'),
'603893.SH':('强势观察：不追涨停','等未来缩量整理及行业确认，不预设深回踩必到','不追次日大幅跳空，触发原失效位则退出','乖离约15.6%；涨停强度非买入赔率')}
coretable=table(['股票 / 观察层','9/22收盘 / 当日','MA20 / MA60','RPS120 / 乖离','A / G2','研究条件与取消'],[(e(rows[c]['name'])+'<br>'+c+'<br>'+plans[c][0],f(rows[c]['close'])+f'<br>{rows[c]["ret1"]:+.2f}%',f(rows[c]['ma20'])+' / '+f(rows[c]['ma60']),f(rows[c]['rps120'])+' / '+f(rows[c]['bias'])+'%',('是' if rows[c]['A'] else '否')+' / '+('是，仅次行业' if rows[c]['g2'] else '否'),plans[c][1]+'<br><b>失效：</b>'+plans[c][2]+'<br>'+plans[c][3]) for c in core])
gaps=table(['股票','较前收盘跳空','收盘日涨跌','开盘至收盘','日高→收盘'],[(e(rows[c]['name']),f'{rows[c]["gap_pct"]:+.2f}%',f'{rows[c]["ret1"]:+.2f}%',f'{rows[c]["intraday_pct"]:+.2f}%',f'{(rows[c]["close"]/rows[c]["high"]-1)*100:+.2f}%') for c in core])
atab=table(['代码 / 名称','阶段','收盘','RPS120','距MA20','MA60','数值G2','较前日新增'],[(r.ts_code+'<br>'+e(r['name']),e(r.role),f(r.close),f(r.RPS_120),f(r.vs_ma20_pct)+'%',f(r.ma60),'是（不含行业）' if r.g2_full else '否','是' if r.a_new_vs_previous_day else '否') for _,r in A.sort_values(['g2_full','RPS_120'],ascending=False).iterrows()])
ind=table(['排名 / 行业代码','行业','成分数','MA20宽度','RPS120中位','共振','单股驱动'],[(str(i+1)+' / '+r['industry'],e(r['name']),r['members'],f(r['breadth_ma20_pct'],1)+'%',f(r['median_rps120'],1),f(r['resonance'],1),'是' if r['single_stock_driven'] else '否') for i,r in enumerate(d['industries'][:7])])
gates=table(['门控','结果','解释'],[(r['id']+' '+e(r['name']),'通过' if r['pass'] else '未通过',e(r['detail'])) for r in d['gates']])
ex=table(['股票','信号日','次根开盘日','第5根收盘日','未计费收益'],[(r['name'],r['signal'],r['entry_date'],r['exit_date'],f(r['gross_return_pct'])+'%') for r in d['counterexamples'][:4]])
chartcodes=['688008.SH','002156.SZ','688347.SH','002463.SZ']
z=pd.read_parquet(O/'tdx_stock_daily_with_rps.parquet',filters=[('ts_code','in',chartcodes)],columns=['ts_code','trade_date','open','high','low','close','vol'])
charts=[]
for code in chartcodes:
    zz=z[z.ts_code.eq(code)].sort_values('trade_date').copy();zz['m20']=zz.close.rolling(20).mean();zz['m60']=zz.close.rolling(60).mean();b=zz.tail(60).reset_index(drop=True)
    lo=min(b.low.min(),b.m20.min(),b.m60.min())*.96;hi=max(b.high.max(),b.m20.max(),b.m60.max())*1.04
    def Y(v):return 25+(hi-v)/(hi-lo)*200
    def X(i):return 50+i*8.3
    svg=['<svg viewBox="0 0 575 325" role="img" aria-label="'+rows[code]['name']+'近60根日K及MA20、MA60和原始成交量，价格元"><rect width="575" height="325" fill="white"/>']
    for k in range(4):
        p=lo+(hi-lo)*k/3;yy=Y(p);svg.append(f'<path d="M45 {yy}H558" stroke="#e6ecf2"/><text x="2" y="{yy+4}" font-size="10">{p:.0f}</text>')
    for i,r in b.iterrows():
        color='#c33d4b' if r.close>=r.open else '#168465';xx=X(i);height=r.vol/b.vol.max()*48
        svg.append(f'<path d="M{xx} {Y(r.high)}V{Y(r.low)}" stroke="{color}"/><rect x="{xx-2.7}" y="{min(Y(r.open),Y(r.close))}" width="5.4" height="{max(1,abs(Y(r.open)-Y(r.close)))}" fill="{color}"/><rect x="{xx-2.7}" y="{290-height}" width="5.4" height="{height}" fill="{color}"/>')
    for key,col in [('m20','#b78c00'),('m60','#5868c4')]:svg.append('<polyline fill="none" stroke="'+col+'" stroke-width="1.7" points="'+' '.join(f'{X(i)},{Y(r[key])}' for i,r in b.iterrows())+'"/>')
    for i in [0,30,59]:svg.append(f'<text x="{X(i)-16}" y="311" font-size="10">{str(b.trade_date.iloc[i])[5:10]}</text>')
    svg.append('</svg>');charts.append('<article><h3>'+rows[code]['name']+' '+code+'</h3><p class="small">近60根日K至9/22；金线MA20、蓝线MA60；红涨绿跌，下栏原始量（统一比例）</p>'+''.join(svg)+'</article>')
generated=datetime.now().astimezone().isoformat(timespec='minutes')
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>9月22日右侧趋势｜CPU映射与20项正反检验</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf2f7;color:#22334a;font:16px/1.8 "Microsoft YaHei",sans-serif}}main{{max-width:1420px;padding:24px;margin:auto}}header{{background:#152941;color:white;padding:30px;border-radius:18px}}h1{{font-size:32px;margin:5px 0}}h2{{font-size:24px;margin:0 0 15px}}h3{{font-size:19px}}.action{{color:#ffdc98;font-size:22px}}section,article{{background:white;padding:25px;border-radius:15px;margin-top:20px;break-inside:avoid;min-width:0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}.stat{{background:white;padding:20px;border-radius:12px}}.stat b{{font-size:26px;display:block}}.small{{font-size:14px;color:#5b7087}}header .small{{color:#b5cbe1}}.scroll{{overflow-x:auto;max-width:100%}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid #dce4ee;vertical-align:top;text-align:left;min-width:75px}}th{{background:#edf3fa}}a{{color:#14619f}}p,td,a{{overflow-wrap:anywhere}}.warn{{padding:15px;border-left:4px solid #b47b1e;background:#fff3d9}}.tag{{color:#a0c2e3}}nav a{{display:inline-block;margin:12px 16px 0 0}}svg{{width:100%;height:auto}}.up{{color:#c33d4b}}.down{{color:#168465}}@media(max-width:760px){{main{{padding:10px}}section,article,header{{padding:17px}}.grid{{grid-template-columns:1fr}}.stats{{grid-template-columns:1fr 1fr}}h1{{font-size:26px}}}}@media print{{nav{{display:none}}body{{background:white}}header{{background:#eee;color:black}}section{{border:1px solid #ddd}}}}</style><main>
<header><div class="tag">沪深A股 · 2026-09-22收盘 · 本地TDX锚 / 已验证本地前复权</div><h1>有右侧信号，但没有“月底全赢”的证据</h1><p class="action">优先研究澜起的回踩确认、通富的均线修复；不追高开。当前统一动作：观察，不可执行。</p><p>G0–G4通过，G2完整35只与第一行业交集为0，G5缺账户信息。不是只有“缺账户”一个问题；不能补账户后自动把半导体次行业当可买第一行业。</p><p>唯一主观察：澜起216–220附近未来缩量承接、重新转强；跌回动态MA60约216.09且不能修复则取消。此区间没有2R证明，不是挂单建议。首仓/最大单股仓位不生成，缺账户、有效买点与订单级风控。</p><p class="small">生成 {generated}。A股截至9/22收盘；美股冻结23:37–23:38北京时间盘中快照，不是今晚收盘。宏观状态缺完整指数证据，不强行分类；盘面推断为分歧扩散。量化标签高潮，ST_cycle=88.4、formal=false；参考上限35%，不是目标仓位。缺当日分钟历史与账户，T+1不能当日卖新仓。数据置信度较高、产业推断中等、短期盈利承诺不成立。</p></header>
<nav><a href="#result">选股</a><a href="#us">美股与映射</a><a href="#market">市场</a><a href="#core">重点股</a><a href="#checks">20项检验</a><a href="#month">月底计划</a><a href="#all">全名单</a></nav>
<div class="stats"><div class="stat">A完整命中<b>32只</b><small>31新增 / 1连续；仅与前日比较</small></div><div class="stat">B回踩信号<b>0只</b><small>不是数据故障导致的空名单</small></div><div class="stat">原始ZZ1234<b>199只</b><small>不能与V2或G2相加</small></div><div class="stat">G2研究池<b>35只</b><small>第一行业交集0；无可执行单</small></div></div>
<section id="result"><h2>一、从18:10阻断到本次通过：发生了什么</h2><p>傍晚同花顺库已是9/22、TDX仍为9/21，所以零交易正确。本次TDX更新后重新执行增量同步、库校验、G0、G1、RPS、A/B，再执行六门与独立RPS复核；没有使用skip-sync，没有初始化数据库或绕过门控。</p><p>A中RPS120≥80共16只；进一步满足MA60、乖离等数值条件为8只：澜起科技、科瑞技术、陇神戎发、光华科技、米奥会展、奕瑞科技、时空科技、国民技术。行业仍须单独检查，不是8张买单。A完整32，下方全列；唯一日报原有前30展示上限已标清。</p>{gates}<p class="warn">为什么还不是买单：房地产细分行业排名第一，但没有个股同时进入G2；半导体只是第三。该交集限制可能过严，需独立回测，不因今晚故事临时取消。</p></section>
<section id="us"><h2>二、先纠正前提：今晚不是第二次集体暴涨</h2>{table(['标的','美国9/21收盘约涨幅','9/22北京时间23:38附近盘中','含义'],[('Intel','+12.1%','122.04美元 / +0.21%','大涨后震荡'),('AMD','+10.0%','619.27美元 / +0.61%','小幅延续，不是再涨10%'),('Arm','+17.2%','327.17美元 / +1.32%','较23:23的+3.01%有所回落'),('SOXX ETF','本页不混用SOX指数涨幅','565.80美元 / +1.15%','板块仍强，但涨幅分化')])}<p>{S('yesterday')}；今晚值来自web.finance带UTC时间戳的快照：INTC15:38:17、AMD15:38:29、ARM15:38:30、SOXX15:37:57。不能把新闻标题中的“9月22日凌晨”误读为“9月22日晚”。</p><h3>事实：Agent增加的不只是GPU计算</h3><p>Muse的官方架构包含每用户云虚拟机、CPU、内存及工具执行；AMD也强调CPU对Agent工作流的支持。GPU没有因此消失，虚拟机也不等于每用户独占一颗物理CPU。{S('meta')}；{S('amd')}。</p><h3>把想象力用在情景，而不是虚构订单</h3>{table(['传导层','最积极的炒作假设','A股研究映射','必须跨过的事实边界'],[('核心计算','从GPU单一叙事扩展至CPU+内存资源重估','通富微电、澜起科技；海光、龙芯为国产替代联想','通富有AMD合作证据，澜起有配套产品；海光并非因此直接获得海外订单'),('封装与互连','计算平台扩张带动封测、基板、PCB升级','华天、长电、深南、兴森、沪电','必须分清产品结构与客户认证；不能宣布所有环节同幅增长'),('端侧与制造','Agent从云向端扩散，终端SoC与特色工艺被重估','瑞芯微、华虹、中芯','边缘SoC、特色代工不等于高端服务器CPU；本次不新增未核实供应商关系'),('外围扩散','存储、散热、光互连、材料轮番补涨','聚辰、英维克、高澜、联瑞及既有光链','这是条件性研究扩散，不是量化概念成分池；今日不少个股已收跌')])}<p>业务证据：{S('tf')}说明AMD客户关系；{S('montage')}列示RCD/MRCD/MDB等；{S('sn')}披露基板订单与量产进展；{S('hygon')}、{S('rock')}、{S('hh')}用于核对各自产品边界。没有查到今晚新公告证明上述股票统一取得CPU订单。</p><p class="warn">乐观情景：海外需求预期维持 → A股核心经历首次分歧后修复 → 次级链条扩散。反向情景：已高开兑现 + 板块宽度恶化 + 高位卖压 → 扩散成为兑现出口。只有前者被价格确认，才值得继续评估；不把情景写成预测收益率。</p></section>
<section id="market"><h2>三、芯片热，不等于普涨</h2><p>同口径市场成交额由20245.9升至21330.3亿元，约+5.4%；上涨比例由81%降至43.8%，收益中位−0.17%。站上MA20/60比例为56.2%/62.7%。另一个TDX生态口径成交额20946.3亿元，不能混算两套金额。</p><p>TDX涨停104→64、跌停3→6、炸板率21.8%→30.4%，昨日涨停平均收盘溢价3.29%→2.43%。ST_cycle从93.2降为88.4，3日斜率+16不代表当日上升，模型标签为高潮，未完成样本外验证。</p>{ind}<p>行业是TDX细分口径，同名房地产分属不同代码，不是全部地产总排名；只有行业排序，没有可复现概念成分或估算资金流。本次半导体第3，元器件由第3落至第5，不能仍用昨日G2资格。</p></section>
<section id="core"><h2>四、8只核心观察：名单不等于买单</h2>{coretable}<h3>今天已经出现的“追涨反例”</h3>{gaps}<p>开盘到收盘仅为日K价格路径，不假设开盘必能成交，不计费用，不是实际回测。澜起日涨5.58%与开盘买入后账面浮亏3.55%可以同时成立；中国长城也曾开盘接近涨停后回落。T+1令当天追入者无法当天纠错。</p></section>
<div class="grid">{''.join(charts)}</div>
<section id="checks"><h2>五、20项不同观点的证据检验</h2><p>这是20个独立视角的审计摘要，不是20次独立统计实验或20轮回测。结论允许否决，不能用反复论证制造盈利确定性。</p>{checktable}<h3>历史负收益反例（不是完整策略绩效）</h3>{ex}<p class="small">取当前CPU研究池回看2025/9/1至2026/9/14：YJ或QD、站MA60、RPS120≥80、乖离≤12%、前20日成交额中位≥1.5亿元；次根开盘至第5根收盘。只列各股负收益极端例以检验“必赚”，不估计胜率。存在当前股票池选择偏差；未模拟行业门、账户门、止损、停牌、涨跌停成交、费用与组合容量。5根个股日K也不保证严格5个市场交易日。</p></section>
<section id="month"><h2>六、9月底是复盘期限，不是保本期限</h2><p>若9月23日才买，到9月30日仅有23、24、28、29、30五个交易日；25–27休市，10月1–7继续休市。{S('holiday')}。</p><ul><li>9/23：只观察预设池的承接与修复，不为错过9/22高开而补追。第一行业、结构、赔率与账户条件任何一项不满足，都不生成买单。</li><li>9/24：复核节前隔夜风险；持仓理由不成立就降险，不盲目跨假期等待解套。</li><li>9/28：休市后重新核验海外催化与A股结构，不能把节前观察当成持续有效订单。</li><li>9/29–30：若目标是月底结束交易，按实际可成交价格评估退出；9/30新买不能当天卖出，计划必须考虑T+1。</li></ul><p>已有趋势仓：原硬失效位优先；收盘破MA20按规则降险、连续两日下方退出，收盘破MA60不等待反弹；在下一可交易时点执行，涨跌停与跳空可能导致超额损失。情绪单不要临时变长期持仓。空仓保持观察，重仓先降低相关科技股合计风险，不向亏损仓摊平。</p><p>不操作可能错过第二段拉升，这是机会成本；但当前主要样本的保守2R未获证明，错过不等于应该追。不要把60日历史高点当成五日必达目标，也不把多个同链股票视为分散。</p></section>
<section id="all"><h2>七、A策略完整32只（本表不截断）</h2>{atab}<p>B=0。数值G2仅检查价格、历史、RPS与流动性相关口径，不含行业许可；生产G2使用滞后成交额中位数，与A的均值ADV并非同一指标，须以六门结果为准。RPS在A中不是统一硬筛80；研究阶段不得把低RPS标的隐藏成不存在。</p></section>
<section><h2>八、审计边界与文件</h2><p>本次研究池133只：既有跟踪池、CPU映射研究池、完整A/B、G2合并；不是沪深股票全集的逐股基本面尽调。所有当日原始OHLC已对齐TDX，RPS独立重算零差。日期、量额、复权通过不代表策略已获得稳定盈利证明。</p><p>唯一日报已经更新并纠正本次展示：A完整32/表格前30、B0，formal=false有效参考上限35而非生态原值55；这是产物说明修正，生成脚本仍有上述显示/语义问题，下次重跑需继续审计。本次没有修改生产筛选参数，没有下单。</p><p>价格：TDX C:/new_tdx/vipdoc/{'{sh,sz}'}/lday原始日K用于锚；均线/ATR/RPS/收益来自项目data/hithink/market.duckdb:v_daily_qfq派生面板。日频、人民币元、成交量原始股、成交额元；前复权为本地公司行动等比重建。G0为本地运营一致性验证，不是独立外部复权证明。沪深A股，不含北交所。</p><p>证据文件：cpu_right_side_20260922_research.json、right_side_20260922_audit.json、right_side_20260922_A_enriched.csv、integrated_decision_20260922_audit.json。未使用估算资金流、过时概念归属、分钟级资金效率、未经核实当前估值。账户未提供；没有用虚构仓位填表。</p><p>{link('唯一双策略日报','a_share_analysis_daily_dual_strategy_journal.html')} · {link('今晨盘前计划','a_share_analysis_20260922_us_close_revision.html')} · {link('昨日20项检验','a_share_analysis_20260921_cpu_20_checks.html')}</p><p>本报告用于研究与风险评估，不承诺收益。最重要的修正：消息正确、股票上涨、买入获利，是三个不同命题。</p></section></main></html>'''
assert not any(t in body for t in ['NaN','Infinity','{{PLACEHOLDER}}'])
(O/'a_share_analysis_20260922_cpu_20_checks.html').write_text(body,encoding='utf-8')
(O/'cpu_20260922_twenty_checks.json').write_text(json.dumps({'generated':generated,'asof':'2026-09-22','checks':[{'id':i,'lens':a,'evidence':b,'conclusion':c} for i,(a,b,c) in enumerate(checks,1)]},ensure_ascii=False,indent=2),encoding='utf-8')
print('Report complete; A rows',len(A),'checks',len(checks),'charts',len(charts))
