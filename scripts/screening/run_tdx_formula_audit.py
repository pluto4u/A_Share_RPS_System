"""Reproduce the user supplied ZZ1234 and audit its screenshot pool."""
from __future__ import annotations
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import html
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from run_right_side_research import ROOT, core, chart


def formula(d):
    d = core.add_indicators(d)
    g = d.groupby('ts_code', sort=False)
    for n in (5,10,120):
        d[f'ma{n}'] = g.close.transform(lambda s:s.rolling(n).mean())
    d['vm20'] = g.vol.transform(lambda s:s.rolling(20).mean())
    d['DIF'] = g.close.transform(lambda s:s.ewm(span=12,adjust=False).mean()-s.ewm(span=26,adjust=False).mean())
    d['DEA'] = d.groupby('ts_code').DIF.transform(lambda s:s.ewm(span=9,adjust=False).mean())
    d['MH'] = 2*(d.DIF-d.DEA)
    for n in (5,10,20):
        d[f'ph{n}'] = g.high.transform(lambda s:s.shift(1).rolling(n).max())
    d['peak60'] = g.high.transform(lambda s:s.rolling(60).max())
    d['low30'] = g.low.transform(lambda s:s.rolling(30).min())
    d['LOWAGE'] = g.low.transform(lambda s:s.rolling(30).apply(lambda v:np.argmin(v[::-1]),raw=True))
    d['PEAKAGE'] = g.high.transform(lambda s:s.rolling(60).apply(lambda v:np.argmax(v[::-1]),raw=True))
    d['DD_pct'] = (d.peak60-d.low30)/d.peak60*100
    d['DD'] = d.DD_pct >= 28
    d['LOWNEAR'] = d.LOWAGE <= 15
    d['BASE'] = (d.bars_count > 250) & ~d['name'].str.startswith(('ST','*ST'),na=False)
    d['YJ'] = d.DD & d.LOWNEAR & (d.close>d.ma20) & (d.ma5>d.ma10) & (d.ma5>d.groupby('ts_code').ma5.shift(2)) & (d.close>d.ph5) & (d.MH>0) & (d.MH>d.groupby('ts_code').MH.shift(1)) & (d.close/d.ma20<=1.12)
    d['QD'] = d.DD & d.LOWNEAR & (d.close>d.ma20) & (d.ma5>d.ma10) & (d.close>d.ma120) & (d.close>d.ph10) & (d.vol>=d.vm20*1.1) & (d.MH>0)
    d['QR'] = d.DD & (d.close>d.ma60) & (d.ma10>d.ma20) & (d.close>d.ph20) & (d.MH>0)
    d['QQR'] = d.QR & (d.vol>=d.vm20*1.8) & (d.DIF>0)
    d['XG'] = d.BASE & (d.YJ | d.QD | d.QR | d.QQR)
    d['volume_ratio_inclusive'] = d.vol/d.vm20
    d['order_ok'] = d.PEAKAGE>d.LOWAGE
    d['slope60'] = d.ma60/d.groupby('ts_code').ma60.shift(5)-1
    d['slope20'] = d.ma20/d.groupby('ts_code').ma20.shift(5)-1
    d['clean_trend'] = (d.close>d.ma20) & (d.ma20>d.ma60) & (d.slope60>0) & (d.slope20>0)
    d['atr_pct'] = d.atr20/d.close*100
    return d


