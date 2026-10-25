"""Compact conditional watchlist; research allocations never override industry gates."""
import json,html,math
from pathlib import Path
from datetime import datetime
O=Path(__file__).resolve().parents[1]/'output'
d=json.loads((O/'review_15_20260924.json').read_text(encoding='utf-8'));rows={r['code']:r for r in d['rows']}
selected=['301536.SZ','002463.SZ','301217.SZ','688535.SH','001314.SZ','688401.SH','688008.SH','301099.SZ','002119.SZ','688578.SH']
risks=[3500,3200,3000,2800,2500,2200,1800,1500,1200,800]
reasons={
'301536.SZ':('三周期强度最均衡','RPS50/120/250均超过90，MA20和MA60的5日变化均向上。','10日高低振幅27.3%，不是紧凑杯柄；没有当日放量突破。'),
'002463.SZ':('离均线最近，容错更清楚','距MA20仅3.6%；10日振幅约10%，20日上涨日/下跌日量比1.89。','RPS50仅19.4，MA60仍下行；只算修复，不是全面主升。'),
'301217.SZ':('长期强度＋新启动重合','9/23 A命中，RPS120为99.7，收盘接近当日高点，流动性充足。','RPS50仅20，MA60下行；123.97附近有前高压力。'),
'688535.SH':('突破旧高的量价证据较清晰','A命中，收盘越过前20日高114元，成交量约前20日均量1.56倍。','乖离11.7%、RPS50仅7.1；属于深调后的修复，不是成熟杯柄。'),
'001314.SZ':('新启动但没有极端追价','A命中，两条均线之上，RPS120为90.9，乖离8.4%。','量比仅1.16，突破前高幅度很小；MA60尚未转升。'),
'688401.SH':('右侧修复与中期强度重合','A命中，RPS120/250均超过90，站回MA60。','量比1.38、RPS50约33；突破力度有限，MA60仍下行。'),
'688008.SH':('保留高流动性核心观察','RPS120为96，价格仍在MA20/60上，具备复查价值。','当天收盘处于日内区间底部约5%；不能把收红当承接强。'),
'301099.SZ':('显著放量突破，但只留回踩资格','放量约3.05倍，收盘位于日内区间约91%，突破前20日高。','乖离12.2%已越现行上限，RPS50仅11.9；严禁追当日长阳。'),
'002119.SZ':('强收盘与中期强度改善','收于日高，RPS120较20日前提高约11.9点，量比1.76。','乖离13.7%且接近涨停表现，不能假定次日买得到或能当天退出。'),
'688578.SH':('放量反弹有观察价值，仍是候补','9/23 A命中，量比2.80，收盘位于日内区间约88%。','仍低于MA60约114.33，MA20也下行；不把反弹叫趋势确认。')}
def f(x,n=2):return '未取得' if x is None else f'{x:.{n}f}'
def e(x):return html.escape(str(x))
def table(head,body):return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+e(x)+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in z)+'</tr>' for z in body)+'</tbody></table></div>'
plans=[]
for i,c in enumerate(selected):
 r=rows[c];anchor=max(r['m20'],r['m60']);kind='回踩转强'
 lo=round(anchor*1.003,2);hi=round(anchor*1.02,2);stop=round(anchor*.985,2)
 if c=='688578.SH':kind='收复后确认';lo=round(max(r['m60'],r['pivot'])*1.003,2);hi=round(lo*1.01,2);stop=round(r['m60']*.985,2)
 lp=.12 if c.startswith(('300','301','688')) else .06
 terms={'结构距离':hi-stop,'ATR20':r['atr'],'隔夜压力下限':hi*lp};binding=max(terms,key=terms.get);D=terms[binding]
 reserve=math.floor((risks[i]/D*hi)/1000)*1000
 # Nearest locally observed obstacle, not an invented distant profit target.
 obstacles=[r['high'],r['pivot']];obstacles=[p for p in obstacles if p>hi]
 resistance=min(obstacles) if obstacles else None
 rr=(resistance-hi)/D if resistance else None
 plans.append(dict(code=c,kind=kind,lo=lo,hi=hi,stop=stop,pressure=resistance,rr=rr,risk_budget=risks[i],reserve=reserve,first=reserve//2000*1000,D=D,terms=terms,binding=binding,actual=0))
cash=1500000-sum(p['reserve'] for p in plans)
assert sum(p['reserve'] for p in plans)<=525000 and sum(p['risk_budget'] for p in plans)==22500
assert cash+sum(p['reserve'] for p in plans)==1500000
def chart(r,p):
 b=r['bars'][-140:];n=len(b);low=min(min(t['low'],t['m20'],t['m60']) for t in b)*.96;high=max(max(t['high'],t['m20'],t['m60']) for t in b)*1.04
 X=lambda i:42+i*5.0;Y=lambda v:20+(high-v)/(high-low)*200
 s=[f'<svg viewBox="0 0 760 302" role="img" aria-label="{e(r["name"])}近140根日K与MA20 MA60；下栏原始成交量"><rect width="760" height="302" fill="#fff"/>']
 for j in range(4):v=low+(high-low)*j/3;s.append(f'<path d="M40 {Y(v)}H748" stroke="#e2e8f0"/><text x="2" y="{Y(v)+4}" font-size="11">{v:.0f}</text>')
 vmax=max(t['vol'] for t in b)
 for i,t in enumerate(b):
  col='#c53c47' if t['close']>=t['open'] else '#168164';x=X(i)
  s.append(f'<path d="M{x} {Y(t["high"])}V{Y(t["low"])}" stroke="{col}"/><rect x="{x-1.7}" y="{min(Y(t["open"]),Y(t["close"]))}" width="3.4" height="{max(.8,abs(Y(t["open"])-Y(t["close"])))}" fill="{col}"/><rect x="{x-1.7}" y="{275-t["vol"]/vmax*40}" width="3.4" height="{t["vol"]/vmax*40}" fill="{col}"/>')
 for k,col in [('m20','#bf950a'),('m60','#6267c1')]:s.append('<polyline fill="none" stroke="'+col+'" stroke-width="1.6" points="'+' '.join(f'{X(i)},{Y(t[k])}' for i,t in enumerate(b))+'"/>')
 for i in [0,n//2,n-1]:s.append(f'<text x="{X(i)-15}" y="296" font-size="11">{b[i]["date"][5:]}</text>')
 s.append('</svg>');return ''.join(s)
reasonrows=[];cards=[];planrows=[]
for i,p in enumerate(plans):
 r=rows[p['code']];title,why,no=reasons[r['code']]
 reasonrows.append((f'{i+1}. {r["name"]}<br>{r["code"][:6]}',f'{r["close"]:.2f}<br>{r["ret1"]:+.2f}%',f'{r["rps50"]:.0f} / {r["rps120"]:.0f}',f'{r["vol"]:.2f}× / {r["bias"]:.1f}%',title+'<br>'+why,'<span class="risk">'+no+'</span>'))
 planrows.append((r['name'],p['kind'],f'{p["lo"]:.2f}–{p["hi"]:.2f}',f(p['stop']),f(p['pressure']),f(p['reserve']/10000,1)+'万 / '+f(p['first']/10000,1)+'万','0元'))
 details=table(['计划距离','ATR20','隔夜压力下限','D / 主约束','最近压力对应R'],[(f(p['terms']['结构距离']),f(r['atr']),f(p['terms']['隔夜压力下限']),f(p['D'])+' / '+p['binding'],f(p['rr']))])
 cup=r['cup'];failed=[k for k,v in cup['tests'].items() if not v]
 labels={'prior_advance':'缺前置上升','depth':'杯深不符','rim':'左右杯沿不齐','rounded':'底部不够圆缓','handle':'柄深不符','upper_half':'柄位置过低','no_deep_handle':'柄回撤偏大','dry_volume':'柄部未缩量','no_new_high_in_handle':'柄内已越前沿','body_ceiling':'杯身中途越沿'}
 fail='、'.join(labels[k] for k in failed[:3])
 cards.append(f'<article><h3>{i+1:02d} {r["name"]} · {r["code"][:6]}</h3><p class="small">日线至9/23，价格元；金线MA20 / 紫线MA60；红涨绿跌；下栏量。{r["shape"]}</p>{chart(r,p)}<p><b>杯柄：</b>{cup["status"]}，{fail}。<b>关键点：</b>{r["pivot"]:.2f}为前20日高，突破还需量与回踩确认。</p><details><summary>查看风控计算（研究预算，不是买单）</summary>{details}<p>最大损失预算{p["risk_budget"]}元，实际跳空损失可能更大；额度向下取整至千元，未计费用。不是止损保证。</p></details></article>')
reject={
'600641.SH':'乖离39.9%，先等消化；不追加速。','688381.SH':'乖离22.8%，强势不等于买点。','688591.SH':'RPS120仅57.6，ADV约1.1亿未达标。','301555.SZ':'ADV约0.6亿，现行流动性门不通过。','688045.SH':'ADV约0.9亿，且当日量未放大。','688690.SH':'乖离19.1%，无有依据的近端上方目标。','301183.SZ':'乖离22.7%，不以缩量直接认定惜售。','301041.SZ':'乖离19.0%，杯形代理杯深偏大。','603893.SH':'乖离16.9%，先等整理，不追第二根加速。'}
all15=table(['用户原名单','形态 / 杯柄','RPS50/120','MA20/60','关键点突破策略','均线策略 / 结论'],[(r['name']+' '+r['code'][:6],r['shape']+'<br>'+r['cup']['status'],f(r['rps50'],0)+' / '+f(r['rps120'],0),f(r['m20'])+' / '+f(r['m60']),f'观察 {r["entry"]:.2f} 上方放量确认；不是挂单',('进入10只观察池；条件见上' if r['code'] in selected else reject.get(r['code'],'暂不优先'))) for r in d['rows'] if r['given']])
others=table(['补充候选','数值结构','本次未优先的原因'],[(r['name'],r['shape'],('RPS120不足80' if r['rps120']<80 else '尚未通过价格/流动性条件' if not r['basic'] else '数值可观察；本次优先原名单和量价证据更清楚者，不等于该股更差')) for r in d['rows'] if not r['given'] and r['code'] not in selected])
generated=datetime.now().astimezone().isoformat(timespec='minutes')
body=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>10只趋势观察股｜150万元条件预案</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf2f6;color:#203449;font:15px/1.7 "Microsoft YaHei",sans-serif}}main{{max-width:1450px;margin:auto;padding:22px}}header{{background:#142d46;color:#fff;padding:24px;border-radius:16px}}h1{{font-size:30px;margin:5px 0}}h2{{font-size:23px}}h3{{font-size:19px}}.lead{{color:#ffda98;font-size:20px}}section,article{{background:#fff;padding:22px;border-radius:14px;margin-top:18px;break-inside:avoid;min-width:0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}}.stat{{background:white;padding:17px;border-radius:12px}}.stat b{{font-size:25px;display:block}}.small{{font-size:14px;color:#61758b}}header .small{{color:#c2d5e5}}.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e0e7ef;min-width:75px}}th{{background:#edf4fa}}a{{color:#2269a0}}p,td,a{{overflow-wrap:anywhere}}.risk{{color:#986519}}.warn{{background:#fff3db;padding:14px;border-left:4px solid #c39035}}nav a{{display:inline-block;margin:12px 18px 0 0}}svg{{width:100%;height:auto}}summary{{cursor:pointer;color:#2269a0;font-weight:600}}@media(max-width:760px){{main{{padding:10px}}header,section,article{{padding:16px}}.grid{{grid-template-columns:1fr}}h1{{font-size:25px}}.stats{{grid-template-columns:1fr}}}}@media print{{details>*{{display:block}}nav{{display:none}}body{{background:white}}section{{border:1px solid #ddd}}}}</style></head><body><main>
<header><div>2026-09-24 · 数据截至9月23日收盘 · 沪深A股</div><h1>精选10只观察，不编造“非买不可”</h1><p class="lead">优先：星宸科技、沪电股份、铜冠铜箔、华海诚科。其余分层等确认。</p><p>150万元、空仓、无融资，按你确认的假设。当前新增买入0元，现金150万元；下表是未来条件满足后的预留预算，不是今天的买单。</p><p class="small">本地TDX原始锚＋已验证同花顺本地前复权；G0/G1通过。第一行业是地产，10只均无当前第一行业资格；缺实时买点，不能绕过门控。情绪“修复”40.4、formal=false，参考上限35%即52.5万元，非目标仓位。宏观状态缺完整指数证据；盘面推断风险偏好收缩。唯一动作：等待，不新开仓。生成 {generated}。</p></header>
<nav><a href="#ten">10只与理由</a><a href="#money">买点/止损/资金</a><a href="#charts">10张K线</a><a href="#fifteen">原15只逐股结论</a><a href="#method">方法与来源</a></nav>
<div class="stats"><div class="stat">当前可执行新增<b>0元</b>未满足行业与完整买点</div><div class="stat">未来10份预留额度<b>{sum(p['reserve'] for p in plans)/10000:.1f}万元</b>全部条件成立才有资格使用</div><div class="stat">即使预留全用，仍留现金<b>{cash/10000:.1f}万元</b>不把150万元硬分满</div></div>
<section id="ten"><h2>一、10只：每个理由旁边都有反证</h2><p>6只来自你的15只，4只是9/23分析补充。排序是研究优先级，不是预测胜率；没有一只具备“无法拒绝”的确定性。</p>{table(['标的','收盘 / 日涨跌','RPS50 / 120','量比 / 乖离','最有力入选证据','否决条件与不足'],reasonrows)}<p class="small">量比=当日量÷此前20日均量，不含当日；与旧日报包含当日的均量口径略有不同。RPS50弱而RPS120强，多数是中期强股的短期修复，不是同步主升。</p></section>
<section id="money"><h2>二、150万元：给出价位，但不伪装成已经可买</h2>{table(['股票','观察方式','未来买入观察区（元）','失效/止损参考','近端压力','预留上限 / 首仓上限','当前买入'],planrows)}<p class="warn">共同触发：行业许可恢复＋回踩量≤此前20日均量0.9倍＋转强量≥1.3倍＋站回观察区＋重新核算至少2R。艾力斯另需收复MA60及前高。当前均未通过完整条件，越过区间上沿不追；先跌破止损参考则取消，不在下跌中挂单接刀。</p><p>回踩区取MA20/60较高者上方0.3%–2%，止损参考在其下1.5%；艾力斯用前高确认区。都是静态研究坐标，出现新K线必须重算。止损是失效指令，不保证该价成交；T+1新仓只能下一交易日卖。</p><p>风险预算合计2.25万元（权益1.5%），各股按优先级分配800–3500元，再以max(结构距离、ATR20、6%/12%隔夜压力)计算额度并向下取整；不是10只等额买。最多先开3只、同链合计风险不超过9000元，只在盈利和风险释放后评估后续，永不补亏损仓。额度用不掉就留现金。</p><p>当前所有近端压力对应的保守赔率都不足2R或尚未证明。预算是“条件改善后的上限”，不能以已有额度为买入理由。9/24新买下一正常卖出日为9/28；9/30新买不能当日清仓。<a href="https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml">交易所日历</a></p></section>
<section id="charts"><h2>三、逐只K线：看结构，不画想象中的杯子</h2><p>金线20日、紫线60日；完整杯柄本次未确认。点击每张卡片可看风险计算。</p></section><div class="grid">{''.join(cards)}</div>
<section id="fifteen"><h2>四、你给的15只，一个不少</h2>{all15}<p>“惠栢新材”已按惠柏新材301555核对；先导基电600641，曾用名万业企业。未入10只不代表一定跌，只表示当前不优先。</p><details><summary>9/23补充池：其余候选为什么没进前10</summary>{others}</details></section>
<section id="method"><h2>五、只保留必要的方法说明</h2><p><b>三种策略交叉：</b>①杯柄：前置涨势、圆底、左右杯沿、上半部缩量柄、突破；②利弗莫尔式关键点：过前高后承接，而非见涨就追；③RPS＋均线：50/120/250日横向排名与20/60日趋势。RPS不是利弗莫尔独有指标，也不等同于IBD专有评分。</p><p><b>形态事实：</b>本次固定杯形35–150根、柄5–20根、杯深12%–35%、柄深3%–12%，检查柄部缩量、杯身未中途越沿；均为自定义代理，未做样本外验证。未通过仅表示本次窗口不满足，不证明任何周期都不存在杯柄。不是把“8/10条件”当80%胜率。</p><p><b>数据确认：</b>{len(d['rows'])}只合并研究股当日OHLC逐只匹配TDX；无重复日期，所有数据截至9/23。RPS此前全市场独立复算最大差0。G0为本地运营一致性验证，非独立外部证明。均线、ATR、形态用v_daily_qfq；量额原始口径。金额元，股票价格元。</p><p><b>判断边界：</b>技术形态不验证公司价值，不推断“主力吸筹”；未使用估算资金流、过时概念池或虚构分钟数据。不操作可能错过反弹，但不因错过而临时抬高买价。任何买入后结构失效，在T+1允许且可成交时退出；跳空、跌停、假期均可能扩大损失。</p><p>方法来源：<a href="https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cup-with-handle">Fidelity杯柄说明</a>；<a href="https://shop.investors.com/images/promotional/20-Rules_102808.pdf">IBD相对强度资料</a>；<a href="https://www.600641.com.cn/">先导基电公司官网</a>。原始审计：review_15_20260924.json；资金预案：top10_20260924_plan.json。研究报告，不承诺收益。</p></section></main></body></html>'''
assert len(cards)==10 and 'NaN' not in body and 'Infinity' not in body
(O/'a_share_analysis_20260924_top10_150w.html').write_text(body,encoding='utf-8')
(O/'top10_20260924_plan.json').write_text(json.dumps({'generated':generated,'asof':d['asof'],'assumption':{'equity':1500000,'cash':1500000,'positions':[],'margin':False},'actual_new_orders':0,'selected':selected,'plans':plans,'reserve_total':sum(p['reserve'] for p in plans),'cash_if_all_reserves_used':cash,'status':'research_only_not_executable'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':[rows[c]['name'] for c in selected],'reserve':sum(p['reserve'] for p in plans),'cash':cash,'rr':[p['rr'] for p in plans]},ensure_ascii=False))
