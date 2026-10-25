"""Selected defense-related names, not a complete military-sector universe."""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
import pandas as pd
from run_right_side_research import ROOT, chart
from run_optical_overlay import anchor

out = ROOT/'output'
codes = ['600685.SH','600765.SH','600562.SH','600482.SH','600760.SH','302132.SZ','000768.SZ','600391.SH','600967.SH']
gate = json.loads((out/'qfq_verification.json').read_text(encoding='utf-8-sig'))
assert gate['price_adjustment']=='verified_qfq'
assert gate['full_market_checks']['latest_anchor']['max_date']=='2026-09-09'
s = pd.read_csv(out/'right_side_20260909_all.csv').set_index('ts_code').loc[codes].reset_index()
d = pd.read_parquet(out/'right_side_20260909_history.parquet',filters=[('ts_code','in',codes)])
for _,r in s.iterrows():
    raw = anchor(r.ts_code,'20260909')
    assert raw['anchor_status']=='已读取'
    assert max(abs(r[k]-raw['raw_'+k]) for k in ['open','high','low','close'])<.011
columns = {'name':'股票','ts_code':'代码','shape':'形态','close':'收盘','RPS_50':'RPS50','RPS_120':'RPS120','vr':'量比','vs_ma20_pct':'距MA20%','ma20':'MA20','ma60':'MA60','ma120':'MA120','ph20':'此前20日高点'}
table = s[list(columns)].rename(columns=columns).to_html(index=False,border=0,float_format=lambda v:f'{v:.2f}',na_rep='历史不足')
base = (out/'a_share_analysis_20260909_right_side.html').read_text(encoding='utf8')
head = base.split('<main>')[0]
head = head[:head.index('<header>')]+'<header><h1>军工相关股：趋势已经出现，成熟度不同</h1><p>截至2026年9月9日收盘；精选9只本地复核，不代表完整军工成分股筛选。</p></header><main>'
body = ['<h2>直接结论</h2><p>有。中船防务、中航重机符合本次放量突破定义；国睿科技、中国动力、中航沈飞等已经满足收盘&gt;MA20&gt;MA60且两线向上的趋势条件，但当天没有符合规则的新买点。</p><p>这9只的MA60均仍低于MA120，因此按本项目严格分层都还不是“成熟趋势”。国睿科技和中国动力的RPS120更高，只表示中期相对强度更好，不意味着长期均线完全多头。</p><div class="table">',table,'</div><h2>观察优先级</h2><p>突破研究先看中船防务：量比1.97、已越过此前20日高点28.08；但距MA20约8.2%，不能追高过度。中航重机量比1.76、越过14.06，但RPS120仅36.9，且收盘仍低于MA120约14.48，更像弱势后的早期修复。</p><p>持续趋势看国睿科技与中国动力。国睿RPS50约96.8、RPS120约86.0，量比0.78，观察是否缩量整理后再越过30.79；中国动力RPS120约83.3，量比1.15，暂未达到放量突破要求。中航沈飞保持趋势，仍需化解49.52附近前高。</p><p>航发科技、内蒙一机距MA20分别约21.0%、20.7%，是“趋势强但位置过热”的例子，不放进追买优先名单。机会成本可能是错过继续加速，但没有可验证的确定上涨空间，不能据此强行交易。</p><h2>产业边界</h2><p>军工相关不等于纯军品收入。中船防务同时覆盖防务装备与民船海工，国睿包括军工电子与民用雷达；不能仅依据战争新闻推导新增订单或利润。来源：<a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0327/2026032704436_c.pdf">中船防务年报</a>、<a href="https://www.glaruntech.com/product.php?p=1&t1=7">国睿产品说明</a>。</p>']
for _,r in s.head(5).iterrows():
    body += [f'<article><h2>{r["name"]} {r.ts_code}</h2>',chart(d[d.ts_code==r.ts_code]),'<p>最近80个交易日前复权K线，金色MA20、蓝色MA60，下方原始成交量共享日期轴。</p></article>']
body += ['<h2>策略与数据边界</h2><p>当前动作是观察，不直接下单。突破需价格保持和成交量配合；已经偏离均线过远的股票等整理，不因军工标签降低门槛。已有趋势仓按原计划和实际可卖数量管理，收盘跌破MA20或原结构失效时重新评估减仓，T+1新买当天不可卖。缺少账户及公告核查，不给实际仓位。</p><p>本地9月9日数据已通过复权一致性闸门，上述9只最新OHLC另与通达信原始日线逐只核对。MA、RPS采用本地等比前复权，量比为当日量除以前20日均量（不含当日）；验证范围是本地运营一致性，不是独立外部证明。研究名单不构成完整行业宽度统计，也未验证策略胜率。</p><p><a href="a_share_analysis_20260909_execution_plan.html">同日全市场、风险背景及完整策略</a></p></main></html>']
(out/'a_share_analysis_20260909_defense_focus.html').write_text(head+''.join(body),encoding='utf8')
s.to_csv(out/'defense_focus_20260909.csv',index=False,encoding='utf-8-sig')
print('defense report: 9 stocks, 5 charts, 9 anchors passed')