def assess(r):
    if pd.isna(r.get('close')):
        return '未覆盖','北交所未纳入当前经验证的沪深复权链'
    flags=[]
    if not r['order_ok']: flags.append('窗口低点不晚于高点，DD未证明先跌后涨')
    if r.vs_ma20_pct>12: flags.append('距MA20超过12%')
    if r.volume_ratio_inclusive>3: flags.append('成交量超过当日含量20日均量3倍')
    if r.adv20<1e8: flags.append('前20日成交额中位数不足1亿元')
    if r.close<r.ma60: flags.append('仍在MA60下方')
    if r.slope60<=0: flags.append('MA60仍下行')
    if r.LOWAGE>15: flags.append('低点已超过15日，来自QR分支的较晚阶段')
    if not r.XG: return '原公式未命中','；'.join(flags) or '至少一项原始条件不满足，见逐项诊断'
    if r.vs_ma20_pct>12 or r.volume_ratio_inclusive>3: return '过热或爆量等待','；'.join(flags)
    if r.clean_trend and r.order_ok and r.adv20>=1e8: return '趋势与公式共振','；'.join(flags) or '均线结构与原公式同时成立；还需位置和后续成交确认'
    return '反弹修复观察','；'.join(flags) or '公式命中，但均线排列/斜率尚未全部成立'


