from pathlib import Path
import pandas as pd
import json, struct
ROOT=Path(__file__).resolve().parents[1]
gate=json.loads((ROOT/'output/qfq_verification.json').read_text(encoding='utf-8'))
assert gate['price_adjustment']=='verified_qfq'
d=pd.read_parquet(ROOT/'output/tdx_stock_daily_with_rps.parquet',filters=[('ts_code','==','688026.SH')]).sort_values('trade_date').set_index('trade_date')
raw=struct.unpack('<IIIIIfII',Path('C:/new_tdx/vipdoc/sh/lday/sh688026.day').read_bytes()[-32:])
assert raw[0]==int(d.index[-1].strftime('%Y%m%d')) and abs(raw[4]/100-d.close.iloc[-1])<.011
w=d.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','vol':'sum','ts_code':'count'}).dropna()
for n in [5,10,20,60]: w[f'MA{n}']=w.close.rolling(n).mean()
w['日均量']=w.vol/w.ts_code
w['日均量比']=w['日均量']/w['日均量'].shift(1).rolling(4).mean()
w['周涨幅%']=w.close.pct_change()*100
last=w.iloc[-1]
plot=w.tail(45); bottom=float(plot.low.min())*.93; top=float(plot.high.max())*1.06
x=lambda i:60+i*18
y=lambda p:330-(p-bottom)/(top-bottom)*270
svg='<svg viewBox="0 0 900 425" role="img" aria-label="洁特生物最近45个有交易周前复权周K及5、10、20周均线；红阳绿阴，人民币元">'
for p in [10,15,20,25,30]:
 svg+=f'<line x1="50" x2="875" y1="{y(p)}" y2="{y(p)}" stroke="#dce2df"/><text x="8" y="{y(p)}" font-size="12">{p}元</text>'
for i,(_,r) in enumerate(plot.iterrows()):
 color='#b84343' if r.close>=r.open else '#228273'
 svg+=f'<line x1="{x(i)}" x2="{x(i)}" y1="{y(r.high)}" y2="{y(r.low)}" stroke="{color}"/><rect x="{x(i)-5}" y="{min(y(r.open),y(r.close))}" width="10" height="{max(1,abs(y(r.close)-y(r.open)))}" fill="{color}"/>'
 if i%10==0: svg+=f'<text x="{x(i)-20}" y="360" font-size="11">{plot.index[i]:%Y-%m-%d}</text>'
for n,color in [(5,'#9a692b'),(10,'#6659a0'),(20,'#307185')]:
 pts=' '.join(f'{x(i)},{y(r[f"MA{n}"])}' for i,(_,r) in enumerate(plot.iterrows()))
 svg+=f'<polyline points="{pts}" stroke="{color}" fill="none" stroke-width="1.6"/><text x="{70+(n//5-1)*160}" y="400" fill="{color}" font-size="14">MA{n}周</text>'
