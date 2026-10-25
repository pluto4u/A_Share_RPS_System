import json
from pathlib import Path
from html import escape as E
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/'output/a_share_analysis_20260920_cxmt_integrated.json').read_text(encoding='utf-8'))
by={r['name']:r for r in d['rows']}
core=['拓荆科技','芯源微','江丰电子','力芯微','纳芯微','药康生物','美迪西','皓元医药']
notes={
'拓荆科技':'消息与价格交集最值得跟踪；仍属重新站回MA60的修复，不是完整多头。',
'芯源微':'涂胶显影工艺映射；MA20仍下行，先等斜率转正及回踩确认。',
'江丰电子':'靶材工艺映射；仅略高于MA60，支撑缓冲非常薄，不能只因消息加仓。',
'力芯微':'原观察池中均线结构较完整；不是本次DRAM制造设备受益逻辑。',
'纳芯微':'RPS较强、乖离适中；MA10仍低于MA20，作为独立模拟芯片修复观察。',
'药康生物':'研发工具链、动物模型；结构相对完整，但尚不是系统第一行业。',
'美迪西':'临床前研发服务；结构相对完整，不能据此推断AI收入高增长。',
'皓元医药':'研发分子砌块/化学服务；短均线仍在修复，先看回踩承接。'}

def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def zone(r): return f"{r['zone'][0]:.1f}–{r['zone'][1]:.1f}" if r['zone'] else '未形成有效观察区'
def n(x): return f'{x:.2f}'
def signed(x): return f'<span class="{"up" if x>=0 else "down"}">{x:+.1f}%</span>'
def tag(x): return '<span class="tag">'+E(x)+'</span>'

coretable=table(['研究优先级 / 代码','9/18收盘 / RPS120','趋势判断','静态回踩观察区 / 结构线','最近高点参考 / 压力预算RR','当前动作'],[
 [f'{i+1}. <b>{name}</b><br><small>{by[name]["code"]}</small>',f'{by[name]["close"]:.2f}<br>{by[name]["rps120"]:.1f}',notes[name],f'{zone(by[name])}<br>结构线 {by[name]["invalid"]:.2f}',f'{by[name]["pressure"]:.2f}<br>{by[name]["rr"]:.2f}R','观察，不可执行'] for i,name in enumerate(core)])