def main():
    out=ROOT/'output'
    pool=json.loads((ROOT/'research_inputs/20260908_tdx_trend_screenshot.json').read_text(encoding='utf8'))
    date=pd.Timestamp(pool['local_evaluation_date'])
    gate=json.loads((out/'qfq_verification.json').read_text(encoding='utf-8-sig'))
    assert gate['price_adjustment']=='verified_qfq'
    assert gate['full_market_checks']['latest_anchor']['max_date']==str(date.date())
    daily,meta=core.load_hithink_qfq(core.DEFAULT_HITHINK_DB,date,400,min_bars=251)
    names=core.load_name_master(core.DEFAULT_HQ)
    daily['name']=daily.ts_code.map(names).fillna(daily['name']).fillna(daily.ts_code)
    d=formula(daily)
    s=d[d.trade_date==date].copy()
    rps=pd.read_csv(out/'right_side_20260907_all.csv',usecols=['ts_code','RPS_50','RPS_120','RPS_250','industry_name'])
    s=s.merge(rps,on='ts_code',how='left',validate='one_to_one')
    s['stages']=s.apply(lambda r:'/'.join(k for k in ['YJ','QD','QR','QQR'] if r[k]) or '无',axis=1)
    audit=pd.DataFrame(pool['securities'],columns=['ts_code','screenshot_name']).merge(s,on='ts_code',how='left',validate='one_to_one')
    audit['name']=audit['name'].fillna(audit.screenshot_name)
    audit['name_conflict']=audit.name!=audit.screenshot_name
    decisions=audit.apply(assess,axis=1)
    audit['assessment']=[p[0] for p in decisions]
    audit['risk_notes']=[p[1] for p in decisions]
    audit['trigger_ref']=audit.high
    audit['invalid_ref']=np.minimum(audit.low,audit.ma20)
    audit['no_chase_ref']=audit.high+0.35*audit.atr20
    order={'趋势与公式共振':0,'反弹修复观察':1,'过热或爆量等待':2,'原公式未命中':3,'未覆盖':4}
    audit['priority']=audit.assessment.map(order)
    audit=audit.sort_values(['priority','RPS_120'],ascending=[True,False])
    selected=s[s.XG]
    codes=set(audit.ts_code)
    dates=[]
    for dt, group in d[d.trade_date>=date-pd.Timedelta(days=35)].groupby('trade_date'):
        matches=group[group.XG]
        dates.append({'date':str(dt.date()),'all_hits':len(matches),'screenshot_hits':len(set(matches.ts_code)&codes)})
    summary={'generated_at':datetime.now().isoformat(),'as_of':str(date.date()),'formula':'ZZ1234 user original','screenshot_count':len(audit),'covered':int(audit.close.notna().sum()),'screenshot_hit':int(audit.XG.fillna(False).sum()),'full_market_hit':len(selected),'stage_counts_overlapping':{k:int(selected[k].sum()) for k in ['YJ','QD','QR','QQR']},'assessment_counts':audit.assessment.value_counts().to_dict(),'all_hit_clean_trend':int(selected.clean_trend.sum()),'all_hit_order_fail':int((~selected.order_ok).sum()),'all_hit_overheat':int((selected.vs_ma20_pct>12).sum()),'all_hit_low_adv':int((selected.adv20<1e8).sum()),'all_hit_below_ma60':int((selected.close<selected.ma60).sum()),'all_hit_old_low':int((selected.LOWAGE>15).sum()),'date_overlap':dates,'metadata':meta}
    prefix=out/'tdx_formula_audit_20260907'
    audit.to_csv(str(prefix)+'_screenshot.csv',index=False,encoding='utf-8-sig')
    selected.to_csv(str(prefix)+'_all_hits.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(dates).to_csv(str(prefix)+'_date_overlap.csv',index=False,encoding='utf-8-sig')
    (Path(str(prefix)+'.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    def table(df,cols,labels):
        return df[cols].rename(columns=dict(zip(cols,labels))).to_html(index=False,escape=True,na_rep='未覆盖',float_format=lambda v:f'{v:.2f}',border=0)
    body=[f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZZ1234公式检查与截图评估</title><style>body{{font:16px system-ui,"Microsoft YaHei";line-height:1.7;margin:0;color:#24342f;background:#f5f7f6}}main{{max-width:1350px;margin:auto;padding:24px}}header{{padding:24px;background:#1b443a;color:white}}h1{{font-size:27px}}h2{{font-size:22px}}article{{border-top:2px solid #bac9c1;padding:16px 0}}.table{{overflow:auto}}table{{border-collapse:collapse;background:white;font-size:13px;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;white-space:nowrap}}svg{{width:100%;height:auto;background:white}}@media(max-width:600px){{main,header{{padding:12px}}h1{{font-size:23px}}}}@media print{{article{{break-inside:avoid}}}}</style><header><h1>ZZ1234 通达信趋势公式检查</h1><p>截至{date:%Y-%m-%d}收盘；截图57只，沪深覆盖{summary["covered"]}只，其中{summary["screenshot_hit"]}只复现命中；盖世食品未覆盖。</p><p>结论：本公式偏向大跌后的右侧修复，不能把合并输出理解为全是趋势确认。当前动作：按阶段与风险重新分层，等待触发；账户与盘中数据未提供，不生成即时委托。</p></header><main>',
          f'<h2>原公式全市场复算</h2><p>沪深本地当日命中{len(selected)}只。重叠阶段计数：{summary["stage_counts_overlapping"]}。这些数字不能相加；QQR包含在QR中。原公式命中且满足收盘>MA20>MA60、两条均线向上的只有{summary["all_hit_clean_trend"]}只。</p>',
          '<h2>公式逻辑检查</h2><ol><li>QQR是QR的严格子集，XG中的OR QQR对名单没有增量，只能作标签。</li><li>DD使用两个不同窗口的最高/最低点，没有检查高点先于低点；窗口振幅≥28%不等于实际先跌28%再涨。</li><li>YJ要求低点近期及MA20上方，但不要求MA60上行。QD同样不要求MA60上行。QR不要求LOWNEAR，也不要求成交量增加或MA20乖离上限。</li><li>阶段不构成严格升级链：QD未要求MACD柱增强和MA5两日上升；QR也未包含YJ全部条件。应同时显示各分支，不把“QR”名称当成已经验证的趋势。</li><li>VM20包含当日成交量。原公式量比与上一份报告的“此前20日均量”不同；本页按含当日口径复现。</li><li>BASE排除ST并要求超过250根K线，但没有成交额下限、过热过滤、市场情绪或行业条件。</li></ol>',
          f'<p>实际量化：命中股中MA60下方{summary["all_hit_below_ma60"]}只；距MA20超过12%有{summary["all_hit_overheat"]}只；前20日成交额中位数不足1亿元{summary["all_hit_low_adv"]}只；低点超过15日{summary["all_hit_old_low"]}只；高低点顺序不符{summary["all_hit_order_fail"]}只。以上风险可重叠。</p>',
          '<h2>截图逐股检查</h2><p>先列趋势共振，再列修复与过热；该排序不是买入顺序。触发、失效均为日线观察参考。</p><div class="table">',table(audit,['ts_code','name','stages','XG','assessment','close','RPS_50','RPS_120','volume_ratio_inclusive','vs_ma20_pct','LOWAGE','risk_notes'],['代码','名称','原阶段','原公式命中','评估','收盘','RPS50','RPS120','原公式量比','距MA20%','距低点日数','风险']),'</div>',
          '<h2>重点图形复核</h2>']
    for _,r in audit[audit.close.notna()].head(10).iterrows():
        body.extend([f'<article><h2>{html.escape(r["name"])} {r.ts_code} · {r.assessment}</h2><p>{html.escape(r.risk_notes)}。原阶段{r.stages}，MA20 {r.ma20:.2f}、MA60 {r.ma60:.2f}。</p>',chart(d[d.ts_code==r.ts_code]),f'<p>金色MA20，蓝色MA60；下方原始成交量相对窗口最大值。越过当日高点参考{r.trigger_ref:.2f}元仍需保持及量能配合；超过{r.no_chase_ref:.2f}元不追。结构观察失效位{r.invalid_ref:.2f}元。该失效位为当日低点与MA20较低值，不代表保证可按此价成交。</p></article>'])
    body.extend(['<h2>日期与名称对账</h2><p>截图未显示交易日，用户指定最新交易日，本页使用9月7日。下面给出最近窗口的名单重合数；最高重合日期只是诊断线索，不能据此断言截图日期。若差异存在，应继续检查通达信复权方式、股票范围、停牌、历史长度与公式保存版本。</p><div class="table">',table(pd.DataFrame(dates),['date','all_hits','screenshot_hits'],['日期','当日沪深原公式命中','截图重合数']),'</div><p>截图名称与本地主表冲突：</p><div class="table">',table(audit[audit.name_conflict],['ts_code','screenshot_name','name'],['代码','截图名','本地主表名']),'</div>',
        '<h2>使用建议与证据边界</h2><p>保留原公式做发现池，分支标签单列；另设量价和趋势质量层，逐项标注MA60方向、MA20乖离、量比、流动性，不静默修改公式。龙头/情绪用于仓位参考而非只选龙头。9月7日情绪ST_cycle=72.8，连续确认未完成，保守上限参考10%；未完成样本外验证，不机械决定已有趋势仓退出。</p><p>新仓T+1当日不可卖；次日可卖时跌破失效位退出，涨跌停可能无法成交。未给账户权益不计算股数。没有交易可能错过修复延续，但不会因此把弱趋势视作强趋势。缺少公告复核与盘中承接数据，不把日线候选作为即时买单。</p><p>这是公式逻辑、当日复现与形态审计，不是历史收益回测。截图是事后选择的名单，不能用它估计公式全市场胜率；后续应全市场滚动信号、次日开盘入场、处理涨停不可买及T+1退出、去除重复信号，再作样本外验证。</p><p>数据：同花顺本地v_daily_qfq等比前复权，量额原始；通达信名称及行业表；复权门为本地运营一致性验证，非独立外部证明。400根预热日线，EMA初始值采用第一根收盘；严格边界仍可能受终端更长预热历史影响。BARSCOUNT>250在最新截面精确，历史日期对账窗口仅作诊断。当前名称回用于历史，历史ST状态未完整覆盖。RPS引用同日全沪深计算缓存，并在截图之外的全截面排名。</p><p>脚本：run_tdx_formula_audit.py；公式：research_inputs/ZZ1234_user_20260908.tdx；逐股数据及完整命中名单：output/tdx_formula_audit_20260907_*.csv。</p></main></html>'])
    report=out/'a_share_analysis_20260907_tdx_formula_audit.html'
    report.write_text(''.join(body),encoding='utf8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ['metadata','date_overlap']},ensure_ascii=False))


if __name__=='__main__':
    main()