svg+='</svg>'
rows=''.join(f'<tr><td>{idx:%Y-%m-%d}</td><td>{int(r.ts_code)}</td><td>{r.open:.2f}</td><td>{r.high:.2f}</td><td>{r.low:.2f}</td><td>{r.close:.2f}</td><td>{r["周涨幅%"]:+.2f}%</td><td>{r["日均量比"]:.2f}</td></tr>' for idx,r in w.tail(8).iterrows())
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>洁特生物｜周线结构核验</title><style>body{margin:0;background:#f6f7f3;color:#243d33;font:16px/1.85 system-ui,"Microsoft YaHei",sans-serif}header{background:#193f31;color:white;padding:30px}main{max-width:1050px;margin:auto;padding:24px}h1{font-size:27px}h2{font-size:21px;margin-top:30px}.warn{padding:18px;background:#fff0d5}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid #ddd;white-space:nowrap}svg{width:100%;background:white}a{color:#236b50}.small{font-size:13px;color:#617367}@media print{body{background:white}}</style><header><h1>洁特生物 688026：周线强，但已不是低位启动</h1><p>研究日期2026-10-11｜日K截至2026-10-09｜周线价格：本地同花顺等比前复权聚合</p></header><main>
<div class="warn"><b>结论：上涨结构仍在，30.58元附近出现明显分歧，当前不宜把“趋势强”直接等同于“追买划算”。</b><br>研究观察，不授权交易。宏观状态、个股行业门、账户仓位与具体执行计划未覆盖；不提供伪精确仓位和买单。</div>
<h2>先纠正周期：截图和真正周线不完全一致</h2><p>截图末根阴线，但实际10月8—9日合成周K开27.28、收27.95，为小阳线；相对上周27.61上涨1.23%。截图可能是日K或采用不同周期/截止点，不能把截图阴线当周线阴线。最后一周只有2个交易日：是已结束的短交易周，不是周中未完成K线。9月25日标签周有4根、10月2日标签周有3根，标签是周五归档日期，不代表当日开市。</p>
<h2>真正周K：逐级上台阶，随后加速</h2>'''+svg+'''<p class="small">图：最近45个有交易周，纵轴人民币元，横轴周末归档日期；红为收≥开、绿为收&lt;开。MA为周收盘简单均线，参数与截图颜色不做对应。</p>
<ul><li>7月24日当周低点约11.19元，之后8月上冲到19.20元，再回撤到15.15元；底部开始抬高。</li><li>9月4日当周放量突破19.20，最高23.11；随后两周整理，9月18日周低19.81仍高于8月整理低点，呈阶梯式推进。</li><li>9月25日当周上涨23.45%，突破23.11后进入加速；不是刚离开底部的早期位置。</li><li>最新周最高30.58、最低25.00，收27.95，处于周振幅中部偏上；上下影都明显，多空分歧扩大，尚非明确周线反转。</li></ul>
<h2>强度与风险必须一起看</h2><p>5/10/20/60周均线分别约24.75 / 21.21 / 17.42 / 17.43元。5&gt;10&gt;20且三者上升，但20周仍略低于60周，因此不能说全部长短均线已经完美多头排列。收盘高于5周线约12.9%、高于20周线约60.5%，中期趋势强与短期过热同时存在。</p><p>日频RPS50/120/250约99.9 / 98.0 / 91.4；这是日频收益横截面排名，不是周RPS，也不是继续上涨概率。截图未标明副图参数，不能据其线条直接断言资金吸筹或MACD背离。</p><p>最新周总量约3496万股，日均约1748万股，为此前4周日均量均值的1.80倍，不能叫缩量洗盘。10月9日下跌4.44%，成交量比10月8日增加约6.3%，同时从日低25元收回至27.95元：有抛压，也有承接；两者都不能证明主力出货或洗盘。</p>
<h2>三个结构观察区，不是固定支撑保证</h2><ul><li><b>30.58元：前高确认线。</b>更强证据是完整周收在其上，后续回踩不快速跌回；仅盘中摸高不够。直接上冲而均线未跟上，追买风险更高。</li><li><b>24.3—25.0元：近端防守区。</b>来自前一周低点24.33、最新低点25.00和当前5周线24.75。未来若缩量横盘守住、低点不再下移，有利于延续；放量周收跌破意味着加速段受损，不再按强势整理解释。</li><li><b>21.0—23.1元：中期结构复核区。</b>对应旧突破区、整理收盘与10周线附近，范围较宽；回到这里已是明显降速。进一步周收跌破19.81，阶梯抬高结构会明显削弱。</li></ul>
<p>研究性赔率示例：若以27.95为观察价、25为失效参考、30.58为第一阻力，名义空间2.63元 / 风险2.95元≈0.89R，未计跳空、T+1与费用。它不是全走势目标，但说明“马上追到前高”这段空间并不宽。科创板波动大，触及失效位也未必能按该价退出。</p>
<h2>后续怎么跟踪</h2><p><b>趋势延续：</b>横盘1—3周、日均量逐步回落、5周线追上价格，再形成放量向上确认，是值得观察的路径；不是预言。<b>风险路径：</b>量持续大而价格不创新高、周收跌破近端防守区，则考虑上涨动能衰减。单根长影线不判顶，更不把所有回调都解释成洗盘。</p><p>未持仓：本次只列观察条件，不追着截图开仓。已有持仓：需结合成本、可卖数量和原有退出规则；不能仅因周线好看忽略日线风险。机会成本是它可能直接突破不回踩，但等待可减少在加速末端承受大波动的风险。</p>
<h2>近8周审计数据</h2><div class="scroll"><table><thead><tr><th>周五标签</th><th>交易日数</th><th>开</th><th>高</th><th>低</th><th>收</th><th>周涨跌</th><th>日均量/此前4周均值</th></tr></thead><tbody>'''+rows+'''</tbody></table></div>
<h2>口径与边界</h2><p class="small">来源：output/tdx_stock_daily_with_rps.parquet，本次上游从market.duckdb:v_daily_qfq刷新；量为原始股数。G0于2026-10-11 22:49:30通过16/16抽样与全库公式一致性，属于本地运营验证，非独立外部复权证明。最新收盘另与C:/new_tdx/vipdoc/sh/lday/sh688026.day核对一致。按W-FRI聚合，OHLC取首/最大/最小/末；空周删除，量求和。网页<a href="https://4g.stockstar.com/detail/RB2026101000004017">10月9日行情报道</a>交叉核对27.95元，未使用其估算资金流。截图来源为用户附件，未确认周期；本稿没有基本面估值、事件催化或历史形态胜率回测，不提供上涨概率。</p></main></html>'''
out=ROOT/'output/a_share_analysis_20261011_688026_weekly.html'
out.write_text(page,encoding='utf-8')
w.tail(60).to_csv(ROOT/'output/688026_weekly_20261009.csv',encoding='utf-8-sig')
assert 'NaN' not in page
print(out)
