import json, html
from pathlib import Path
import pandas as pd

root=Path(__file__).resolve().parents[1]
out=root/'output/review_20260916'
r=json.loads((out/'historical_daily_snapshot.json').read_text(encoding='utf-8'))
a=json.loads((out/'integrated_decision_20260916_audit.json').read_text(encoding='utf-8'))
df=pd.read_csv(root/'output/siruipu_right_side_v2_20260916.csv',dtype={'ts_code':str})
df=df[df.role.isin(['DISCOVERY','UPGRADE']) & (df.adv20_yi>=1.5)].copy()
df['阶段']=df.role.map({'DISCOVERY':'预警转强','UPGRADE':'放量启动'})
cols=['ts_code','name','industry_name','阶段','close','RPS_50','RPS_120','RPS_250','vs_ma20_pct','vol_ratio']
labels=['代码','名称','行业','阶段','前复权收盘','RPS50','RPS120','RPS250','距MA20%','量比']
def table(frame):
    return frame.to_html(index=False,border=0,float_format=lambda x:f'{x:.2f}',na_rep='未取得',escape=True)
t=df[cols].copy();t.columns=labels
b=pd.DataFrame(r['strategy_b'])[['ts_code','name','close','RPS_50','RPS_120','vs_ma20_pct','vol_ratio']]
b.columns=['代码','名称','前复权收盘','RPS50','RPS120','距MA20%','量比']
g=''.join(f"<tr><td>{x['id']} {html.escape(x['name'])}</td><td>{'通过' if x['pass'] else '未通过'}</td><td>{html.escape(x['detail']).replace('第一主线可执行 2 只','第一行业归属 2 只（不代表授权）')}</td></tr>" for x in a['gates'])
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>9月16日右侧趋势筛选复核</title><style>body{{margin:0;background:#101b2c;color:#e8edf5;font:16px/1.7 system-ui}}main{{max-width:1180px;margin:auto;padding:32px}}section{{background:#19283c;padding:24px;border-radius:16px;margin:20px 0}}h1,h2{{color:#8bdbce}}.warn{{color:#ffd08c}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #395069;white-space:nowrap}}p{{max-width:1050px}}@media print{{body,section{{background:white;color:black}}}} </style><main>
<h1>2026年9月16日｜右侧趋势筛选与评估</h1><p>历史截面复核 · 沪深A股，不含北交所 · 不使用9月17日涨跌结果挑选赢家</p>
<section><h2>结论：有候选，尚无通过全部门控的买单</h2><p>策略A实际命中 <b>{len(df)}</b> 只，日报仅展示前30只；其中RPS120≥80共 <b>{int((df.RPS_120>=80).sum())}</b> 只。策略B回踩条件命中 <b>{len(b)}</b> 只，但“首次”属性尚未严格校验。</p><p class="warn">G0、G1通过；G3行业共振、G5账户执行未通过。候选不是买入指令，历史信号不能当作当前价格下的买点。</p></section>
<section><h2>盘面：普遍反弹，不等于全面趋势恢复</h2><p>当日上涨占比74.7%，个股收益中位数+0.98%；站上MA20占28.1%，站上MA60占45.6%。本地生态统计涨停91只、跌停4只、炸板15只，炸板率14.15%，昨日涨停股收盘平均收益+3.65%。</p><p>推断：短期情绪明显修复，但中期均线结构仍待恢复。ST_cycle=97.6并不自动升为主升状态：状态机仍是“修复”，且未经样本外验证，35%只作风险上限参考，不是建议仓位。</p><p>行业第一为建材，但最大个股占行业成交额37.54%，超过35%规则，G3失败。元器件排名第二，MA20宽度57.93%、成交额为ADV20的1.22倍、最大股占4.56%，结构更均衡；按现有“只执行第一行业”规则仍只能观察，不临时改规则放行。</p></section>
<section><h2>优先复核的研究名单</h2><p>不是胜率排名。元器件组先看中巨芯688549、兴福电子688545：RPS120分别99.63、96.05，量比1.51、1.26，贴近MA20但RPS50仅5.62、2.93，属于中长周期强股的短期修复，不能描述为全周期强势。</p><p>启动组看太极实业600667、拓荆科技688072：RPS120分别97.94、97.69，量比1.78、1.58，距MA20约4.94%、4.12%；行业许可仍未满足。低乖离观察精测电子300567、雅克科技002409，距MA20约1.57%、1.62%。</p><p>成都先导、明微电子、彤程新材、宏达电子距MA20约10%或以上，先观察回踩，不因“放量启动”追价。B组华能蒙电优于绿发电力的依据仅是相对强度和缩量程度；两只RPS120均未达到统一G2的80门槛。</p></section>
<section><h2>六道门逐项复核</h2><div class="scroll"><table>{g}</table></div><p>G2/G4的25只来自另一套行业整合筛选，不是策略A/B的命中数。G4仅检验流动性与乖离，不代表已验证触发价、止损和2R空间。未覆盖—缺少实时盘口、账户权益/持仓/现金与单笔风险预算。</p></section>
<section><h2>策略A全部命中</h2><div class="scroll">{table(t)}</div></section><section><h2>策略B回踩条件命中</h2><div class="scroll">{table(b)}</div><p class="warn">代码未排除TURN后之前已发生的回踩事件，因此本报告不将其认定为“首次回踩”。这属于策略实现缺口，不能用运行成功代替规则完备。</p></section>
<section><h2>如何使用这份历史名单</h2><p>空仓：等待行业门通过，再检查突破位/回踩低点与可执行报价；确定硬失效位后计算风险，至少2R空间才进入交易计划，当前不生成仓位。已有持仓：按原有趋势失效位管理，不因再次入选而补亏损仓。当天新增仓受T+1限制，跳空或跌停可能使实际损失超过计划1R。</p><p>交易假设：突破后不迅速跌回突破位、回踩后重新转强，并有行业同步支撑；失效：跌破结构低点或既定趋势止损。没有逐股价格结构核验，不能给精确止损价。等待会错过快速V形反弹，这是纪律的机会成本，而非保证盈利的方法。</p></section>
<section><h2>数据与审计边界</h2><p>候选来自9月16日已落盘筛选文件及23:24:38日报快照。原始锚为本地TDX日K；均线/RPS等使用本地同花顺v_daily_qfq，不是纯TDX未复权指标。G0为本地运营一致性，16/16抽样通过、全库公式与量额错配0，不是独立外部复权证明。</p><p>行业与市场指标为本次按9月16日截断重算；生产库可能包含较晚更新的复权因子、当前行业/名称，属于历史复核而非严格当时可见的完整回测。历史G0/G1原样复制到隔离目录，未覆盖当前日报或最新门控。生态与整合市场成交额因样本覆盖不同，不混用。</p><p>事实、推断和交易假设已分开。方法未证明稳定盈利，不自动下单。完整机器审计保存在本目录integrated_decision_20260916_audit.json。</p></section></main></html>'''
path=out/'a_share_analysis_20260916_review.html'
path.write_text(body,encoding='utf-8')
assert 'NaN' not in body and 'Infinity' not in body
print(json.dumps({'A_total':len(df),'A_G2':int((df.RPS_120>=80).sum()),'B':len(b),'report':str(path)},ensure_ascii=False))
