from pathlib import Path
import json, struct

ROOT = Path(__file__).resolve().parents[1]
stocks = [
 dict(code='002920', name='德赛西威', shares=5.96809294, fy=24.5358476775, h1=12.4464532307, prev_h1=12.2255905324, profit=[22,27,33], pe=[16,22,28], base_pe=[20,24]),
 dict(code='000333', name='美的集团', shares=76.28798092, fy=439.45, h1=264.46037, prev_h1=260.1369, profit=[400,450,500], pe=[11,14,17], base_pe=[12,15]),
]
for s in stocks:
    path = Path(f'C:/new_tdx/vipdoc/sz/lday/sz{s["code"]}.day')
    bar = struct.unpack('<IIIIIfII', path.read_bytes()[-32:])
    s.update(date=bar[0], price=bar[4]/100, source=str(path))
    s['ttm_profit'] = s['fy']+s['h1']-s['prev_h1']
    s['ttm_eps_proxy'] = s['ttm_profit']/s['shares']
    s['pe_proxy'] = s['price']/s['ttm_eps_proxy']
    s['values'] = [p/s['shares']*pe for p,pe in zip(s['profit'],s['pe'])]
    s['base_range'] = [s['profit'][1]/s['shares']*pe for pe in s['base_pe']]
    s['h2_growth_base'] = (s['profit'][1]-s['h1'])/(s['fy']-s['prev_h1'])-1

def ddm(g,r,gt):
    return sum(4.3*(1+g)**i/(1+r)**i for i in range(1,6))+4.3*(1+g)**5*(1+gt)/(r-gt)/(1+r)**5