waitnames=['北方华创','中微公司','盛美上海','华海清科','长川科技','精智达','中船特气','兆易创新','江波龙','佰维存储','寒武纪']
waittable=table(['股票','收盘','MA20 / MA60','没有纳入核心买点观察的原因'],[[x,n(by[x]['close']),f"{by[x]['ma20']:.2f} / {by[x]['ma60']:.2f}",'、'.join(by[x]['blockers'])] for x in waitnames])
hotnames=['百普赛斯','义翘神州','成都先导','泓博医药','近岸蛋白','思瑞浦','中科飞测']
hottable=table(['股票 / 代码','收盘','近5日涨跌 / MA20乖离','MA10 / MA20','计划'],[[x+'<br><small>'+by[x]['code']+'</small>',n(by[x]['close']),signed(by[x]['ret5'])+' / '+signed(by[x]['bias']),f"{by[x]['ma10']:.2f} / {by[x]['ma20']:.2f}",'不追涨；等待新平台或重新通过回踩筛选'] for x in hotnames])
alltable=table(['名称 / 代码','收盘 / 5日涨跌','MA20 / MA60','RPS120 / 乖离','前20日成交额中位数·亿元','行业 / 已保存行业排名','G2 / A / B','阶段 / 未过原因'],[[f'<b>{r["name"]}</b><br><small>{r["code"]}</small>',n(r['close'])+' / '+signed(r['ret5']),n(r['ma20'])+' / '+n(r['ma60']),f"{r['rps120']:.1f} / {r['bias']:+.1f}%",f"{r['adv_median']:.2f}",r['industry']+' / '+(str(r['industry_rank']) if r['industry_rank'] else '不在已保存前12'),f"{'通过' if r['g2'] else '未过'} / {'是' if r['A'] else '否'} / {'是' if r['B'] else '否'}",r['phase']+'<br><small>'+('、'.join(r['blockers']) or 'G2通过≠最终交易许可')+'</small>'] for r in d['rows']])
industrytable=table(['行业 / 排名','5日 / 20日中位收益','站上MA20','RPS120中位','成交额/历史基准','解释'],[[x['name']+' / '+str(i+1),signed(x['ret5_pct'])+' / '+signed(x['ret20_pct']),f"{x['breadth_ma20_pct']:.1f}%",f"{x['median_rps120']:.1f}",f"{x['amount_vs_adv20']:.2f}倍",'单股成交占比47.85%，当前规则降级' if x['single_stock_driven'] else '共振较广，但现行规则仅排名第一行业可执行'] for i,x in enumerate(d['industries'][:3])])
mapping=table(['工艺事实 / 研究推断','A股工艺对应（不是订单确认）','需进一步验证'],[
['多重图形化、复杂薄膜与高深宽比结构','拓荆科技：沉积；中微公司：刻蚀；北方华创：多类工艺设备','G5对应机型是否验收、份额与订单，不能把全行业技术需求等同于其订单。'],
['曝光前后配套与良率控制','芯源微：涂胶显影；盛美上海：清洗；中科飞测：检测；华海清科：CMP','多重曝光不等于涂胶显影销售同比例倍增，更不能写成四倍订单。'],
['量产后工艺耗材与测试需求','江丰电子：靶材；中船特气：电子特气；长川科技/精智达：测试','耗用与晶圆开工、配方和认证有关；测试产品仍要核对具体DRAM覆盖与客户验收。'],
['下游存储产品','兆易创新、江波龙、佰维存储仅作对照','国产供给、采购成本、库存与售价影响可能相反，不是一律利好。']])
sources=[
('中新社，2026-09-20 15:11：G5平台量产现场报道','https://www.chinanews.com/cj/2026/09-20/10700064.shtml'),
('中安在线，2026-09-20 12:39：工艺参数现场报道','https://ah.anhuinews.com/szxw/202609/t20260920_9599704.html'),
('香港商报，2026-09-20 12:02：单颗24Gb及封装信息','https://hkcd.com/hkcdweb/content/2026/09/20/content_8776112.html'),
('拓荆科技官网，2025-09-28：薄膜设备应用于逻辑、存储制造','https://piotech.cn/index.php/News/detail?id=825'),
('北方华创官网：半导体设备产品','https://www.naura.com/product/semiconductor.html'),
('中微公司：2024年ESG报告（2025发布）','https://static.amec-inc.com/uploads/files/20250421/17451977051472.pdf'),
('芯源微官网：涂胶显影设备','https://www.kingsemi.com/index.php/Product/detail?id=221'),
('盛美上海官网：产品范围','https://www.acmrcsh.com.cn/product.html'),
('芯碁微装官网：直接成像应用，不混同前道涂胶显影','https://www.cfmee.cn/news_detail/50.html'),
('百普赛斯2025年报，2026-04-21：主营业务依据','https://static.cninfo.com.cn/finalpage/2026-04-21/1225128781.PDF'),
('义翘神州2025年报，2026-04-14：主营业务依据','https://static.cninfo.com.cn/finalpage/2026-04-14/1225098515.PDF'),
('长鑫交易所问询回复：工艺迭代与设备升级，2025-12-30','https://static.sse.com.cn/stock/disclosure/announcement/c/202512/002170_20251230_H2T8.pdf')]
links=''.join(f'<li><a href="{u}">{E(t)}</a></li>' for t,u in sources)
g2=sum(r['g2'] for r in d['rows']); ac=sum(r['A'] for r in d['rows']); bc=sum(r['B'] for r in d['rows'])
stamp=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')
html='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>长鑫G5 × 右侧趋势 × 创新药｜买卖决策研究</title><style>
:root{--ink:#172b38;--muted:#5b6e78;--line:#dbe4e8;--accent:#006c70;--warn:#97580c}*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#eef2f3;font:16px/1.7 'Microsoft YaHei',system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:28px}header{padding:32px;border-radius:20px;background:#102c39;color:#fff}h1{font-size:32px;line-height:1.3;margin:12px 0}h2{font-size:24px;margin:0 0 16px}h3{font-size:18px;margin:0 0 8px}p{margin:10px 0}.kicker{color:#8fe2d8;letter-spacing:2px;font-size:13px}small,.muted{color:var(--muted);font-size:13px}header .muted{color:#bed1d8}.decision{color:#ffd789;font-size:22px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:18px}.card{background:white;border:1px solid var(--line);border-radius:16px;padding:24px;margin-top:20px}.half{grid-column:span 6}.third{grid-column:span 4}.full{grid-column:span 12}.metric{font-size:29px;font-weight:700;color:var(--accent)}.warning{border-left:5px solid #ba7617;background:#fff8e9;padding:16px 20px;border-radius:8px;margin:16px 0}.fact{border-top:4px solid #188f83}.infer{border-top:4px solid #567cab}.hypo{border-top:4px solid #c99236}.tag{display:inline-block;padding:3px 10px;border-radius:5px;background:#e5f1ef;color:#125c55;font-size:13px;margin:3px}.scroll{max-width:100%;overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th{background:#e8f0f2;text-align:left;color:#244b59}th,td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top;min-width:92px}tr:nth-child(even){background:#f8fafb}.up{color:#ad293d}.down{color:#08764f}a{color:#08717a}nav{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}nav a{background:white;border:1px solid var(--line);padding:6px 12px;border-radius:20px;text-decoration:none}details{margin-top:16px}summary{cursor:pointer;font-weight:700;color:#006c70}.formula{font-family:Consolas,monospace;background:#edf2f6;padding:16px;border-radius:8px;overflow-wrap:anywhere}footer{font-size:13px;color:var(--muted);margin:28px 0}.step{border-left:3px solid #278a8c;padding-left:16px;margin:18px 0}ul{padding-left:22px}li{margin:6px 0}@media(max-width:760px){main{padding:12px}header,.card{padding:19px}h1{font-size:25px}.half,.third{grid-column:span 12}th,td{padding:9px}.metric{font-size:25px}}@media print{body{background:white}main{padding:0;max-width:none}nav{display:none}.card{break-inside:avoid}header{background:white;color:#142f3b;border:2px solid #142f3b}.decision{color:#97580c}.scroll{overflow:visible}details{display:block}}
</style></head><body><main>'''
html+=f'''<header><div class="kicker">WEEKEND DECISION NOTE · 2026 / 09 / 20</div><h1>长鑫G5，值得重视。<br>但消息、趋势、买点是三件事。</h1><p>整合50只科技与创新药研发链股票；原始价来自本地TDX，跨日指标来自本地同花顺 v_daily_qfq 导出面板。数据截至 <b>2026-09-18收盘</b>，不是周日实时价格。</p><p class="decision">当前动作：观察，不可执行；本报告新增交易授权为零。</p><p>研究顺序：<b>拓荆科技 → 芯源微 → 江丰电子</b>；原趋势补充：<b>力芯微、纳芯微</b>；医药比较：<b>药康生物、美迪西、皓元医药</b>。此顺序不是上涨概率排名。</p><p class="muted">生成：{stamp} · 沪深A股，不含北交所 · 本地运营一致性验证，非独立外部复权证明。消息置信度：量产事件较高；个股G5订单增量未证实；盈利概率未估计。</p><div class="warning" style="color:#523b16"><b>唯一主计划：</b>下一交易日先观察，不挂隔夜买单。待第一行业门控通过、相应A/B信号成立、确认价格提供≥2R且账户信息补齐，再重做交易计划。任一条件不满足取消；当前首仓0，无加仓授权。表中区间仅为静态复核区，触及不自动买入；若新仓次日结构失效，在T+1允许且有流动性时退出，成交价不保证。</div></header>
<nav><a href="#event">消息与映射</a><a href="#market">环境与门控</a><a href="#core">8只核心观察</a><a href="#drug">医药不追清单</a><a href="#plan">买卖计划</a><a href="#audit">全部50只与审计</a></nav>
<div class="grid"><section class="card third"><small>50只指定研究池内</small><div class="metric">{g2}只通过G2</div><p>A信号{ac}只 / B信号{bc}只。信号与最终交易许可分开；不代表全市场计数。</p></section><section class="card third"><small>量化情绪状态</small><div class="metric">修复 · 82.2</div><p>ST_cycle尚未完成样本外验证。35%仅为情绪参考上限，不是目标仓位；G3/G5未通过不得据此建仓。</p></section><section class="card third"><small>宏观 / 盘面过程</small><div class="metric">修复观察</div><p>盘面过程：修复（推断）。宏观三状态未覆盖完整指数证据，不强判趋势扩张；缺分钟数据，不提供盘中承接结论。</p></section></div>
<section class="card" id="event"><h2>01｜消息到底意味着什么</h2><div class="grid"><div class="card third fact"><h3>事实</h3><p>9月20日世界制造业大会现场报道：长鑫宣布第五代DRAM平台量产，工艺涉及四重曝光、11.95nm有源区半间距、45:1电容深宽比及HKMG。<a href="https://ah.anhuinews.com/szxw/202609/t20260920_9599704.html">报道来源</a></p><p>量产事件在白天已公开，不是只有晚间才出现的信息。</p></div><div class="card third infer"><h3>推断</h3><p>更复杂的结构可能提升沉积、刻蚀、清洗、量检测及相关耗材的工艺要求。因此设备/材料更适合列为直接工艺观察链。但实际订单、认证份额尚待逐公司披露。</p></div><div class="card third hypo"><h3>交易假设</h3><p>若资金把技术突破转化为半导体行业持续共振，已收复MA60且不过热的个股更值得跟踪；如果高开回落、行业不共振，消息不能抵消价格失效。</p></div></div>{mapping}
<div class="warning"><b>四个不能画的等号：</b>G5≠第五代DDR产品；11.95nm半间距≠逻辑芯片同名工艺节点；单晶圆裸片产出提高≠公司利润/设备订单同比例提高；DRAM进展≠HBM已经量产。量产设备往往此前已采购，此次公告不等于新增订单日。</div><p>报道中“24Gb单颗”和“24GB产品”不可混为一个单位：24Gb折合3GB，封装组合容量可能不同。本报告不据此计算公司收入。<a href="https://hkcd.com/hkcdweb/content/2026/09/20/content_8776112.html">单颗及封装信息来源</a></p><p><b>名称纠偏：</b>芯源微是涂胶显影设备研究对象；芯碁微装的直接成像/封装应用，不能直接映射为G5前道多重曝光供应商。长鑫与长江存储也不混同。顺络、亿道、富信、奕东及模拟芯片仍可跟踪，但本次新闻不是它们各自新增收入的证据。</p></section>
<section class="card" id="market"><h2>02｜周五环境有修复，但不等于已经全面主升</h2><p>本地5207只样本：上涨占比75.6%，日收益中位数+1.04%，成交额20701.3亿元；站上MA20仅39.2%，站上MA60为52.4%。涨停生态另一个口径样本：涨停76、跌停1、炸板27，炸板率26.2%，连板高度4。不可把不同样本成交额混算。</p>{industrytable}<p>半导体5日强、20日仍接近原位，支持“强修复”判断，不足以证明新一轮持久主升。第一名酿酒来自本地具体行业编码的小样本，不等同于整个白酒行业。缺少完整指数与同期对照，不下宏观全面牛市结论。</p><div class="warning"><b>门控说明：</b>G0/G1通过；逐股重算G2后本研究池12只通过。沿用9/18审计的G3仍失败：第一行业单股驱动；G5因账户未知失败。旧G4通过仅指流动性与乖离，不代表今日实时执行质量或2R已满足。不能把已保存前25只候选当成全部合格股票，也不能因本次新闻绕过G3自动晋升第二、第三行业。</div><p>这种“第一名失效就全部观察”的规则可能过于保守，需要独立回测“排除失效行业后顺延”版本；本次不临时修改生产规则以得到买单。空名单是规则结果，不是市场绝无机会的证明。</p></section>
<section class="card" id="core"><h2>03｜8只核心观察：先排研究优先级，再谈价格</h2><p>静态回踩区按 max(MA20,MA60) 上方约0.3%–2%构造，并不高于9/18收盘。它只是统一复核规则，不是经过回测的最佳买价，也不证明策略B成立。次日均线变化、除权或新平台形成后必须重算。</p>{coretable}<p><b>当前核心8只均未满足压力预算下的近端2R</b>。这不是预测它们不涨，而是不能以遥远高点代替近端压力来包装赔率。药康与美迪西的均线结构相对更完整；拓荆消息关联更强，但MA20仍低于MA60，两者不是同一维度。</p><details><summary>工艺有关，但价格确认不足的11只</summary>{waittable}<p>上述MA60只是重新评估门槛，不是站上就立刻买入。长川、北方、中船等即使出现在A信号池，也仍可能没有通过G2，不能互相替代。</p></details></section>
<section class="card" id="drug"><h2>04｜创新药研发链：区分产业身份与股价热度</h2><p>百普赛斯、义翘神州应主要按研发试剂/重组蛋白及服务理解，不直接等同于拥有获批重磅新药的制药企业，也不能把其全部收入称为AI制药收入。业务依据来自各自年报；这里没有用2025财务数据冒充2026最新盈利预测。长鑫新闻与它们没有已证实的直接收入传导关系。</p><p>泓博医药、成都先导可作为AI/药物发现研究对照；药康、美迪西、皓元是研发基础设施或服务观察方向。此为人工研究分组，不是系统的正式行业或概念数据。</p>{hottable}<div class="warning"><b>最需要克制的组合：</b>百普赛斯近5日上涨约39%，距MA20约25%；成都先导近5日约31%、乖离约20%。强度高不等于新仓赔率好。不要因为错过半导体，再转去追医药加速股。</div><p>近岸蛋白按非常遥远的MA20回踩区算得2.32R，但需要较现价回落约22%，且当前乖离超过30%、G2不通过；这不是当前买点。深回撤后趋势与压力结构可能已经变化，不能预埋静态低价买单。</p></section>
<section class="card" id="plan"><h2>05｜买入与卖出：一套主计划，三种环境分支</h2><div class="step"><h3>空仓：先等待可执行资格</h3><p>周日无盘中指令。预计下一交易日9/21，开市及停复牌以交易所安排为准。消息高开不直接追；若无回踩、无2R或主线仍未通过，继续观察。不将“可能错过”当作放宽门控的理由。</p></div><div class="step"><h3>出现回踩：重新跑策略B，不凭触线自动成交</h3><p>检查是否为启动后2–8日首次缩量回踩、关键结构未破，并等待价格确认。核心8只当前没有B信号；现在给的区间不是预先宣布未来命中。重新取得当日数据及完整门控后，只从真正通过的第一行业择优，不同时开设备与医药两套新仓。</p></div><div class="step"><h3>放量突破：先重算压力与风险，不临盘补买单</h3><p>突破9/18高点/前高只是新证据，不等于获得交易许可；无新平台时不能只拿60日高点作目标。若高开后回落、跌回结构线或原突破区，取消观察方案。禁止盘中因新闻提高事先仓位上限。</p></div>
<div class="grid"><div class="card half"><h3>已有趋势仓：按原理由退出，不用新消息拖延</h3><ul><li>触及事先约定的硬失效位：有可卖库存与流动性时执行退出，不能把止损向下挪。</li><li>收盘跌破MA20：触发减仓；连续两日收在其下：退出该趋势仓；收盘跌破MA60：退出优先，不再等MA20两日确认。收盘确认后在下一可交易时段执行。</li><li>拓荆当前MA60约699.12、芯源345.39、江丰259.13，是重要结构参考；不保证跌破时能按该价成交。</li><li>核心医药趋势单参考MA20：药康32.44、美迪西97.35、皓元92.85；加速股可观察MA10失守，但不临时把短线观察线写成原系统强制卖出规则。</li><li>只有浮盈超过1ATR且止损移到成本以上，才研究加一次；整笔风险仍不能增加。不摊平亏损。</li></ul></div><div class="card half"><h3>重仓或被套：先识别结构，不先找利好</h3><p>中船特气、北方华创、中微、盛美、长川等9/18收盘仍低于MA60。若原本严格按本站MA60趋势规则建仓，其持有依据已需处理；不能拿周末长鑫消息将趋势单改成长期价值单。</p><p>账户持仓、成本、仓位、可卖数量、现金与单笔风险预算未提供，本报告不生成实际卖出股数或账户目标仓位。已有合规趋势仓不会仅因本次G3失败机械清空，应按其个股退出规则及风险上限处理。</p><p><b>T+1：</b>当天买入股票通常不能当天卖出；跌停、跳空和流动性不足会扩大损失。压力预算不是最大亏损保证。</p></div></div>
<h3>把“2R”算清，而不是把止损画窄</h3><div class="formula">有效风险距离 D = max(入场价−结构失效价, 入场价×涨跌幅限制×0.6, ATR20)<br>近端压力预算 RR = (最近可见高点−入场价) ÷ D<br>账户风险仓位上限 = 单笔风险预算比例 ÷ (D÷入场价)</div><p>表中入场参考取观察区上沿；最近高点取当日/前20日/前60日高点中在其上方的最近值，属于粗粒度压力代理，不是成交密集区真值。未扣手续费滑点，实际赔率只会更低。20%涨跌幅股票风险下限为12%，10%股票为6%；这是现有保守压力假设，未证明是收益最优参数，不是预计跌幅。</p><p>例：拓荆参考713.10、结构699.12，计划距离13.98，ATR约33.93，压力下限85.57，因此D=85.57；最近高点729.88，只有约0.20R，不能拿945元远高点绕过729.88来声称当前有充足2R。将来突破并形成新平台后可重新评估。</p><p>仅作算法示例：若账户允许单笔亏损0.5%，风险下限12%对应理论仓位上限约4.17%；仍需取流动性、相关性、剩余风险额度更低者。当前门控失败，实际新增授权仍为0，不是建议现在买4.17%。</p>
<h3>六问落地</h3><ol><li><b>顺势还是抄底？</b>MA60下方者先看修复，不借消息当作趋势已经恢复。</li><li><b>位置关键吗？</b>结合MA20/60与突破平台，不能只见红K。</li><li><b>确认了吗？</b>A信号不等于G2，更不等于G3/G5；表中的未来回踩也不是已确认B。</li><li><b>错了怎么退出？</b>结构线与原策略一致，写明收盘触发、次日执行及T+1限制。</li><li><b>有2R吗？</b>用最近压力和有效风险，缺证据就不写漂亮目标价。</li><li><b>亏1R还能执行吗？</b>只有你能确认；账户风险预算未知，不替你假定承受力。</li></ol><p><b>机会成本：</b>不追可能错过消息推动的跳空和后续上涨，无法可靠估计幅度。接受错过，是为了不把过去亏损转化为急于回本的高开新仓。</p></section>
<section class="card" id="audit"><h2>06｜纠偏、完整清单与可追溯性</h2><ul><li>旧候选表仅展示前25只，“没显示”不等于没通过G2。本次50只逐股核验，12只通过。</li><li>旧回踩建议若低于同时给出的MA60失效线，逻辑冲突；本次统一以两者较高者为结构参考，区间位于其上方。</li><li>生产G2成交额用前20日中位数，本报告没有把含当日20日均值与其混用。</li><li>50只最新TDX原始OHLC与面板最新锚全部一致；没有重复日期行，每只至少260根。历史复权遵循已通过的本地运营验证，未新增外部独立复权证明。</li><li>本报告没有回测新排序/观察区/压力代理，不给出上涨胜率，也不承诺稳定盈利。不修改生产数据库或现有门控。</li></ul><details><summary>展开全部50只：G2、A/B、价格、RPS与失效原因</summary>{alltable}</details>
<details><summary>数据来源、口径与拒绝数据清单</summary><p>本地数据日期：2026-09-18。原始价：C:/new_tdx/vipdoc 下 .day；MA/RPS/ATR：output/tdx_stock_daily_with_rps.parquet（实际是同花顺本地v_daily_qfq导出内容，旧文件名不代表TDX复权）。门控：qfq_verification.json、limit_ecology_latest.json、integrated_decision_20260918_audit.json；A/B来自同日enriched名单。行业来自本地TDX编码，行业排名沿用当日审计。</p><p>价格单位元；量额保持原始口径；RPS为本地沪深样本横截面分位，不是上涨概率。ATR为20日真实波幅简单平均。MA阶段标签仅是本报告研究分类。数值精度不代表成交精度或最优买价。</p><p>未使用：估算主力资金、未经验证概念归属、缺历史的分钟资金效率、未来行情。未取得：下一交易日实时行情、个股G5新增订单与份额、完整账户资料、最新逐股估值及2026半年度盈利质量复核。本报告不是基本面合理价值估值报告。</p></details><h3 style="margin-top:20px">公开来源（查询日：2026-09-20；事件时间与报告时间分别标注）</h3><ul>{links}</ul></section><footer>本报告以交易研究和风险管理为目的，不构成收益保证或自动下单指令。新闻映射、技术形态、账户适配是不同层面的判断；任何一层缺证据，都应保留“不交易”的选项。</footer></main></body></html>'''
path=ROOT/'output/a_share_analysis_20260920_cxmt_buy_sell_plan.html'
path.write_text(html,encoding='utf-8')
assert 'NaN' not in html and 'Infinity' not in html
assert '<script src=' not in html and '<link rel="stylesheet"' not in html
assert len(d['rows'])==50 and g2==12
for r in d['rows']:
    if r['zone']: assert r['zone'][0]>r['invalid'] and r['zone'][0]<=r['zone'][1]
print(json.dumps({'report':str(path),'symbols':len(d['rows']),'g2':g2,'A':ac,'B':bc,'bytes':path.stat().st_size,'asof':d['asof']},ensure_ascii=False))