ddms = [ddm(.02,.10,.02),ddm(.05,.09,.03),ddm(.07,.08,.03)]
out = ROOT/'output/a_share_analysis_20261011_desay_midea_valuation.html'
datafile = ROOT/'output/valuation_desay_midea_20261011.json'
datafile.write_text(json.dumps({'valuation_date':'2026-10-11','units':'profit/shares:亿元/亿股; price:人民币元','stocks':stocks,'midea_ddm':ddms,'status':'analyst scenarios, not consensus or trade authorization'}, ensure_ascii=False, indent=2), encoding='utf-8')
rows = ''.join(f'<tr><td>{s["name"]}<br>{s["code"]}</td><td>{s["price"]:.2f}</td><td>{s["ttm_profit"]:.2f}亿元<br>{s["pe_proxy"]:.1f}倍</td><td>{s["base_range"][0]:.0f}—{s["base_range"][1]:.0f}元</td><td>{s["values"][0]:.0f}元</td><td>{s["values"][2]:.0f}元</td></tr>' for s in stocks)
scenario_rows=''
for s in stocks:
    for label,p,pe,v in zip(['保守','基准','乐观'],s['profit'],s['pe'],s['values']):
        scenario_rows+=f'<tr><td>{s["name"]}·{label}</td><td>{p}亿元</td><td>{p/s["shares"]:.2f}元</td><td>{pe}倍</td><td>{v:.1f}元</td><td>{v/s["price"]-1:+.1%}</td></tr>'
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>德赛西威 × 美的集团｜估值研究</title><style>
body{margin:0;background:#f5f6f2;color:#23352e;font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif}header{background:#193e32;color:white;padding:32px max(22px,calc((100vw - 1120px)/2))}main{max-width:1120px;margin:auto;padding:22px}h1{font-size:28px;line-height:1.4}h2{font-size:22px;margin-top:32px}h3{font-size:18px}p{margin:12px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}.card{background:white;padding:22px;border:1px solid #d9e1db}.warn{background:#fff3d9;padding:16px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #d8ded8}th{white-space:nowrap}a{color:#226145}.small{font-size:13px;color:#5c6d62}header .small{color:#dbe5df}nav a{margin-right:18px}li{margin:9px 0}@media(max-width:720px){.grid{display:block}.card{margin-bottom:18px}h1{font-size:23px}main{padding:16px}}@media print{.card{break-inside:avoid}body{background:white}}
</style><header><p>估值研究 / A股 / 2026-10-11</p><h1>美的：合理附近；德赛：有条件的折价</h1><p>德赛的便宜依赖业绩恢复，美的的价值依赖现金分配的持续性。</p><p class="small">价格：本地TDX 2026-10-09原始收盘；财报：2025年报＋2026半年报。估值置信度中等，交易判断未覆盖。</p></header><main><nav><a href="#summary">估值结论</a><a href="#model">模型</a><a href="#risk">验证与风险</a><a href="#source">来源</a></nav>
<div class="warn">当前动作：研究观察，不据本报告直接下单。市场状态、ST_cycle、行业第一主线、账户仓位上限未覆盖——本次未执行市场与账户门控。既有G0仍有抽样对齐失败，因此不计算MA/RPS/ATR，不提供技术买点或止损。单日原始价格可用于估值比较，不代表复权门已通过。</div>
<h2 id="summary">30秒结论</h2><div class="scroll"><table><thead><tr><th>公司</th><th>参考收盘</th><th>TTM利润 / 静态股本PE近似</th><th>基准合理区间</th><th>保守情景</th><th>乐观情景</th></tr></thead><tbody>'''+rows+'''</tbody></table></div><p class="small">单位：人民币元/股。估值日取2026-10-11，使用2026全年预测盈利的前瞻PE法；不是年底必达目标。PE是分析者假设，不是历史分位或市场一致预期；情景没有赋予概率，保守价也不是底。</p>
<div class="grid"><section class="card"><h3>德赛西威｜002920</h3><p><b>基准90—109元，中心约100元。</b>79.45元低于此区间，但保守情景约59元，仍可能有约26%下行。不能因为跌过很多就定义低估。</p><p>基准2026归母利润27亿元，需要下半年同比增长约18%。若只延续半年报的低增速，基准就偏乐观。</p><p>研究性安全折价线：约72元，即基准下沿再折20%；只是纪律示例，不是支撑位或买单。</p></section><section class="card"><h3>美的集团｜000333</h3><p><b>基准71—88元，中心约83元。</b>82.46元处于区间内；现金分红有吸引力，但目前没有明显估值折价。</p><p>基准2026归母利润450亿元，需要下半年同比增长约3.5%。利润稳定性与现金回报，比“机器人科技股”标签更重要。</p><p>研究性安全折价线：约57元，即基准下沿再折20%；65—70元意味着更温和折价，不属于同一个20%标准。</p></section></div>
<h2>事实：最新业绩透露了什么</h2><p>德赛2026H1归母利润12.45亿元、同比+1.81%，扣非同比+6.32%；经营现金流10.63亿元，购建长期资产现金支出7.81亿元，两者差额仅2.81亿元。盈利不是没有增长，但现金再投资负担值得跟踪。数据见<a href="https://static.cninfo.com.cn/finalpage/2026-08-14/1225472735.PDF">半年报</a>。</p><p>美的2026H1归母利润264.46亿元、同比+1.66%，扣非195.95亿元、同比−25.31%。公司解释汇兑损失与套保收益分类不对称，不能机械只选归母或扣非作为“真实利润”。经营现金流375.52亿元，购建长期资产支出26.34亿元，差额349.18亿元。数据见<a href="https://disc.static.szse.cn/disc/disk03/finalpage/2026-08-29/df25443f-d67a-4cc5-8bd6-6c3e681a9575.PDF">半年报</a>。</p><p class="small">上述“经营现金流−资本支出”是粗略现金代理，不是股东自由现金流；未扣收购支出、债务净偿还和其他必要项目，也未调整美的金融业务资金。半年现金流不可直接翻倍，不能据此宣称现金都能派给股东。</p>
<h2 id="model">模型一：2026全年盈利情景 × 前瞻PE</h2><div class="scroll"><table><thead><tr><th>情景</th><th>归母利润假设</th><th>静态股本EPS</th><th>PE假设</th><th>估值</th><th>相对参考价空间</th></tr></thead><tbody>'''+scenario_rows+'''</tbody></table></div>
<p><b>德赛倍数：</b>保守16倍用于增长停滞、客户压价；基准20—24倍要求订单转收入、利润恢复并有后续增长；28倍乐观值需要更强持续增长和现金兑现，不能仅由汽车智能化叙事支撑。预测22/27/33亿元，不是券商共识。</p><p><b>美的倍数：</b>保守11倍体现需求与海外经营承压；基准12—15倍用于成熟业务稳定增长和分红持续；17倍要求更好的增长质量。400/450/500亿元是分析者归母利润情景，未声称完成套保逐项正常化。</p><p><b>敏感性：</b>德赛基准PE每变化1倍，估值约变化4.52元；美的约5.90元。盈利变动10%，同PE估值也变动10%。因此“精确目标价”不可靠。</p>
<h3>分母与时间口径</h3><p>德赛使用半年报5.9681亿总股本；美的使用半年报披露日76.2880亿A＋H总股本。没有用A股流通股数分摊全部利润，也没有把A股价格乘全部股本称为实际A＋H总市值。两者均为静态、未扣库存股的保守近似，不等于会计加权或完全稀释EPS；最新回购注销、可转债转股及潜在新股发行须另行更新。若股数增加10%且利润不变，每股估值降低约9.1%；融资带来的现金和未来收益还需单独计入。</p><p>TTM归母=2025全年＋2026H1−2025H1；美的2025利润取439.45亿元（四舍五入），德赛24.5358亿元。全年预测不采用半年简单乘二。这里没有把2027盈利提前套上而不折现，也没有把已过去的分红再加到价格上。</p>
<h2>模型二：美的分红折现交叉检验</h2><p>以2025财年每股分红4.30元作为D0历史锚（不是未来承诺），预测未来5年分红，然后用永续增长终值折现。V=Σ D0×(1+g)^t/(1+r)^t＋D5×(1+g终)/(r−g终)/(1+r)^5。</p><ul><li>保守：5年分红增长2%、股权回报要求10%、永续2% → '''+f'{ddms[0]:.1f}'+'''元。</li><li>基准：增长5%、回报要求9%、永续3% → '''+f'{ddms[1]:.1f}'+'''元。</li><li>乐观：增长7%、回报要求8%、永续3% → '''+f'{ddms[2]:.1f}'+'''元。</li></ul><p>基准约80元，与PE法中心约83元接近，但不是独立证明：两者都依赖稳定盈利。分红折现未另加回购或全部账面现金，避免重复计价；未来分红应由再投资后的可分配现金支持。实际未来一年股息可能不同，4.3/82.46≈5.2%只是历史分红参考率，不是承诺收益。</p><p>德赛低派息且投入较重，不机械用股息定价。本次只检验现金兑现，没有完成完整FCFE/DCF，不能声称已有第二套独立现金流模型支持其估值。</p>
<h2 id="risk">把叙事变成能证伪的跟踪表</h2><div class="grid"><section class="card"><h3>德赛：终局很大，利润要跟上</h3><ul><li>订单→收入：项目年化订单不能直接加到当年收入，更不能当利润。</li><li>壁垒→毛利：看域控产品竞争、客户降价与软硬件价值分配。</li><li>盈利→现金：跟踪应收/存货占用和全年现金转换，不单看一个季度。</li><li>基准失效：全年利润趋向22亿元、增发稀释超预期或持续现金转弱，转用保守模型。</li></ul></section><section class="card"><h3>美的：现金回报，不是无限重估</h3><ul><li>海外增长：拆收入、汇率、关税和当地成本，不把名义增长当利润增长。</li><li>利润正常化：汇兑与套保放在一起分析，关注匹配期限及未实现损益。</li><li>第二曲线：机器人/楼宇业务增长必须贡献利润和资本回报；不对集团利润整体套科技PE。</li><li>基准失效：可持续利润趋向400亿元或以下、分红削弱、现金明显恶化，重新下调。</li></ul></section></div>
<p><b>比较结论：</b>按本模型德赛的估值修复空间更大、假设风险也更大；美的现金回报逻辑更清楚，但现价接近基准。若关注资本保全，不应为追求弹性把德赛的乐观情景当作主计划。未买的机会成本是错过提前修复；等待换来的是更多财报证据，而不是保证更低价格。</p><p><b>交易边界：</b>两者都没有在本次获得交易授权。账户资料、行业共振和技术结构未评估；T+1、跳空及跌停可能导致止损无法按计划成交。估值区间不是技术止损或短期股价预测。</p>
<h2 id="source">来源与审计</h2><ul><li>报价：C:/new_tdx/vipdoc/sz/lday/sz002920.day、sz000333.day；末条20261009，close/100，未复权；只用单日锚。</li><li><a href="https://static.cninfo.com.cn/finalpage/2026-03-06/1224998406.PDF">德赛2025年报</a>；<a href="https://static.cninfo.com.cn/finalpage/2026-08-14/1225472735.PDF">德赛2026半年报</a>；2026-03-06/08-14披露。</li><li><a href="https://www.hkexnews.hk/listedco/listconews/sehk/2026/0330/2026033002440_c.pdf">美的2025年报</a>；<a href="https://disc.static.szse.cn/disc/disk03/finalpage/2026-08-29/df25443f-d67a-4cc5-8bd6-6c3e681a9575.PDF">美的2026半年报</a>；2026-03-30/08-29来源日期。</li><li>财报采用本次核实的最新完整半年报，未使用未核实三季报。估值数据采集2026-10-11；未对公告全量逐项审计。</li><li>复算：tmp/value_desay_midea_20261011.py；参数与结果：output/valuation_desay_midea_20261011.json。未修改生产库、策略、门控或自动交易。</li></ul><p class="small">预测、PE、折现率与安全折价均属研究假设。来源事实不等于对估值判断的背书。</p></main></html>'''
out.write_text(page,encoding='utf-8')
assert 'NaN' not in page and 'Infinity' not in page
assert all(s['date']==20261009 for s in stocks)
print(json.dumps({'report':str(out),'stocks':stocks,'ddm':ddms},ensure_ascii=False,indent=2))
